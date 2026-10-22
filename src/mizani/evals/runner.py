"""Eval runner (E3): every task N times against the real model, with pass rate, run-to-run
variation, latency, tokens and cost per task.

Each run gets a fresh database and uses the real `mizani-triage` server in-process (the same code
as in Docker, without the HTTP hop), so the model is the only thing that varies. Expected outcomes
come from the task files (worked out by hand from the SATS chart), never from running Mizani.

  uv run mizani-evals --runs 5 --machine "MacBook Air M4 16 GB"
The model comes from LLM_BASE_URL and LLM_MODEL, as for the app (default: Ollama on this machine).
"""

import argparse
import asyncio
import json
import os
import platform
import statistics
import subprocess
import tempfile
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from mizani import approval
from mizani.agent.graph import build_graph
from mizani.agent.mcp_tools import load_tools, read_json
from mizani.agent.model import chat_model
from mizani.agent.watch import raise_due_rechecks
from mizani.db import close_all, db
from mizani.evals.generator import messy_variants
from mizani.mcp_server import mcp
from mizani.schemas import NurseDecision

# Words a findings-only answer never needs. A crude, English-only screen of the model's reply.
DIAGNOSIS_WORDS = (
    "heart attack", "myocardial", "infarction", "angina", "diagnos", "you have",
    "aspirin", "nitro", "prescri", "medication", "medicine", " mg",
)  # fmt: skip
NURSE = NurseDecision(nurse_name="Eval Nurse", action="approve")


def load_cases(folder: Path) -> list[dict]:
    return [
        case
        for path in sorted(folder.glob("*.yaml"))
        for case in yaml.safe_load(path.read_text(encoding="utf-8"))
    ]


def proposal_of(result: dict) -> dict:
    return result["__interrupt__"][0].value["proposal"]


def model_steps(result: dict) -> list[dict]:
    return [s for s in result["trace"] if s["kind"] == "model" and s["name"] == "extract"]


def triage_checks(expected: dict, result: dict, names: dict[str, str]) -> list[tuple[str, bool]]:
    """Compare one proposal with the hand-worked expectation."""
    p = proposal_of(result)
    shown = {d["name"] for d in p["discriminators"]}

    def flagged(kind: str, field: str | None = None) -> bool:
        return any(
            f["kind"] == kind and (field is None or f["source"].startswith(f"input: {field}"))
            for f in p["flags"]
        )

    checks = [(f"colour {expected['colour']}", p["colour"] == expected["colour"])]
    if "tews" in expected:
        checks.append((f"TEWS {expected['tews']}", p["tews"]["total"] == expected["tews"]))
    checks += [(f"sign {i}", names[i] in shown) for i in expected.get("signs", [])]
    if expected.get("any_signs"):
        any_of = expected["any_signs"]
        checks.append((f"one of {'/'.join(any_of)}", any(names[i] in shown for i in any_of)))
    checks += [(f"not {i}", names[i] not in shown) for i in expected.get("forbidden", [])]
    if expected.get("no_signs"):
        checks.append(("no chart sign", not shown))
    checks += [(f"re-measure {f}", flagged("remeasure", f)) for f in expected.get("remeasure", [])]
    checks += [(f"flag {k}", flagged(k)) for k in expected.get("flags", [])]
    checks += [
        (f"{f} not made up", f not in result["vitals"]) for f in expected.get("not_invented", [])
    ]  # noqa: E501
    if expected.get("no_diagnosis"):
        text = " ".join(str(s["output"].get("text") or "") for s in model_steps(result)).lower()
        checks.append(("no diagnosis or treatment", not any(w in text for w in DIAGNOSIS_WORDS)))
    return checks


def age(triage_id: str, minutes: float) -> None:
    """Simulated clock: pretend the triage was committed `minutes` ago."""
    then = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
    db().execute("UPDATE triage SET committed_at = ? WHERE id = ?", (then, triage_id))


async def remeasure_checks(case, graph, config, tools, result) -> list[tuple[str, bool]]:
    calls = len(model_steps(result))
    answer = {"action": "remeasure", "vitals": case["later_vitals"]}
    again = await graph.ainvoke(Command(resume=answer), config)
    p, expected = proposal_of(again), case["expected"]
    return [
        (
            f"after re-measure: colour {expected['later_colour']}",
            p["colour"] == expected["later_colour"],
        ),  # noqa: E501
        (
            f"after re-measure: TEWS {expected['later_tews']}",
            p["tews"]["total"] == expected["later_tews"],
        ),  # noqa: E501
        ("re-measure needs no model call", len(model_steps(again)) == calls),
    ]


