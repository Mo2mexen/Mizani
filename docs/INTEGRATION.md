# Connecting your own agent or system to Mizani

Other software can use Mizani in three ways: through its MCP server (the triage tools, for any agent), through its HTTP API (the whole agent, for another front end), and through FHIR (the saved records, for a hospital record system). This page shows each one with commands you can paste. Every example was run against the stack started by `docker compose up`, on synthetic patients.

## What is reachable after `docker compose up`

```
git clone https://github.com/Mo2mexen/Mizani.git
cd Mizani
docker compose up
```

| What | Address on this machine | Protocol | Details |
|---|---|---|---|
| Our MCP server, `mizani-triage` | `http://127.0.0.1:8000/mcp` | MCP over streamable HTTP | [systems/mcp-server.md](systems/mcp-server.md) |
| The API and the agent | `http://127.0.0.1:8090/api`, described at `http://127.0.0.1:8090/docs` | HTTP and server-sent events | [systems/api.md](systems/api.md) |
| The FHIR store (HAPI) | `http://127.0.0.1:18080/fhir` | FHIR R4 REST | [systems/fhir.md](systems/fhir.md) |
| wso2's FHIR MCP server | `http://127.0.0.1:8001/mcp/` (keep the trailing slash) | MCP over streamable HTTP | [systems/fhir.md](systems/fhir.md) |

To use only the triage tools, without the model, the page or FHIR, start one service: `docker compose up mizani-mcp`.

