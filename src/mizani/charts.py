"""The triage chart in use, and the charts the chief doctor uploads.

A chart is a YAML rule pack. The one in use is the chief doctor's last activated chart, or, until
one is activated, the built-in pack that `MIZANI_RULE_PACK` names. An uploaded chart is checked at
once (`parse_pack`), then evaluated in a separate process (evals/chart.py), and can only be
activated with a chief doctor's approval token through the MCP server once every safety check of
its evaluation held. The chart it replaced is kept, so going back is one click. Each triage
records the chart that scored it, and proposals show it.
"""

import json
import sqlite3
from datetime import UTC, datetime

import yaml
from pydantic import ValidationError

from mizani import audit
from mizani.db import db_path
from mizani.rules.pack import RulePack, load_pack, parse_pack

BUILT_IN = "built-in"
MAX_CASES = 60  # test patients per upload; each runs 5 times on the desk's model

_cache: dict = {}
override: RulePack | None = None  # the chart under evaluation, in the evaluation's own process


class ChartError(Exception):
    """A chart or its test patients were refused, with a message for the chief doctor."""


def now() -> str:
    return datetime.now(UTC).isoformat()


def built_in() -> RulePack:
    if "built_in" not in _cache:
        _cache["built_in"] = load_pack()
    return _cache["built_in"]


def label(pack_id: str, version: int | None) -> str:
    """How a chart is named on a proposal and a record: "sats_adult, version 2" or "built in"."""
    return f"{pack_id}, version {version}" if version else f"{pack_id}, built in"


def active(con: sqlite3.Connection) -> tuple[RulePack, str]:
    """The chart in use and its label. Read each call, parsed only after a change."""
    if override is not None:
        return override, label(override.id, None) + " (under evaluation)"
    row = con.execute(
        "SELECT id, pack_id, version, activated_at FROM chart WHERE status = 'active'"
    ).fetchone()
    key = (str(db_path()), row["id"] if row else None, row["activated_at"] if row else None)
    if _cache.get("key") != key:
        if row is None:
            pack = built_in()
            _cache["active"] = (pack, label(pack.id, None))
        else:
            text = con.execute("SELECT yaml FROM chart WHERE id = ?", (row["id"],)).fetchone()[0]
            _cache["active"] = (parse_pack(text), label(row["pack_id"], row["version"]))
        _cache["key"] = key
    return _cache["active"]


def parse_cases(text: str, pack: RulePack) -> list[dict]:
    """Test patients in the format of evals/cases/core.yaml, checked against this chart: each has
    an id, a complaint, vitals and an expected colour of the chart; expected signs are its own."""
    try:
        cases = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ChartError(f"the test patients file is not valid YAML: {exc}") from None
    if not isinstance(cases, list) or not cases:
        raise ChartError("the test patients file must be a YAML list of patients")
    if len(cases) > MAX_CASES:
        raise ChartError(f"at most {MAX_CASES} test patients (each runs 5 times on the model)")
    colours = {c.colour for c in pack.colours}
    signs = {d.id for d in pack.discriminators}
    seen = set()
    for i, case in enumerate(cases, 1):
        where = f"test patient {i}"
        if not isinstance(case, dict):
            raise ChartError(f"{where} is not a mapping")
        for field in ("id", "complaint", "vitals", "expected"):
            if field not in case:
                raise ChartError(f"{where} has no {field}")
        if case["id"] in seen:
            raise ChartError(f"two test patients are called {case['id']}")
        seen.add(case["id"])
        expected = case["expected"]
        if not isinstance(expected, dict) or expected.get("colour") not in colours:
            raise ChartError(f"{case['id']}: expected.colour must be one of {sorted(colours)}")
        keys = ("signs", "forbidden", "any_signs")
        named = [sign for key in keys for sign in expected.get(key, [])]
        unknown = sorted(set(named) - signs)
        if unknown:
            raise ChartError(f"{case['id']}: signs not on this chart: {unknown}")
        case.setdefault("category", "chart test patient")
        case.setdefault("source", "the chief doctor's test patients")
    return cases


def upload(con: sqlite3.Connection, actor: str, chart_text: str, cases_text: str | None) -> dict:
    """Check and store an uploaded chart (and its test patients). It scores nobody until it has
    been evaluated and a chief doctor activates it."""
    try:
        pack = parse_pack(chart_text)
    except ValidationError as exc:  # the chart's shape or rules; each message names its place
        problems = [
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}".removeprefix(": ")
            for e in exc.errors()[:5]
        ]
        reasons = "; ".join(p.replace("Value error, ", "") for p in problems)
        raise ChartError(f"The chart was refused: {reasons}") from None
    except ValueError as exc:
        raise ChartError(str(exc)) from None
    if cases_text and cases_text.strip():
        parse_cases(cases_text, pack)
    else:
        cases_text = None
    version = con.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM chart WHERE pack_id = ?", (pack.id,)
    ).fetchone()[0]
    cur = con.execute(
        "INSERT INTO chart (pack_id, version, name, yaml, cases, status, uploaded_by,"
        " uploaded_at) VALUES (?, ?, ?, ?, ?, 'uploaded', ?, ?)",
        (pack.id, version, pack.name, chart_text, cases_text, actor, now()),
    )
    audit.event(
        con,
        "chart_uploaded",
        actor,
        {"chart": label(pack.id, version), "name": pack.name, "test_patients": bool(cases_text)},
        {"chart_signs": len(pack.discriminators), "colours": [c.colour for c in pack.colours]},
    )
    return get(con, cur.lastrowid)