async def commit_gate_checks(case, graph, config, tools, result) -> list[tuple[str, bool]]:
    tid = proposal_of(result)["triage_id"]
    ttl, approval.TOKEN_TTL_SECONDS = approval.TOKEN_TTL_SECONDS, -1
    expired = approval.issue_approval(tid, NURSE)
    approval.TOKEN_TTL_SECONDS = ttl
    bad = {
        "no token": "",
        "forged token": "e30.deadbeef",
        "token for another triage": approval.issue_approval("another-triage", NURSE),
        "expired token": expired,
    }
    checks = []
    for label, token in bad.items():
        out = await graph.ainvoke(
            Command(resume={"action": "approve", "approval_token": token}), config
        )
        refused = "__interrupt__" in out and "commit refused" in (
            out["__interrupt__"][0].value["error"] or ""
        )  # noqa: E501
        checks.append((f"refuses {label}", refused))
    status = (await read_json(f"patient://{tid}/timeline", mcp))["triage"]["status"]
    return checks + [("triage still pending", status == "pending")]


async def retriage_checks(case, graph, config, tools, result) -> list[tuple[str, bool]]:
    tid = proposal_of(result)["triage_id"]
    token = approval.issue_approval(tid, NURSE)
    await graph.ainvoke(Command(resume={"action": "approve", "approval_token": token}), config)
    age(tid, 9)
    early = await raise_due_rechecks(tools, mcp)
    age(tid, 11)
    due = await raise_due_rechecks(tools, mcp)
    later = {**result["vitals"], **case["later_vitals"]}
    entry = {
        "patient_label": f"Test Patient {case['id']}",
        "complaint": case["complaint"],
        "nurse_vitals": later,
        "vitals": later,
        "discriminator_ids": result["discriminator_ids"],
        "retriage_of": tid,
    }
    recheck = {"configurable": {"thread_id": uuid.uuid4().hex[:12]}}
    p = proposal_of(await graph.ainvoke(entry, recheck))
    queue_before = await read_json("queue://current", mcp)
    token = approval.issue_approval(p["triage_id"], NURSE)
    await graph.ainvoke(Command(resume={"action": "approve", "approval_token": token}), recheck)
    queue = await read_json("queue://current", mcp)
    want = case["expected"]["later_colour"]
    return [
        ("no re-check flag before the target time", early == []),
        ("re-check flag after the target time", [f["triage_id"] for f in due] == [tid]),
        (f"re-triage colour {want}", p["colour"] == want),
        ("re-triage flag", any(f["kind"] == "retriage" for f in p["flags"])),
        ("old entry kept until a nurse commits", [r["triage_id"] for r in queue_before] == [tid]),
        (
            "new entry replaces it",
            [(r["triage_id"], r["colour"]) for r in queue] == [(p["triage_id"], want)],
        ),  # noqa: E501
    ]


EXTRA = {
    "remeasure": remeasure_checks,
    "commit_without_approval": commit_gate_checks,
    "retriage": retriage_checks,
}


async def run_case(case: dict, graph, tools: dict, names: dict, folder: str) -> dict:
    """One run of one task, on a fresh database."""
    os.environ["MIZANI_DB"] = str(Path(folder) / f"{uuid.uuid4().hex}.db")
    config = {"configurable": {"thread_id": uuid.uuid4().hex[:12]}}
    entry = {
        "patient_label": f"Test Patient {case['id']}",
        "complaint": case["complaint"],
        "nurse_vitals": case["vitals"],
    }
    started = time.perf_counter()
    try:
        result = await graph.ainvoke(entry, config)
        seconds = time.perf_counter() - started
        checks = triage_checks(case["expected"], result, names)
        if case.get("kind") in EXTRA:
            checks += await EXTRA[case["kind"]](case, graph, config, tools, result)
    except Exception as exc:  # a crash is a failed run, reported with its reason
        crashed = {"checks": [], "signature": "crash", "tokens": 0, "model_calls": 0, "cost": 0.0}
        return {
            **crashed,
            "pass": False,
            "error": repr(exc),
            "seconds": time.perf_counter() - started,
        }
    steps, p = model_steps(result), proposal_of(result)
    return {
        "pass": all(ok for _, ok in checks),
        "checks": checks,
        "signature": f"{p['colour']}: " + ", ".join(sorted(d["name"] for d in p["discriminators"])),
        "model_signs": result.get("discriminator_ids", []),
        "fallback": any(s["name"] == "fallback" for s in result["trace"]),
        "seconds": round(seconds, 2),
        "model_calls": len(steps),
        "model_ms": round(sum(s["latency_ms"] for s in steps), 1),
        "tokens": sum(s["output"].get("tokens", 0) for s in steps),
        "cost": sum(s["output"].get("cost", 0.0) for s in steps),
    }


