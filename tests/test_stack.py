"""End-to-end check of the running compose stack, without a model:
propose -> nurse approval -> commit -> wso2 fhir-mcp-server -> HAPI FHIR, read back over REST.

    docker compose up -d mizani-mcp fhir-mcp hapi-fhir
    MIZANI_E2E=1 uv run pytest tests/test_stack.py

"""

import json
import os
import subprocess
import urllib.request
from pathlib import Path

import pytest
from mcp.client import Client

from mizani.approval import issue_approval
from mizani.fhir import write_triage
from mizani.schemas import Account

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


def in_the_mcp_container(code: str) -> str:
    """Run Python inside the running mizani-mcp container, against its own database."""
    command = ["docker", "compose", "exec", "-T", "mizani-mcp", "python", "-c", code]
    root = Path(__file__).parent.parent
    return subprocess.run(command, cwd=root, capture_output=True, text=True, check=True).stdout


def live_secret() -> str:
    """The approval secret the MCP server uses: the one `.env` gives it, or the install's own."""
    code = (
        "import os; from mizani.approval import ensure_secret; ensure_secret(); "
        "print(os.environ['MIZANI_APPROVAL_SECRET'])"
    )
    return in_the_mcp_container(code).strip()


def live_nurse() -> Account:
    """The test's nurse in the stack's database: an account no one can sign in to."""
    code = (
        "from mizani import auth; from mizani.db import db; c = db(); "
        "a = auth.find(c, 'e2e-nurse') or auth.add_account("
        "c, 'e2e-nurse', 'Nurse E2E', 'nurse', created_by='tests/test_stack.py'); "
        "print(a.model_dump_json())"
    )
    return Account.model_validate_json(in_the_mcp_container(code))


async def test_committed_triage_lands_in_hapi_through_wso2(monkeypatch):
    monkeypatch.setenv("MIZANI_APPROVAL_SECRET", live_secret())
    nurse = live_nurse()
    async with Client(MIZANI) as mizani:
        args = dict(
            patient_label="Test Patient E2E",
            complaint="chest pain",
            vitals=VITALS,
            discriminators=[{"id": "chest_pain", "quote": "chest pain"}],
        )
        proposal = (await mizani.call_tool("propose_priority", args)).structured_content
        token = issue_approval(proposal["triage_id"], nurse)
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
