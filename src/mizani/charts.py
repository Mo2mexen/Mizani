"""The triage chart in use, and the charts the chief doctor uploads.

A chart is a YAML rule pack. The one in use is the chief doctor's last activated chart, or, until
one is activated, the built-in pack that `MIZANI_RULE_PACK` names. An uploaded chart is checked at
once (`parse_pack`), then evaluated in a separate process (evals/chart.py), and can only be
activated with a chief doctor's approval token through the MCP server once every safety check of
its evaluation held. The chart it replaced is kept, so going back is one click. Each triage
records the chart that scored it, and proposals show it.
"""

import json
import logging
import sqlite3
from datetime import UTC, datetime

import yaml
from pydantic import ValidationError

from mizani import audit, store
from mizani.approval import ApprovalError
from mizani.db import db_path
from mizani.rules.pack import RulePack, load_pack, parse_pack

BUILT_IN = "built-in"
MAX_CASES = 60  # test patients per upload; each runs 5 times on the desk's model

log = logging.getLogger(__name__)
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
    """The chart in use and its label. Read each call, parsed only after a change.

    An activated chart that this version of Mizani can no longer load (a check added by an
    update) is taken out of use, with the reason on the Charts page and in the audit log, and the
    built-in chart scores from then on: the desk keeps working, and each proposal names it."""
    if override is not None:
        return override, label(override.id, None) + " (under evaluation)"
    row = con.execute(
        "SELECT id, pack_id, version, activated_at FROM chart WHERE status = 'active'"
    ).fetchone()
    key = (str(db_path()), row["id"] if row else None, row["activated_at"] if row else None)
    cached = _cache.get("active")  # (key, pack, label): one value, so threads never mix them
    if cached is None or cached[0] != key:
        if row is None:
            pack = built_in()
            cached = (key, pack, label(pack.id, None))
        else:
            text = con.execute("SELECT yaml FROM chart WHERE id = ?", (row["id"],)).fetchone()[0]
            try:
                cached = (key, parse_pack(text), label(row["pack_id"], row["version"]))
            except ValueError as exc:
                if con.in_transaction:  # inside a commit: write nothing; the next call marks it
                    pack = built_in()
                    return pack, label(pack.id, None)
                _unloadable(con, row, exc)
                return active(con)
        _cache["active"] = cached
    return cached[1], cached[2]


def _why(exc: ValueError) -> str:
    """What is wrong with a chart, in a line: each of pydantic's messages with its place."""
    if not isinstance(exc, ValidationError):
        return str(exc).splitlines()[0]
    problems = [
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}".removeprefix(": ")
        for e in exc.errors()[:5]
    ]
    return "; ".join(p.replace("Value error, ", "") for p in problems)


def _unloadable(con: sqlite3.Connection, row: sqlite3.Row, exc: ValueError) -> None:
    """Take a chart out of use that no longer loads; the built-in chart is in use after it."""
    chart = label(row["pack_id"], row["version"])
    why = _why(exc)
    log.error("%s no longer loads (%s); the built-in chart is in use", chart, why)
    con.execute(
        "UPDATE chart SET status = 'failed', progress = ? WHERE id = ? AND status = 'active'",
        (f"taken out of use: this version of Mizani cannot load it ({why}); the built-in chart"
         " is in use. Fix the chart and upload it again.", row["id"]),
    )  # fmt: skip
    audit.event(con, "chart_unloadable", "Mizani", {"chart": chart}, {"reason": why}, error=True)


def scale(con: sqlite3.Connection, chart_label: str | None) -> tuple[str, str] | None:
    """The id and name of the chart a triage record names ("sats_kenya, version 2"), for the
    FHIR record of that triage; None when no such chart is known."""
    if not chart_label:
        return None
    pack_id, _, which = chart_label.partition(", ")
    if which == "built in":
        pack = built_in()
        return (pack.id, pack.name) if pack.id == pack_id else None
    version = which.removeprefix("version ")
    if not version.isdigit():
        return None
    row = con.execute(
        "SELECT name FROM chart WHERE pack_id = ? AND version = ?", (pack_id, int(version))
    ).fetchone()
    return (pack_id, row["name"]) if row else None


def parse_cases(text: str, pack: RulePack) -> list[dict]:
    """Test patients in the format of evals/cases/core.yaml, checked against this chart: each has
    an id, a complaint, vitals and an expected colour of the chart; expected signs are its own."""
    try:
        cases = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:  # RecursionError: nesting thousands deep
        raise ChartError(f"the test patients file is not valid YAML: {exc}") from None
    if not isinstance(cases, list) or not cases:
        raise ChartError("the test patients file must be a YAML list of patients")
    if len(cases) > MAX_CASES:
        raise ChartError(f"at most {MAX_CASES} test patients (each runs 5 times on the model)")
    colours = {c.colour for c in pack.colours}
    signs = {d.id for d in pack.discriminators}
    seen = set()
    try:
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
    except (TypeError, AttributeError) as exc:  # e.g. a list where a name belongs
        raise ChartError(f"{where} could not be read ({exc})") from None
    return cases