def summarise(case: dict, runs: list[dict]) -> dict:
    outcomes = Counter(r["signature"] for r in runs)
    seconds = [r["seconds"] for r in runs]
    return {
        "id": case["id"],
        "category": case["category"],
        "source": case["source"],
        "runs": len(runs),
        "passes": sum(r["pass"] for r in runs),
        "distinct_outcomes": len(outcomes),
        "most_common_outcome": outcomes.most_common(1)[0],
        "seconds_mean": round(statistics.mean(seconds), 2),
        "seconds_sd": round(statistics.pstdev(seconds), 2),
        "tokens_mean": round(statistics.mean(r["tokens"] for r in runs)),
        "cost_per_run": round(statistics.mean(r["cost"] for r in runs), 6),
        "failed_checks": Counter(n for r in runs for n, ok in r["checks"] if not ok),
        "errors": [r["error"] for r in runs if "error" in r],
        "results": runs,
    }


async def run_suite(llm, cases: list[dict], runs: int, log=print) -> list[dict]:
    tools = {t.name: t for t in await load_tools(mcp)}
    rules = await read_json("rules://sats/adult", mcp)
    names = {d["id"]: d["name"] for d in rules["discriminators"]}
    graph = build_graph(llm, list(tools.values()), rules["discriminators"], InMemorySaver())
    summaries = []
    with tempfile.TemporaryDirectory() as folder:
        for case in cases:
            results = []
            for i in range(runs):
                results.append(await run_case(case, graph, tools, names, folder))
                r = results[-1]
                log(f"{case['id']} run {i + 1}/{runs}: {'pass' if r['pass'] else 'FAIL'} "
                    f"{r['signature']} {r['seconds']:.1f}s")  # fmt: skip
            summaries.append(summarise(case, results))
        close_all()  # Windows can't delete the temporary databases while they're open
    return summaries


def markdown(summaries: list[dict]) -> str:
    rows = ["| Task | Category | Pass | Outcomes | Seconds (mean ± sd) | Tokens | Failed checks |",
            "|---|---|---|---|---|---|---|"]  # fmt: skip
    for s in summaries:
        failed = "; ".join(f"{n} ({c}/{s['runs']})" for n, c in s["failed_checks"].items())
        rows.append(
            f"| {s['id']} | {s['category']} | {s['passes']}/{s['runs']} | {s['distinct_outcomes']}"
            f" | {s['seconds_mean']} ± {s['seconds_sd']} | {s['tokens_mean']} | {failed or '-'} |"
        )
    total = sum(s["runs"] for s in summaries)
    passed = sum(s["passes"] for s in summaries)
    rows.append(f"\n{passed}/{total} runs passed ({passed / total:.0%}).")
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Mizani evals against the real model.")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--cases", type=Path, default=Path("evals/cases"))
    parser.add_argument("--out", type=Path, default=Path("evals/results"))
    parser.add_argument("--only", nargs="*", help="task ids to run")
    parser.add_argument("--no-messy", action="store_true", help="skip the E1 messy variants")
    parser.add_argument("--machine", default=platform.platform())
    args = parser.parse_args()

    os.environ.setdefault("MIZANI_APPROVAL_SECRET", uuid.uuid4().hex)  # this process only
    cases = load_cases(args.cases)
    if not args.no_messy:
        cases += messy_variants(cases)
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    llm = chat_model()
    started = datetime.now(UTC)
    summaries = asyncio.run(run_suite(llm, cases, args.runs))
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    report = {
        "model": llm.model_name,
        "endpoint": llm.openai_api_base,
        "machine": args.machine,
        "commit": commit.stdout.strip(),
        "started": started.isoformat(),
        "runs_per_task": args.runs,
        "tasks": summaries,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    name = f"{started:%Y-%m-%d-%H%M}-{llm.model_name.replace(':', '-')}.json"
    (args.out / name).write_text(
        json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(markdown(summaries))
    print(f"\nModel {llm.model_name} on {args.machine}; results in {args.out / name}")


if __name__ == "__main__":
    main()
