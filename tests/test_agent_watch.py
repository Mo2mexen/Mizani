from datetime import UTC, datetime, timedelta

import pytest

from mizani import approval, audit
from mizani.agent.mcp_tools import load_tools, read_json
from mizani.agent.watch import raise_due_rechecks
from mizani.db import db
from mizani.mcp_server import mcp
from mizani.schemas import NurseDecision

pytestmark = pytest.mark.anyio

NORMAL = dict(
    respiratory_rate=12,
    heart_rate=80,
    systolic_bp=120,
    temperature=37.0,
    avpu="alert",
    mobility="walking",
    trauma=False,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MIZANI_DB", str(tmp_path / "mizani.db"))
    monkeypatch.setenv("MIZANI_APPROVAL_SECRET", "test-secret")


def call(name, args):
    return {"name": name, "args": args, "id": "call-1", "type": "tool_call"}


async def committed(tools, signs):
    args = {
        "patient_label": "Test Patient 01",
        "complaint": "chest pain",
        "vitals": NORMAL,
        "discriminators": [{"id": s, "quote": "chest pain"} for s in signs],
    }
    tid = (await tools["propose_priority"].ainvoke(call("propose_priority", args))).artifact[
        "triage_id"
    ]
    token = approval.issue_approval(tid, NurseDecision(nurse_name="Nurse A", action="approve"))
    await tools["commit_triage"].ainvoke(
        call("commit_triage", {"triage_id": tid, "approval_token": token})
    )
    return tid


def age(triage_id, minutes):
    """Pretend the triage was committed `minutes` ago."""
    then = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
    db().execute("UPDATE triage SET committed_at = ? WHERE id = ?", (then, triage_id))


async def test_flags_only_patients_past_their_target_time_and_only_once():
    tools = {t.name: t for t in await load_tools(mcp)}
    orange = await committed(tools, ["chest_pain"])  # target 10 min
    green = await committed(tools, [])  # target 240 min
    age(orange, 11)
    age(green, 11)

    flags = await raise_due_rechecks(tools, mcp)
    assert [f["triage_id"] for f in flags] == [orange]
    assert flags[0]["kind"] == "recheck_due" and "target 10 min" in flags[0]["message"]
    assert await raise_due_rechecks(tools, mcp) == []  # already raised

    queue = {r["triage_id"]: r for r in await read_json("queue://current", mcp)}
    assert queue[orange]["recheck_raised"] and not queue[green]["recheck_raised"]
    assert queue[orange]["colour"] == "orange"  # the watch never changes a colour
    logged = [r for r in audit.entries(db(), orange) if r["tool"] == "raise_recheck_flag"]
    assert len(logged) == 1 and not logged[0]["error"]


async def test_nothing_is_flagged_before_the_target_time():
    tools = {t.name: t for t in await load_tools(mcp)}
    await committed(tools, ["chest_pain"])
    assert await raise_due_rechecks(tools, mcp) == []
