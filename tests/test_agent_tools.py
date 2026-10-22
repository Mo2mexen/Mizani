import json

import pytest
from langchain_core.messages import ToolMessage
from mcp.client import Client

from mizani import audit
from mizani.agent.mcp_tools import load_tools, with_meta
from mizani.db import db
from mizani.mcp_server import mcp

pytestmark = pytest.mark.anyio

VITALS = dict(respiratory_rate=24, heart_rate=118, systolic_bp=135, temperature=37.8, spo2=94)
META = {"model": "qwen3.5:4b-q4_K_M", "tokens": 882, "cost": 0.0}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MIZANI_DB", str(tmp_path / "mizani.db"))
    monkeypatch.setenv("MIZANI_APPROVAL_SECRET", "test-secret")


async def tools():
    return {t.name: t for t in await load_tools(mcp)}


def tool_call(name, args):
    return {"name": name, "args": args, "id": "call-1", "type": "tool_call"}


async def test_loads_every_server_tool_with_its_schema():
    loaded = await tools()
    async with Client(mcp) as client:
        listed = {t.name for t in (await client.list_tools()).tools}
    assert set(loaded) == listed
    assert {"validate_vitals", "propose_priority", "commit_triage"} <= listed
    assert "vitals" in loaded["compute_tews"].args
    assert loaded["compute_tews"].metadata["read_only"] is True
    assert loaded["commit_triage"].metadata["read_only"] is False


async def test_call_returns_the_structured_result_and_logs_meta():
    message = await (await tools())["compute_tews"].ainvoke(
        tool_call("compute_tews", {"vitals": VITALS}), with_meta(META)
    )
    assert isinstance(message, ToolMessage) and message.status == "success"
    assert message.artifact["total"] == sum(c["points"] for c in message.artifact["components"])
    row = audit.entries(db())[-1]
    assert (row["tool"], row["model"], row["tokens"], row["cost"]) == (
        "compute_tews",
        META["model"],
        META["tokens"],
        META["cost"],
    )


async def test_vitals_sent_as_a_json_string_still_work():
    # qwen3.5:4b sends nested arguments as a JSON string (NOTES §6b); the server parses them.
    message = await (await tools())["validate_vitals"].ainvoke(
        tool_call("validate_vitals", {"vitals": json.dumps(VITALS)})
    )
    assert message.status == "success"
    assert {f["source"] for f in message.artifact} == {
        "input: avpu",
        "input: mobility",
        "input: trauma",
    }


async def test_refused_commit_comes_back_as_an_error_the_agent_can_read():
    loaded = await tools()
    proposal = await loaded["propose_priority"].ainvoke(
        tool_call(
            "propose_priority",
            {
                "patient_label": "Test Patient 01",
                "complaint": "chest pain since morning",
                "vitals": VITALS,
                "discriminator_ids": ["chest_pain"],
            },
        )
    )
    message = await loaded["commit_triage"].ainvoke(
        tool_call(
            "commit_triage", {"triage_id": proposal.artifact["triage_id"], "approval_token": ""}
        ),
        with_meta(META),
    )
    assert message.status == "error" and "commit refused" in message.content
    assert audit.entries(db())[-1]["model"] == META["model"]
