# API

The HTTP API the nurse's web page talks to. It starts one agent run per patient, streams the agent's steps live, turns the nurse's decision into an approval token, writes committed triages to FHIR, drafts and releases the doctor handover, records the nurse's own colour in shadow mode, and serves the queue, the colour list and the audit log.

## Where it lives
- Code: `src/mizani/api.py` (command `mizani-api`); re-check watch `src/mizani/agent/watch.py`
- Tests: `tests/test_api.py` (the real flow and MCP server in memory, a fake model)
- Container: service `app` in `docker-compose.yml`, `http://127.0.0.1:8090` (`MIZANI_API_PORT`, default 8090: 8080 is often taken)
- Settings: `LLM_BASE_URL`, `LLM_MODEL`, `MIZANI_MCP_URL`, `MIZANI_APPROVAL_SECRET` (same value as the MCP server), `MIZANI_DB`, `MIZANI_API_HOST` (default `127.0.0.1`), `MIZANI_API_PORT` (default `8090`), `MIZANI_FHIR_MCP_URL` (wso2's server; unset means no FHIR output), `MIZANI_WEB_DIR` (the built web page served at `/`, default `web/dist`; skipped if the folder doesn't exist)

## How it works
1. At start-up the API connects to `mizani-triage` (retrying while it starts), loads its tools, the rule pack and the colours, and builds the agent (agent.md) with LangGraph's SQLite checkpointer in `checkpoints.db` next to the database.
2. `POST /api/intake` returns a run id at once and starts the agent in the background. The run id is the `{id}` in every `/api/triage/{id}/...` URL. The proposal has its own `triage_id`: the stored record, which changes when the nurse re-measures.
3. `GET /api/triage/{id}/stream` sends server-sent events: `step` for every trace row, `proposal` when the agent pauses for the nurse, then `committed` or `error`, which end the stream. Event ids count from 0, so a browser that reconnects with `Last-Event-ID` gets only what it missed. A run started before an API restart is rebuilt from its checkpoint.
4. `POST /api/triage/{id}/decision` validates the nurse's decision (a name, the `triage_id` of the proposal she read; an override needs a colour and a reason). If the agent re-scored since (re-measured vitals), it returns 409 and she reviews the new proposal. Otherwise it calls `issue_approval` for the proposal on screen and resumes the agent, which calls `commit_triage` with the token. A refusal (for example an override to a colour that is not on the chart) returns 409 with the server's reason, and the run keeps waiting for the nurse.
5. `POST /api/triage/{id}/vitals` sends re-measured vitals; the agent re-scores without another model call and a new `proposal` event follows on the stream.
6. Re-triage watch. A background task (`agent/watch.py`) reads `queue://current` every 30 s and calls `raise_recheck_flag` for each patient past their target time and not flagged yet; the queue then shows `recheck_raised`. `POST /api/queue/{triage_id}/retriage` takes the nurse's fresh vitals and starts a re-check run (no model call; the patient's chart signs are kept), streamed and decided like any run. The committed re-triage replaces the old queue entry. A second request while a re-check run is still open returns that same run.
7. FHIR. With `MIZANI_FHIR_MCP_URL` set, the agent's commit step writes the committed triage to HAPI through wso2's server (fhir.md); the stream shows it as a `fhir_create` step and the audit log records it.
8. Handover (W2). `POST /api/queue/{triage_id}/handover` asks the model for a three-line, findings-only handover (the server's `handover_summary` prompt plus the stored record) and saves it with `draft_handover`; if the server refuses the draft, the model gets one retry with the reason. `POST /api/queue/{triage_id}/handover/{draft_id}/release` turns a named nurse's release into a handover token for that exact draft and calls `release_handover`. When the nurse overrode the proposal, the handover's "why this colour" gives her colour and reason, never the colour she rejected.
9. Shadow mode (W3). While the run waits for the nurse, `POST /api/triage/{id}/assessment` records her own colour with `record_nurse_assessment`; the page should send it before it shows Mizani's colour. `GET /api/shadow` returns the agreement report.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `POST /api/intake` | `{patient_label, complaint, vitals}` | 202 `{triage_id}` (the run id) | starts an agent run |
| `GET /api/triage/{id}/stream` | optional `Last-Event-ID` | events `step`, `proposal` (`{proposal, error}`), `committed`, `error` | none |
| `POST /api/triage/{id}/decision` | `{nurse_name, action, colour?, reason?, triage_id}` (`triage_id` from the proposal on screen) | 200 committed triage, 409 with the reason (incl. a newer proposal), 422 invalid | approval token, commit, audit rows |
| `POST /api/triage/{id}/vitals` | `{vitals}` | 202 | re-scores, new pending triage |
| `POST /api/queue/{triage_id}/retriage` | `{vitals}` (fresh) | 202 `{triage_id}` (new run id); 404 unknown; 409 not a committed patient still in the queue | re-check run, new pending triage with a `retriage` flag |
| `GET /api/queue/{triage_id}` | | `{triage, handovers}`: the stored record and its handover drafts | none |
| `POST /api/queue/{triage_id}/handover` | | 201 `{triage_id, draft_id, status: awaiting_release, text}`; 409 with the reason | one model call, stored draft |
| `POST /api/queue/{triage_id}/handover/{draft_id}/release` | `{nurse_name}` | released `{text, approver, released_at}`; 409 (other draft, used, replaced triage) | handover token, release |
| `POST /api/triage/{id}/assessment` | `{nurse_name, colour, reason?}` | 201 `{triage_id, approver, colour}`; 409 already recorded or not waiting | stored assessment |
| `GET /api/shadow` | | `{assessed, agreed, agreement_rate, disagreements[]}` | none |
| `GET /api/colours` | | `[{colour, label, target_minutes, source}]` from the rule pack | none |
| `GET /api/queue` | | waiting patients by colour then arrival (`queue://current`), with `waited_minutes`, `minutes_since_triage`, `target_minutes`, `recheck_due`, `recheck_raised` | none |
| `GET /api/audit` | optional `triage_id` | every logged tool call, oldest first | none |
| `GET /api/audit/verify` | | `{intact, first_broken_row}` | none |
| `GET /` | | the nurse's web page (web.md), when `web/dist` is built; the Docker image always has it | none |

## Depends on / used by
- Depends on: the agent (agent.md), `approval.py`, `audit.py`, the `mizani-triage` MCP server (tools and the `rules://sats/adult`, `rules://sats/targets` and `queue://current` resources), FastAPI, uvicorn.
- Used by: the nurse's web page (`web/`, web.md), which it also serves at `/`; curl and the FastAPI `/docs` page for testing.

## Safety rules this system enforces
- Only this API makes approval tokens, and only from a validated decision with a nurse's name; the token goes from here to the agent's commit step and never to the model or the browser.
- The UI never decides: colours, target times and queue order all come from the rule pack and the MCP server.
- Every tool call of every run lands in the hash-chained audit log, and `/api/audit/verify` checks the chain.

## Running and testing it alone
```
uv run pytest tests/test_api.py
MIZANI_APPROVAL_SECRET=dev uv run mizani-mcp        # terminal 1
MIZANI_APPROVAL_SECRET=dev uv run mizani-api        # terminal 2, needs Ollama with the model
curl -X POST localhost:8090/api/intake -H 'content-type: application/json' \
  -d '{"patient_label":"Test Patient 01","complaint":"chest pain","vitals":{"heart_rate":118}}'
curl -N localhost:8090/api/triage/<id>/stream
curl -X POST localhost:8090/api/triage/<id>/decision -H 'content-type: application/json' \
  -d '{"nurse_name":"Nurse A","action":"approve","triage_id":"<proposal triage_id from the stream>"}'
```
Or open `http://127.0.0.1:8090/docs`. Both processes must use the same `MIZANI_DB` and `MIZANI_APPROVAL_SECRET`.

### Full walk-through on the compose stack
After `docker compose up` (add `-f docker-compose.gpu.yml` on an NVIDIA machine), one patient from intake to a released handover. Bash shown; on Windows use Git Bash, or the same calls from the `/docs` page.
```
# 1. Intake: returns the run id
curl -s -X POST localhost:8090/api/intake -H 'content-type: application/json' -d '{"patient_label":"Test Patient 01","complaint":"Fell from the roof of a two-storey house 20 minutes ago, back pain.","vitals":{"respiratory_rate":18,"heart_rate":100,"systolic_bp":135,"temperature":36.6,"avpu":"alert","mobility":"stretcher","trauma":true}}'
# 2. The agent's steps live, then the proposal (expected: orange, high energy transfer). Ctrl+C after "proposal".
curl -N localhost:8090/api/triage/<run_id>/stream
# 3. The nurse approves the proposal she read (its triage_id is in the "proposal" event); the reply is the committed triage
curl -s -X POST localhost:8090/api/triage/<run_id>/decision -H 'content-type: application/json' -d '{"nurse_name":"Nurse A","action":"approve","triage_id":"<triage_id>"}'
# 4. The stream again: it replays the run and ends with the fhir_create step, then "committed"
curl -N localhost:8090/api/triage/<run_id>/stream
# 5. Handover: the model drafts (returns draft_id and text), a named nurse releases
curl -s -X POST localhost:8090/api/queue/<triage_id>/handover
curl -s -X POST localhost:8090/api/queue/<triage_id>/handover/<draft_id>/release -H 'content-type: application/json' -d '{"nurse_name":"Nurse A"}'
# 6. The record, the queue and the audit chain
curl -s localhost:8090/api/queue/<triage_id>
curl -s localhost:8090/api/queue
curl -s localhost:8090/api/audit/verify     # {"intact":true,"first_broken_row":null}
```
The FHIR records can be read back from HAPI at `http://127.0.0.1:18080/fhir/Encounter`.

## Known limitations
- No login: the nurse's name is typed (approval.md).
- The watch polls every 30 s, so a re-check flag can appear up to 30 s after the target time passes.
- The stream events of a run live in memory; after a restart they are rebuilt from the checkpoint, so a run that failed with an error shows its steps but not the error.
- One process: runs started on one API instance are not shared with another.

## Extending it
A new endpoint goes in `create_app()` with a test through `TestClient`. If it changes a shape the UI uses, update the API contract and its "Contract changes" list in the team notes in the same change.
