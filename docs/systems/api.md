# API

The HTTP API the nurse's web page talks to. It starts one agent run per patient, streams the agent's steps live, turns the nurse's decision into an approval token, and serves the queue, the colour list and the audit log.

## Where it lives
- Code: `src/mizani/api.py` (command `mizani-api`)
- Tests: `tests/test_api.py` (the real flow and MCP server in memory, a fake model)
- Container: service `app` in `docker-compose.yml`, `http://127.0.0.1:8080`
- Settings: `LLM_BASE_URL`, `LLM_MODEL`, `MIZANI_MCP_URL`, `MIZANI_APPROVAL_SECRET` (same value as the MCP server), `MIZANI_DB`, `MIZANI_API_HOST` (default `127.0.0.1`), `MIZANI_API_PORT` (default `8080`)

## How it works
1. At start-up the API connects to `mizani-triage` (retrying while it starts), loads its tools, the rule pack and the colours, and builds the agent (agent.md) with LangGraph's SQLite checkpointer in `checkpoints.db` next to the database.
2. `POST /api/intake` returns a run id at once and starts the agent in the background. The run id is the `{id}` in every `/api/triage/{id}/...` URL. The proposal has its own `triage_id`: the stored record, which changes when the nurse re-measures.
3. `GET /api/triage/{id}/stream` sends server-sent events: `step` for every trace row, `proposal` when the agent pauses for the nurse, then `committed` or `error`, which end the stream. Event ids count from 0, so a browser that reconnects with `Last-Event-ID` gets only what it missed. A run started before an API restart is rebuilt from its checkpoint.
4. `POST /api/triage/{id}/decision` validates a `NurseDecision` (a name; an override needs a colour and a reason), calls `issue_approval` for the proposal on screen and resumes the agent, which calls `commit_triage` with the token. A refusal (for example an override to a colour that is not on the chart) returns 409 with the server's reason, and the run keeps waiting for the nurse.
5. `POST /api/triage/{id}/vitals` sends re-measured vitals; the agent re-scores without another model call and a new `proposal` event follows on the stream.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `POST /api/intake` | `{patient_label, complaint, vitals}` | 202 `{triage_id}` (the run id) | starts an agent run |
| `GET /api/triage/{id}/stream` | optional `Last-Event-ID` | events `step`, `proposal` (`{proposal, error}`), `committed`, `error` | none |
| `POST /api/triage/{id}/decision` | `NurseDecision` | 200 committed triage, 409 with the reason, 422 invalid | approval token, commit, audit rows |
| `POST /api/triage/{id}/vitals` | `{vitals}` | 202 | re-scores, new pending triage |
| `GET /api/colours` | | `[{colour, label, target_minutes, source}]` from the rule pack | none |
| `GET /api/queue` | | committed patients by colour then arrival, with `waited_minutes`, `target_minutes`, `recheck_due` | none |
| `GET /api/audit` | optional `triage_id` | every logged tool call, oldest first | none |
| `GET /api/audit/verify` | | `{intact, first_broken_row}` | none |

## Depends on / used by
- Depends on: the agent (agent.md), `approval.py`, `audit.py`, the `mizani-triage` MCP server (tools and the `rules://sats/adult`, `rules://sats/targets` and `queue://current` resources), FastAPI, uvicorn.
- Used by: the web UI (`web/`, not built yet); curl and the FastAPI `/docs` page until then.

## Safety rules this system enforces
- Only this API makes approval tokens, and only from a validated decision with a nurse's name; the token goes from here to the agent's commit step and never to the model or the browser.
- The UI never decides: colours, target times and queue order all come from the rule pack and the MCP server.
- Every tool call of every run lands in the hash-chained audit log, and `/api/audit/verify` checks the chain.

## Running and testing it alone
```
uv run pytest tests/test_api.py
MIZANI_APPROVAL_SECRET=dev uv run mizani-mcp        # terminal 1
MIZANI_APPROVAL_SECRET=dev uv run mizani-api        # terminal 2, needs Ollama with the model
curl -X POST localhost:8080/api/intake -H 'content-type: application/json' \
  -d '{"patient_label":"Test Patient 01","complaint":"chest pain","vitals":{"heart_rate":118}}'
curl -N localhost:8080/api/triage/<id>/stream
curl -X POST localhost:8080/api/triage/<id>/decision -H 'content-type: application/json' \
  -d '{"nurse_name":"Nurse A","action":"approve"}'
```
Or open `http://127.0.0.1:8080/docs`. Both processes must use the same `MIZANI_DB` and `MIZANI_APPROVAL_SECRET`.

## Known limitations
- No login: the nurse's name is typed (approval.md).
- Until the re-triage watch (W1) raises its own flags, `recheck_due` means the patient has waited longer than the target time for their colour.
- The stream events of a run live in memory; after a restart they are rebuilt from the checkpoint, so a run that failed with an error shows its steps but not the error.
- One process: runs started on one API instance are not shared with another.

## Extending it
A new endpoint goes in `create_app()` with a test through `TestClient`. If it changes a shape the UI uses, update the API contract and its "Contract changes" list in the team notes in the same change.