All four listen on `127.0.0.1`, so only programs on the same machine can reach them, and none of them asks for a login. See [Limits](#limits) before opening them to a network.

## Use the triage tools from your own agent (MCP)

The MCP server has 10 tools, 5 resources and 2 prompts. An agent that speaks MCP can score vitals, check chart signs and propose a priority with them, and every answer carries the rule it came from. The server does not need our agent or any language model.

By hand, in a browser: run `npx @modelcontextprotocol/inspector`, choose "Streamable HTTP" and enter `http://127.0.0.1:8000/mcp`.

In an MCP client: give it the same address. In Claude Code, for example:

```
claude mcp add --transport http mizani-triage http://127.0.0.1:8000/mcp
```

We tested the server with the MCP Python SDK client and with MCP Inspector. Other clients take the same address, but we have not tried them.

From your own code, with the MCP Python SDK 2.3 or newer (`pip install "mcp>=2.3"`; inside a clone of this repo, `uv run python your_script.py` already has it):

```python
import asyncio

from mcp.client import Client

URL = "http://127.0.0.1:8000/mcp"
VITALS = {
    "respiratory_rate": 24,
    "heart_rate": 118,
    "systolic_bp": 95,
    "temperature": 38.9,
    "avpu": "alert",
    "mobility": "with_help",
    "trauma": False,
}


async def main() -> None:
    async with Client(URL) as mizani:
        tools = (await mizani.list_tools()).tools
        print("tools:", ", ".join(tool.name for tool in tools))

        tews = (await mizani.call_tool("compute_tews", {"vitals": VITALS})).structured_content
        print("TEWS", tews["total"])
        for part in tews["components"]:
            print(f"  {part['field']} = {part['value']}: {part['points']} ({part['source']})")

        proposal = (
            await mizani.call_tool(
                "propose_priority",
                {
                    "patient_label": "Test Patient 01",
                    "complaint": "chest pain since this morning",
                    "vitals": VITALS,
                    "discriminators": [{"id": "chest_pain", "quote": "chest pain"}],
                },
            )
        ).structured_content
        print("proposal:", proposal["colour"], "-", proposal["source"])

        refused = await mizani.call_tool(
            "commit_triage", {"triage_id": proposal["triage_id"], "approval_token": "made-up"}
        )
        print("commit without a nurse:", refused.content[0].text)


asyncio.run(main())
```

What it printed on our machine:

```
tools: validate_vitals, compute_tews, match_discriminators, propose_priority, commit_triage, raise_recheck_flag, draft_handover, release_handover, record_called_in, record_nurse_assessment
TEWS 8
  respiratory_rate = 24: 2 (sats_adult · chart: Adult TEWS, RR row | 21-29 = 2)
  heart_rate = 118: 2 (sats_adult · chart: Adult TEWS, HR row | 111-129 = 2)
  systolic_bp = 95: 1 (sats_adult · chart: Adult TEWS, SBP row | 81-100 = 1)
  temperature = 38.9: 2 (sats_adult · chart: Adult TEWS, Temp row (°C) | >38.4 = 2)
  avpu = alert: 0 (sats_adult · chart: Adult TEWS, AVPU row | alert = 0)
  mobility = with_help: 1 (sats_adult · chart: Adult TEWS, Mobility row (manual p16: a wheelchair user with permanent paralysis counts as immobile) | with_help = 1)
  trauma = False: 0 (sats_adult · chart: Adult TEWS, Trauma row (manual p16: any injury in the past 48 hours) | false = 0)
proposal: red - TEWS 8 -> red (sats_adult · chart flowchart: Emergency, TEWS 7 or more); final colour is the higher of the TEWS colour and any discriminator colour
commit without a nurse: Error executing tool commit_triage: commit refused: no approval token: a named nurse must approve or override first
```

Your agent can call the checking tools and `propose_priority` as often as it likes; a proposal is always pending. It cannot save one. `commit_triage`, `release_handover` and `record_called_in` each need an approval token, and an agent has no way to make one. Every call, including the refused one above, is written to the audit log with its inputs, outputs and time.

The chart signs a client may name, with their ids and definitions, are in the resource `rules://sats/adult`. A sign counts only when its `quote` is in the complaint.

## The approval step in your own system

An approval token is proof that a named nurse decided on one triage. It is signed with `MIZANI_APPROVAL_SECRET`, which the MCP server and your approval service share and the agent never sees. The compose default is `local-demo-secret-change-me`; set your own in `.env`.

In Mizani the API issues the token when the nurse presses Approve. If you build your own front end on the MCP server, the service behind your Approve button does it. In Python, from a clone of this repo and with `MIZANI_APPROVAL_SECRET` set to the server's value:

```python
import asyncio

from mcp.client import Client

from mizani.approval import issue_approval
from mizani.schemas import NurseDecision

URL = "http://127.0.0.1:8000/mcp"


async def main() -> None:
    async with Client(URL) as mizani:
        proposal = (
            await mizani.call_tool(
                "propose_priority",
                {
                    "patient_label": "Test Patient 02",
                    "complaint": "chest pain since this morning",
                    "vitals": {"respiratory_rate": 18, "heart_rate": 90, "avpu": "alert"},
                    "discriminators": [{"id": "chest_pain", "quote": "chest pain"}],
                },
            )
        ).structured_content
        decision = NurseDecision(nurse_name="Nurse A", action="approve")
        token = issue_approval(proposal["triage_id"], decision)
        args = {"triage_id": proposal["triage_id"], "approval_token": token}
        print("commit:", (await mizani.call_tool("commit_triage", args)).structured_content)
        again = await mizani.call_tool("commit_triage", args)
        print("same token again:", again.content[0].text)


asyncio.run(main())
```

```
commit: {'triage_id': '752f361866fc', 'colour': 'orange', 'approver': 'Nurse A', 'action': 'approve', 'committed_at': '2026-11-13T23:47:32.445091+00:00'}
same token again: Error executing tool commit_triage: commit refused: this triage is already committed
```

A token works for one triage, one purpose (`commit`, `handover` or `called_in`), one use, and two minutes. In another language, build it the way `src/mizani/approval.py` does: the payload as JSON, encoded as URL-safe base64, then a dot, then the HMAC-SHA256 of that base64 text in hex, keyed with the secret. The rules are in [systems/approval.md](systems/approval.md).

## Send patients to Mizani's agent over HTTP

A registration system or another front end can use the whole agent through the API: it reads the complaint with the model, calls the tools, and waits for the nurse. Three calls take one patient from intake to a saved triage (Bash shown; the same calls work from `http://127.0.0.1:8090/docs`):

```
# 1. Intake: returns the run id
curl -s -X POST localhost:8090/api/intake -H 'content-type: application/json' \
  -d '{"patient_label":"Test Patient 01","complaint":"chest pain","vitals":{"respiratory_rate":18,"heart_rate":118,"avpu":"alert"}}'
# 2. The agent's steps as they happen, then the proposal with its triage_id. Ctrl+C after "proposal".
curl -N localhost:8090/api/triage/<run_id>/stream
# 3. The nurse's decision: the API signs the approval token and the agent commits
curl -s -X POST localhost:8090/api/triage/<run_id>/decision -H 'content-type: application/json' \
  -d '{"nurse_name":"Nurse A","action":"approve","triage_id":"<triage_id>"}'
```

The waiting list is `GET /api/queue` and the audit log is `GET /api/audit`. Every endpoint, with the re-check, handover and called-in calls, is in [systems/api.md](systems/api.md).

## Read the records from a hospital record system (FHIR)

After a nurse commits a triage, Mizani writes a Patient, an Encounter (status `triaged`, the colour and the approver as its priority) and one Observation per vital sign to the FHIR store, through wso2's FHIR MCP server. Any system that reads FHIR R4 can fetch them:

```
curl -s "http://127.0.0.1:18080/fhir/Encounter?_count=5"
curl -s "http://127.0.0.1:18080/fhir/Observation?encounter=Encounter/<id>"
```

The codes and the mapping are in [systems/fhir.md](systems/fhir.md).

To write to a hospital's own FHIR server, change `FHIR_SERVER_BASE_URL` on the `fhir-mcp` service in `docker-compose.yml` to that server's address. The compose file also turns wso2's authorisation off (`FHIR_SERVER_DISABLE_AUTHORIZATION`), which is only acceptable for the local store; a real server needs it on, set up as wso2's [README](https://github.com/wso2/fhir-mcp-server) describes. We have run Mizani against the local HAPI store only.

Mizani does not yet read an existing patient from a record system. wso2's server has a `search` tool for that, and the agent does not use it today.

## Change the model or the triage scale

The model is a setting: `LLM_MODEL` and `LLM_BASE_URL` in `.env` point the agent at any OpenAI-compatible server ([REMOTE_MODEL.md](REMOTE_MODEL.md)). A triage scale is a YAML rule pack in `src/mizani/rules/`, where every rule records its source ([systems/rules-engine.md](systems/rules-engine.md)). The server loads the adult SATS pack; choosing another pack by a setting is not built yet.

## Limits

- Local only. Every port is bound to `127.0.0.1`. Reaching Mizani from another machine means changing the port lines in `docker-compose.yml`, and nothing behind those ports checks who is calling, so do that only on a network you trust.
- The default approval secret is public. Anyone who knows the secret and can reach the MCP server can sign an approval, so set your own `MIZANI_APPROVAL_SECRET` on any shared machine.
- The examples create test patients. They stay in the waiting list and the audit log until `docker compose down -v`, which deletes the stored data (and the downloaded model).
- Synthetic data only. Mizani is a prototype that has not been connected to a real hospital system or validated in a clinic.