def _row(row: sqlite3.Row, full: bool = False) -> dict:
    chart = {
        k: row[k]
        for k in ("id", "pack_id", "version", "name", "status", "uploaded_by", "uploaded_at",
                  "progress", "activated_by", "activated_at", "previous")
    }  # fmt: skip
    chart["label"] = label(row["pack_id"], row["version"])
    chart["test_patients"] = len(yaml.safe_load(row["cases"])) if row["cases"] else 0
    report = json.loads(row["report"]) if row["report"] else None
    chart["safety_ok"] = bool(report and report.get("safety_ok"))
    if full:
        chart["report"] = report
    return chart


def get(con: sqlite3.Connection, chart_id: int, full: bool = True) -> dict | None:
    row = con.execute("SELECT * FROM chart WHERE id = ?", (chart_id,)).fetchone()
    return _row(row, full) if row else None


def overview(con: sqlite3.Connection) -> dict:
    """The Charts page: the chart in use, the one before it, and every upload, newest first."""
    pack, in_use = active(con)
    row = con.execute("SELECT * FROM chart WHERE status = 'active'").fetchone()
    previous = row["previous"] if row else None
    return {
        "active": {"label": in_use, "name": pack.name, "chart": _row(row) if row else None},
        "previous": previous,
        "previous_label": _previous_label(con, previous),
        "built_in": label(built_in().id, None),
        "charts": [_row(r) for r in con.execute("SELECT * FROM chart ORDER BY id DESC")],
    }


def _previous_label(con: sqlite3.Connection, previous: str | None) -> str | None:
    if previous is None:
        return None
    if previous == BUILT_IN:
        return label(built_in().id, None)
    chart = get(con, int(previous), full=False)
    return chart["label"] if chart else None


def target(con: sqlite3.Connection, ref: str) -> tuple[RulePack, dict | None]:
    """The chart a chief doctor wants in use: an uploaded chart's id, or "built-in"."""
    if ref == BUILT_IN:
        return built_in(), None
    if not ref.isdigit() or (chart := get(con, int(ref))) is None:
        raise ChartError(f"no chart {ref}")
    text = con.execute("SELECT yaml FROM chart WHERE id = ?", (int(ref),)).fetchone()[0]
    return parse_pack(text), chart


def activate(con: sqlite3.Connection, ref: str, approver: str, waiting: set[str]) -> dict:
    """Put a chart in use (called by the MCP tool, after the chief doctor's token was checked).
    Refused for an uploaded chart whose evaluation did not finish with every safety check held,
    and for a chart without the colour of a patient still waiting. The chart in use before is
    kept, so going back is one more activation."""
    pack, chart = target(con, ref)
    in_use = con.execute("SELECT 1 FROM chart WHERE status = 'active'").fetchone()
    if (chart is not None and chart["status"] == "active") or (chart is None and not in_use):
        raise ChartError(f"{label(pack.id, chart['version'] if chart else None)} is already in use")
    if chart is not None and not chart["safety_ok"]:
        raise ChartError(
            f"{chart['label']} cannot be activated: not every safety check of its evaluation held"
        )
    missing = waiting - {c.colour for c in pack.colours}
    if missing:
        raise ChartError(
            f"patients still waiting are {sorted(missing)}, a colour this chart does not have; "
            "call them in under the chart in use first"
        )
    con.execute("BEGIN IMMEDIATE")
    try:
        before = con.execute("SELECT id FROM chart WHERE status = 'active'").fetchone()
        previous = str(before["id"]) if before else BUILT_IN
        con.execute("UPDATE chart SET status = 'retired' WHERE status = 'active'")
        if chart is not None:
            con.execute(
                "UPDATE chart SET status = 'active', activated_by = ?, activated_at = ?,"
                " previous = ? WHERE id = ?",
                (approver, now(), previous, chart["id"]),
            )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    in_use = label(pack.id, chart["version"] if chart else None)
    return {"in_use": in_use, "previous": previous, "approver": approver}


def set_progress(con: sqlite3.Connection, chart_id: int, status: str, progress: str | None,
                 report: dict | None = None) -> None:  # fmt: skip
    """The evaluation's state: evaluating with progress, then evaluated or failed with a report."""
    con.execute(
        "UPDATE chart SET status = ?, progress = ?, report = COALESCE(?, report) WHERE id = ?",
        (status, progress, json.dumps(report) if report is not None else None, chart_id),
    )


def ended(con: sqlite3.Connection, chart_id: int, exit_code: int | None) -> None:
    """The evaluation's process ended. If it did not record a report, say it stopped."""
    con.execute(
        "UPDATE chart SET status = 'failed', progress = ? WHERE id = ? AND status = 'evaluating'",
        (f"stopped before the end (exit code {exit_code}); run the evaluation again", chart_id),
    )


def interrupted(con: sqlite3.Connection) -> None:
    """At start: an evaluation that was running when the server stopped did not finish."""
    con.execute(
        "UPDATE chart SET status = 'failed', progress = 'stopped: the server restarted;"
        " run the evaluation again' WHERE status = 'evaluating'"
    )