def upload(con: sqlite3.Connection, actor: str, chart_text: str, cases_text: str | None) -> dict:
    """Check and store an uploaded chart (and its test patients). It scores nobody until it has
    been evaluated and a chief doctor activates it."""
    try:
        pack = parse_pack(chart_text)
    except ValidationError as exc:  # the chart's shape or rules; each message names its place
        raise ChartError(f"The chart was refused: {_why(exc)}") from None
    except ValueError as exc:
        raise ChartError(str(exc)) from None
    cases = parse_cases(cases_text, pack) if cases_text and cases_text.strip() else []
    cases_text = cases_text if cases else None
    version = con.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM chart WHERE pack_id = ?", (pack.id,)
    ).fetchone()[0]
    cur = con.execute(
        "INSERT INTO chart (pack_id, version, name, yaml, cases, test_patients, status,"
        " uploaded_by, uploaded_at) VALUES (?, ?, ?, ?, ?, ?, 'uploaded', ?, ?)",
        (pack.id, version, pack.name, chart_text, cases_text, len(cases), actor, now()),
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
    count = row["test_patients"]  # counted at upload; a chart from before: count them now
    if count is None:
        count = len(yaml.safe_load(row["cases"])) if row["cases"] else 0
    chart["test_patients"] = int(count)
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
    try:
        return parse_pack(text), chart
    except ValueError as exc:  # stored before a check this version adds
        raise ChartError(
            f"{chart['label']} cannot be activated: this version of Mizani cannot load it "
            f"({_why(exc)}); fix it and upload it again"
        ) from None


def activate(con: sqlite3.Connection, ref: str, approver: str, nonce: str) -> dict:
    """Put a chart in use (called by the MCP tool, after the chief doctor's token was checked).
    Refused for an uploaded chart whose evaluation did not finish with every safety check held,
    and for a chart without the colour of a patient still waiting. The checks, the token's
    single use (`nonce`) and the change are one transaction, so nothing can change between them
    and a refused activation leaves the token unused. The chart in use before is kept, so going
    back is one more activation."""
    con.execute("BEGIN IMMEDIATE")
    try:
        pack, chart = target(con, ref)
        in_use = con.execute("SELECT 1 FROM chart WHERE status = 'active'").fetchone()
        if (chart is not None and chart["status"] == "active") or (chart is None and not in_use):
            version = chart["version"] if chart else None
            raise ChartError(f"{label(pack.id, version)} is already in use")
        if chart is not None and chart["status"] not in ("evaluated", "retired"):
            why = {
                "uploaded": "it has not been evaluated yet",
                "evaluating": "its evaluation is still running",
                "failed": chart["progress"] or "its evaluation stopped before the end",
            }[chart["status"]]
            raise ChartError(f"{chart['label']} cannot be activated: {why}")
        if chart is not None and not chart["safety_ok"]:
            raise ChartError(
                f"{chart['label']} cannot be activated: not every safety check of its evaluation"
                " held"
            )
        missing = store.waiting_colours(con) - {c.colour for c in pack.colours}
        if missing:
            raise ChartError(
                f"patients still waiting are {sorted(missing)}, a colour this chart does not "
                "have; call them in under the chart in use first"
            )
        try:
            con.execute("INSERT INTO used_token (nonce) VALUES (?)", (nonce,))
        except sqlite3.IntegrityError:
            raise ApprovalError("this approval token was already used") from None
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


def start(con: sqlite3.Connection, chart_id: int) -> bool:
    """Mark the chart as evaluating, with its old report gone, unless another evaluation runs or
    the chart was in use. One statement, so two clicks cannot start two."""
    cur = con.execute(
        "UPDATE chart SET status = 'evaluating', progress = 'starting', report = NULL"
        " WHERE id = ? AND status IN ('uploaded', 'evaluated', 'failed')"
        " AND NOT EXISTS (SELECT 1 FROM chart WHERE status = 'evaluating')",
        (chart_id,),
    )
    return cur.rowcount == 1


def set_progress(con: sqlite3.Connection, chart_id: int, status: str, progress: str | None,
                 report: dict | None = None) -> None:  # fmt: skip
    """The evaluation's state: evaluating with progress, then evaluated or failed with a report."""
    con.execute(  # only while it is evaluating: never over a chart that went into use
        "UPDATE chart SET status = ?, progress = ?, report = COALESCE(?, report)"
        " WHERE id = ? AND status = 'evaluating'",
        (status, progress, json.dumps(report) if report is not None else None, chart_id),
    )


def ended(con: sqlite3.Connection, chart_id: int, exit_code: int | None) -> None:
    """The evaluation's process ended (`exit_code` None: it never started). If it did not record
    a report, say it stopped."""
    why = (
        "could not start"
        if exit_code is None
        else f"stopped before the end (exit code {exit_code})"
    )
    con.execute(
        "UPDATE chart SET status = 'failed', progress = ? WHERE id = ? AND status = 'evaluating'",
        (f"{why}; run the evaluation again", chart_id),
    )


def interrupted(con: sqlite3.Connection) -> None:
    """At start: an evaluation that was running when the server stopped did not finish."""
    con.execute(
        "UPDATE chart SET status = 'failed', progress = 'stopped: the server restarted;"
        " run the evaluation again' WHERE status = 'evaluating'"
    )
