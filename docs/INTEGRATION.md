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

All four listen on `127.0.0.1`, so only programs on the same machine can reach them. The API needs a signed-in account for everything but its health check; the MCP server needs no login, but nothing it saves goes through without an approval token from a signed-in account (below). See [Limits](#limits) before opening them to a network.

## Use the triage tools from your own agent (MCP)

The MCP server has 10 tools, 6 resources and 2 prompts. An agent that speaks MCP can score vitals, check chart signs and propose a priority with them, and every answer carries the rule it came from. The server does not need our agent or any language model.

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
commit without a nurse: Error executing tool commit_triage: commit refused: no approval token: a signed-in nurse must approve or override first
```

Your agent can call the checking tools and `propose_priority` as often as it likes; a proposal is always pending. It cannot save one. `commit_triage`, `release_handover` and `record_called_in` each need an approval token, and an agent has no way to make one. Every call, including the refused one above, is written to the audit log with its inputs, outputs and time.

The chart signs a client may name, with their ids and definitions, are in the resource `rules://sats/adult`. A sign counts only when its `quote` is in the complaint.

## The approval step in your own system

An approval token is proof that a signed-in person decided on one triage. It is signed with the approval secret, which the MCP server and your approval service share and the agent never sees: each install makes its own on the first start, in the data folder beside the database (`/data/approval.secret` in Docker), unless `.env` sets `MIZANI_APPROVAL_SECRET`. The token names an account in Mizani's database, and the MCP server checks that account itself: it must be active, and its role must be allowed to do that action (a nurse commits and releases handovers; a nurse or a doctor records called in).

In Mizani the API issues the token for the signed-in nurse when they press Approve. If you build your own front end on the MCP server, the service behind your Approve button does it, for the nurse's Mizani account (an admin adds it under Users). In Python, run next to the MCP server so it sees the same data folder: outside Docker from a clone of this repo, or inside the container with `docker compose exec -T mizani-mcp python - < approve.py`:

```python
import asyncio

from mcp.client import Client

from mizani import auth
from mizani.approval import ensure_secret, issue_approval
from mizani.db import db

URL = "http://127.0.0.1:8000/mcp"


async def main() -> None:
    ensure_secret()  # the secret the MCP server signs with, from the shared data folder
    nurse = auth.find(db(), "amina.otieno")  # an active nurse's account
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
        token = issue_approval(proposal["triage_id"], nurse)
        args = {"triage_id": proposal["triage_id"], "approval_token": token}
        print("commit:", (await mizani.call_tool("commit_triage", args)).structured_content)
        again = await mizani.call_tool("commit_triage", args)
        print("same token again:", again.content[0].text)


asyncio.run(main())
```

```
commit: {'triage_id': '17ad86d2bc6a', 'colour': 'orange', 'approver': 'Amina Otieno', 'action': 'approve', 'committed_at': '2026-11-16T17:08:03.969379+00:00'}
same token again: Error executing tool commit_triage: commit refused: this triage is already committed
```

A token works for one triage, one purpose (`commit`, `handover` or `called_in`), one account, one use, and two minutes. In another language, build it the way `src/mizani/approval.py` does: the payload as JSON (with `account_id`, `approver`, `role` and `chief` exactly as the account has them), encoded as URL-safe base64, then a dot, then the HMAC-SHA256 of that base64 text in hex, keyed with the secret. The rules are in [systems/approval.md](systems/approval.md).

## Send patients to Mizani's agent over HTTP

A registration system or another front end can use the whole agent through the API: it reads the complaint with the model, calls the tools, and waits for the nurse. The API works for a signed-in account only, so the front end signs in as the nurse (a cookie jar keeps the session) and sends `X-Mizani: 1` with every change. Four calls take one patient from sign-in to a saved triage (Bash shown):

```
M='-b jar -c jar -H X-Mizani:1 -H content-type:application/json'
# 0. Sign in as the nurse (an account an admin added under Users)
curl -s $M -X POST localhost:8090/api/auth/login -d '{"username":"amina.otieno","password":"<the nurse password>"}'
# 1. Intake: returns the run id
curl -s $M -X POST localhost:8090/api/intake \
  -d '{"patient_label":"Test Patient 01","complaint":"chest pain","vitals":{"respiratory_rate":18,"heart_rate":118,"avpu":"alert"}}'
# 2. The agent's steps as they happen, then the proposal with its triage_id. Ctrl+C after "proposal".
curl -N -b jar localhost:8090/api/triage/<run_id>/stream
# 3. The nurse's decision: the API signs the approval token for this account and the agent commits
curl -s $M -X POST localhost:8090/api/triage/<run_id>/decision -d '{"action":"approve","triage_id":"<triage_id>"}'
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

The model is a setting: `LLM_MODEL` and `LLM_BASE_URL` in `.env` point the agent at any OpenAI-compatible server ([REMOTE_MODEL.md](REMOTE_MODEL.md)). A triage scale is a YAML rule pack in `src/mizani/rules/`, where every rule records its source ([systems/rules-engine.md](systems/rules-engine.md)). The server loads the pack that `MIZANI_RULE_PACK` names (default `sats_adult`), and refuses one in which a clinical rule does not cite one of the pack's own references.

## Limits

- Local only. Every port is bound to `127.0.0.1`. The API checks who is calling; the MCP server and the FHIR servers do not (the MCP server only refuses saves without a token), so keep those three on this machine and give other machines the page and the API only.
- Anyone who can read the data folder can read the approval secret and sign an approval for an existing account, as they can read the database itself. Protect the server's disk like the patient records it holds.
- The examples create test patients. They stay in the waiting list and the audit log until `docker compose down -v`, which deletes the stored data (and the downloaded model).
- Synthetic data only. Mizani is a prototype that has not been connected to a real hospital system or validated in a clinic.
