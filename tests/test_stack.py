"""End-to-end check of the running compose stack, without a model:
propose -> nurse approval -> commit -> wso2 fhir-mcp-server -> HAPI FHIR, read back over REST.

    docker compose up -d mizani-mcp fhir-mcp hapi-fhir
    MIZANI_E2E=1 uv run pytest tests/test_stack.py
"""

import json
import os
import urllib.request

import pytest
from mcp.client import Client

from mizani.approval import issue_approval
from mizani.fhir import write_triage
from mizani.schemas import NurseDecision

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not os.environ.get("MIZANI_E2E"), reason="set MIZANI_E2E=1 with compose up"),
]
MIZANI = "http://127.0.0.1:8000/mcp"
FHIR_MCP = "http://127.0.0.1:8001/mcp/"  # trailing slash: wso2's server redirects /mcp
HAPI = "http://127.0.0.1:18080/fhir"
VITALS = dict(
    respiratory_rate=24,
    heart_rate=118,
    systolic_bp=95,
    temperature=38.9,
    spo2=93,
    avpu="alert",
    mobility="with_help",
    trauma=False,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def fhir_get(path):
    with urllib.request.urlopen(f"{HAPI}/{path}", timeout=30) as response:
        return json.load(response)


async def test_committed_triage_lands_in_hapi_through_wso2(monkeypatch):
    # the compose default; the MCP server in Docker must share it
    monkeypatch.setenv(
        "MIZANI_APPROVAL_SECRET",
        os.environ.get("MIZANI_APPROVAL_SECRET", "local-demo-secret-change-me"),
    )
    async with Client(MIZANI) as mizani:
        args = dict(
            patient_label="Test Patient E2E",
            complaint="chest pain",
            vitals=VITALS,
            discriminators=[{"id": "chest_pain", "quote": "chest pain"}],
        )
        proposal = (await mizani.call_tool("propose_priority", args)).structured_content
        token = issue_approval(
            proposal["triage_id"], NurseDecision(nurse_name="Nurse E2E", action="approve")
        )
        committed = await mizani.call_tool(
            "commit_triage", {"triage_id": proposal["triage_id"], "approval_token": token}
        )
        assert not committed.is_error
        timeline = await mizani.read_resource(f"patient://{proposal['triage_id']}/timeline")
        record = json.loads(timeline.contents[0].text)["triage"]

    async with Client(FHIR_MCP, mode="legacy") as fhir:  # wso2 runs MCP SDK 1.x
        ids = await write_triage(fhir, record)

    encounter = fhir_get(f"Encounter/{ids['Encounter']}")
    assert encounter["status"] == "triaged" and "Nurse E2E" in encounter["priority"]["text"]
    assert encounter["subject"]["reference"] == f"Patient/{ids['Patient']}"
    bundle = fhir_get(f"Observation?encounter=Encounter/{ids['Encounter']}&_count=50")
    codes = {c["code"] for e in bundle["entry"] for c in e["resource"]["code"]["coding"]}
    assert {"9279-1", "8867-4", "85354-9", "8310-5", "2708-6", "tews"} <= codes
