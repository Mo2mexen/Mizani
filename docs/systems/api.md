# API

The HTTP API the page talks to. It signs people in and checks their role on every request, starts one agent run per patient, streams the agent's steps live, turns the signed-in nurse's decision into an approval token, writes committed triages to FHIR, drafts and releases the doctor handover, gives doctors their waiting room and handover inbox, lets an admin manage accounts, and serves the queue, the colour list and the audit log.

## Where it lives
- Code: `src/mizani/api.py` (command `mizani-api`); accounts and sessions `src/mizani/auth.py` (auth.md); re-check watch `src/mizani/agent/watch.py`
- Tests: `tests/test_api.py` (the real flow and MCP server in memory, a fake model, a signed-in nurse), `tests/test_auth.py` (sign-in, sessions, and the role check of every endpoint for every role)
- Container: service `app` in `docker-compose.yml`, `http://127.0.0.1:8090` (`MIZANI_API_PORT`, default 8090: 8080 is often taken)
- Settings: `LLM_BASE_URL`, `LLM_MODEL`, `LLM_REASONING_EFFORT`, `LLM_API_KEY`, `MIZANI_MCP_URL`, `MIZANI_APPROVAL_SECRET` (optional: unset, the install makes its own in the data folder, shared with the MCP server; approval.md), `MIZANI_DB`, `MIZANI_API_HOST` (default `127.0.0.1`), `MIZANI_API_PORT` (default `8090`), `MIZANI_FHIR_MCP_URL` (wso2's server; unset means no FHIR output), `MIZANI_SECOND_LOOK` (`1` turns on the agent's second look, agent.md), `MIZANI_WEB_DIR` (the built web page served at `/`, default `web/dist`; skipped if the folder doesn't exist), `MIZANI_ALLOWED_HOSTS` (the names the page may be opened with, comma-separated; in compose the default is `127.0.0.1,localhost` plus `MIZANI_HOSTNAME`; any other `Host` gets 400), `FORWARDED_ALLOW_IPS` (uvicorn's own setting: compose sets `*` so the API trusts the `X-Forwarded-Proto` header Caddy adds and marks the session cookie Secure over HTTPS; the API's port is reachable only from the server and the compose network)

## How it works
1. At start-up (`main()`) the API makes sure there is an approval secret (approval.md) and, while no admin has chosen their own password, prints the first admin's one-time password in a box in its log (auth.md). Then it connects to `mizani-triage` (retrying while it starts), loads its tools, the rule pack and the colours, and builds the agent (agent.md) with LangGraph's SQLite checkpointer in `checkpoints.db` next to the database.
2. Every endpoint except `GET /api/health` and `POST /api/auth/login` needs a session cookie, and checks the account's role on the server: 401 without a session, 403 with the wrong role or while a one-time password is still in use. Who may call what is in the table below. A request that changes something (POST) must carry the header `X-Mizani: 1`; without it the request is refused with 403 before anything else runs (a page on another site cannot send a custom header). Every answer carries security headers (a content security policy that allows only the page's own files, no framing, no sniffing, no referrer) and the API's answers carry `Cache-Control: no-store`, so a shared tablet's browser keeps no patient data.
3. `POST /api/intake` (nurse) returns a run id at once and starts the agent in the background. The run id is the `{id}` in every `/api/triage/{id}/...` URL. The proposal has its own `triage_id`: the stored record, which changes when the nurse re-measures.
4. `GET /api/triage/{id}/stream` sends server-sent events: `step` for every trace row, `proposal` when the agent pauses for the nurse, then `committed` or `error`, which end the stream. Event ids count from 0, so a browser that reconnects with `Last-Event-ID` gets only what it missed. A run started before an API restart is rebuilt from its checkpoint, with a `proposal` event after every pause for the nurse as on the first stream, so the ids a reconnecting browser sends still point at the right events. Before each batch of events the stream checks the session again: once the person is signed out, deactivated or timed out, it sends nothing more.
5. `POST /api/triage/{id}/decision` takes the decision of the signed-in nurse (the `triage_id` of the proposal on screen; an override needs a colour and a reason; a typed name is refused, the name is the account's). If the agent re-scored since (re-measured vitals), it returns 409 and the nurse reviews the new proposal. Otherwise it calls `issue_approval` for this account and the proposal on screen, and resumes the agent, which calls `commit_triage` with the token. A refusal (for example an override to a colour that is not on the chart) returns 409 with the server's reason, and the run keeps waiting for the nurse. So does a commit that cannot reach the MCP server (restarting, for example): 409 "The triage server did not answer", the run waits, and the nurse decides again. A run that stopped returns its own error, never an older refusal.
6. `POST /api/triage/{id}/vitals` sends re-measured vitals; the agent re-scores without another model call and a new `proposal` event follows on the stream.
7. Re-triage watch. A background task (`agent/watch.py`) reads `queue://current` every 30 s and calls `raise_recheck_flag` for each patient past their target time and not flagged yet; the queue then shows `recheck_raised`. `POST /api/queue/{triage_id}/retriage` takes the nurse's fresh vitals and starts a re-check run (no model call; the patient's chart signs are kept), streamed and decided like any run. The committed re-triage replaces the old queue entry. A second request while a re-check run is still open returns that same run. A patient who was called in or re-triaged meanwhile gets 409 at once (a stale card on another screen). If the patient's first run never checked the chart signs (the model did not answer), or the chief doctor changed the chart since (a sign may be gone from it, or a new one may apply), the re-check proposal carries the `signs_not_checked` flag. If the patient is called in while a re-check waits for a nurse, `commit_triage` refuses it, so the patient never comes back to the waiting room.
8. FHIR. With `MIZANI_FHIR_MCP_URL` set, the agent's commit step writes the committed triage to HAPI through wso2's server (fhir.md); the stream shows it as a `fhir_create` step and the audit log records it.
9. Handover. `POST /api/queue/{triage_id}/handover` asks the model for the first two lines of a findings-only handover (the server's `handover_summary` prompt plus the stored record), adds the third line, the waiting time, from the queue record read after the model has answered (the server allows one minute of difference, and a slow model takes longer), and saves it with `draft_handover`, which refuses numbers that are not in the record. Before that the API tidies the model's text: at most two lines, bullets stripped, any sentence about the wait dropped (the real wait is added from the queue), and the record goes to the model with real characters, not JSON escapes. A refused draft goes back to the model once with the reason; after that the nurse sees a plain message to draft again or write the handover by hand. `POST /api/queue/{triage_id}/handover/{draft_id}/release` turns the signed-in nurse's release into a handover token for that exact draft and calls `release_handover`. When the nurse overrode the proposal, the handover's "why this colour" gives the nurse's colour and reason, never the colour rejected.
10. The doctors' inbox. `GET /api/handovers` (doctor) reads `handovers://released`: the latest released handover of each patient still waiting, newest first, with the patient's colour and time waited. A handover released before a re-triage stays with the patient.
11. Shadow mode. While the run waits for the nurse, `POST /api/triage/{id}/assessment` records the nurse's own colour (under the account's name) with `record_nurse_assessment`; the page sends it before it shows Mizani's colour. `GET /api/shadow` returns the agreement report.
12. Called in. `POST /api/queue/{triage_id}/called-in` (a nurse, or a doctor's "Seeing this patient") issues a `called_in` token for the signed-in account and calls `record_called_in`. The patient leaves the queue; the record, the audit rows and the FHIR resources stay. The agent has no part in it.
13. The model. At start the agent uses the admin's model choice when one is saved (`settings.py`, the `setting` table), else `.env`. `POST /api/admin/model` saves a new choice and rebuilds the model client and the agent at once, without a restart; a run already going finishes on the model it started with. An empty `api_key` keeps the saved key when the address is the same, so the key never has to travel back to the page. A saved choice never borrows `.env`'s key.
14. Accounts. `POST /api/auth/login` sets the session cookie (HttpOnly, SameSite=Strict, Secure when the request came over HTTPS). `GET /api/auth/me` says who is signed in; `POST /api/auth/password` changes one's own password (the one-time one too) and ends the person's other sessions; `POST /api/auth/logout` ends this one. The admin's endpoints under `/api/admin/users` add a person (the answer carries the one-time password, once), reset a password, change a role, give or remove the chief flag, deactivate or reactivate, and sign a person out everywhere. Every one of these goes into the audit log (auth.md).

## Interfaces
Roles: N nurse, D doctor (the chief doctor too), A admin, chief = a doctor with the chief flag, all = any signed-in account.

| Name | Who | Input | Output | Side effects |
|---|---|---|---|---|
| `POST /api/auth/login` | anyone | `{username, password}` | 200 the account (`schemas.Account`, never the hash) and the session cookie; 401 wrong name or password (the same answer for both); 429 locked (5 misses, 15 minutes); 403 deactivated | session; audit row `sign_in` or `sign_in_refused` |
| `GET /api/auth/me` | signed in | | the account; `must_change: true` while a one-time password is in use | counts as use of the session |
| `POST /api/auth/password` | signed in | `{current, new}` (new: 10 to 200 characters, without the user name) | the account | other sessions end; audit row |
| `POST /api/auth/logout` | signed in | | `{signed_out: true}` | session ends; audit row |
| `GET /api/admin/users` | A | | every account, active first | none |
| `POST /api/admin/users` | A | `{username, full_name, role}` | 201 `{account, one_time_password}`; 409 user name or full name taken | audit row |
| `POST /api/admin/users/{id}/reset-password` | A | | `{one_time_password}` | sessions end, lock lifted; audit row |
| `POST /api/admin/users/{id}/role` | A | `{role}` | the account; 409 own role or the last admin | the chief flag goes when the role is not doctor; audit row |
| `POST /api/admin/users/{id}/chief` | A | `{chief}` | the account (`chief_by`, `chief_at`); 409 not a doctor | audit row |
| `POST /api/admin/users/{id}/active` | A | `{active}` | the account; 409 oneself or the last admin | sessions end on deactivation; audit row |
| `POST /api/admin/users/{id}/sign-out` | A | | `{signed_out: true}` | every session of the account ends; audit row |
| `GET /api/charts` | chief | | the chart in use, the one before it, every uploaded chart with its evaluation's state | none |
| `POST /api/charts` | chief | `{chart_yaml, cases_yaml?}` | 201 the chart; 422 with what the check refused | stored, scores nobody yet; audit `chart_uploaded` (charts.md) |
| `GET /api/charts/{id}` | chief | | the chart with its evaluation report | none |
| `POST /api/charts/{id}/evaluate` | chief | | 202; 409 while another runs | a process of its own on the desk's model; audit rows at start and end |
| `POST /api/charts/{id}/activate` | chief | | `{in_use, previous, approver, overview}`; 409 with the server's reason | chart token for the chief doctor, MCP `activate_chart`; the API reloads the chart's signs, colours and name |
| `POST /api/charts/rollback` | chief | | the same; 409 when the built-in chart is in use | activates the chart in use before |
| `GET /api/admin/model` | A | | where the model runs: `{location, base_url, model, reasoning_effort, has_key, updated_at, updated_by, this_server}`; never the key | none |
| `POST /api/admin/model/test` | A | a model choice (below) | `{ok: true, seconds, model, reply}` or `{ok: false, error}` within 180 s | one short call with a fixed question, no patient text; nothing saved |
| `POST /api/admin/model` | A | `{location: this_server \| hospital \| cloud, base_url?, model?, api_key?, reasoning_effort?, leaves_hospital_confirmed?}`; 422 without an address and a model, a cloud address that is not `https://`, or a cloud choice not confirmed | the setting as GET shows it | saved in the `setting` table (`this_server` deletes it: `.env` again); the next model call uses it; audit row `model_changed` with where complaint text goes and whether a key is set |
| `POST /api/intake` | N | `{patient_label, complaint, vitals}` | 202 `{triage_id}` (the run id) | starts an agent run |
| `GET /api/triage/{id}/stream` | N | optional `Last-Event-ID` | events `step`, `proposal` (`{proposal, error}`), `committed`, `error` | none |
| `POST /api/triage/{id}/decision` | N | `{action, colour?, reason?, confirmed?, triage_id}` (`triage_id` from the proposal on screen; `confirmed: true` when the nurse checked a "nurse to confirm" sign with the patient) | 200 committed triage, 409 with the reason (incl. a newer proposal), 422 invalid (a typed name too) | approval token for the account, commit, audit rows |
| `POST /api/triage/{id}/vitals` | N | `{vitals}` | 202 | re-scores, new pending triage |
| `POST /api/queue/{triage_id}/retriage` | N | `{vitals}` (fresh) | 202 `{triage_id}` (new run id); 404 unknown; 409 not a committed patient still in the queue (also when called in) | re-check run, new pending triage with a `retriage` flag |
| `GET /api/queue/{triage_id}` | N, D | | `{triage, handovers}`: the stored record and its handover drafts | none |
| `POST /api/queue/{triage_id}/handover` | N | | 201 `{triage_id, draft_id, status: awaiting_release, text}` (third line: "Waited N min since arrival (target for <colour>: T min)."); 409 with the reason | one or two model calls, stored draft; 503 when the model does not answer |
| `POST /api/queue/{triage_id}/handover/{draft_id}/release` | N | | released `{text, approver, released_at}`; 409 (other draft, used, replaced or called-in patient) | handover token for the account, release |
| `GET /api/handovers` | D | | `[{triage_id, text, released_by, released_at, patient_label, colour, waited_minutes, target_minutes, recheck_due}]` | none |
| `POST /api/queue/{triage_id}/called-in` | N, D | | 200 `{triage_id, approver, called_in_at, waited_minutes}`; 409 not on the waiting list | called-in token for the account; the patient leaves the queue |
| `POST /api/triage/{id}/assessment` | N | `{colour, reason?}` | 201 `{triage_id, approver, colour}`; 409 already recorded or not waiting | stored assessment |
| `GET /api/shadow` | N | | `{assessed, agreed, agreement_rate, disagreements[]}` | none |
| `GET /api/health` | anyone | | 200 `{status: "ok", version}` (the version in `pyproject.toml`) when the API answers and its database can be read (the audit log's last row); 503 otherwise | none (Docker's health check; compose.md) |
| `GET /api/colours` | all | | `[{colour, label, target_minutes, source}]` from the rule pack | none |
| `GET /api/queue` | N, D | | waiting patients by colour then arrival (`queue://current`), with `waited_minutes`, `minutes_since_triage`, `target_minutes`, `recheck_due`, `recheck_raised` | none |
| `GET /api/audit` | all | optional `triage_id` | every logged tool call and account event, oldest first | none |
| `GET /api/audit/verify` | all | | `{intact, first_broken_row}` | none |
| `GET /` | anyone | | the page (web.md), when `web/dist` is built; the Docker image always has it. It shows the sign-in screen until someone signs in | none |

The page marks its own polling (`/api/queue`, `/api/colours`, `/api/handovers`) with `X-Mizani-Background: 1`, which does not count as use of the session, so an idle tablet still signs out after 15 minutes.

## Depends on / used by
- Depends on: the agent (agent.md), `auth.py` (auth.md), `approval.py`, `audit.py`, the `mizani-triage` MCP server (tools and the `rules://sats/adult`, `rules://sats/targets`, `queue://current` and `handovers://released` resources), FastAPI, uvicorn.
- Used by: the page (`web/`, web.md), which it also serves at `/`; curl and the FastAPI `/docs` page for testing.

## Safety rules this system enforces
- Only this API makes approval tokens, and only for the signed-in account whose role may do the action; the token goes from here to the agent's commit step and never to the model or the browser. The MCP server checks the role again (approval.md).
- Every endpoint checks the role on the server; `tests/test_auth.py` calls every endpoint as nobody, nurse, doctor, chief doctor and admin.
- The UI never decides: colours, target times and queue order all come from the rule pack and the MCP server.
- Every tool call of every run, and every sign-in and account change, lands in the hash-chained audit log, and `/api/audit/verify` checks the chain.
- It answers only to the names in `MIZANI_ALLOWED_HOSTS`, so a web page on another site cannot reach it through a browser by pointing its own domain at 127.0.0.1 (DNS rebinding); a change needs `X-Mizani: 1`, which such a page cannot send either.

## Running and testing it alone
```
uv run pytest tests/test_api.py tests/test_auth.py
uv run mizani-mcp        # terminal 1
uv run mizani-api        # terminal 2, needs Ollama with the model; prints the first admin's one-time password
```
Both processes use `.cache/data/` unless `MIZANI_DB` says otherwise; the approval secret is made there on the first start. Open `http://127.0.0.1:8090`, sign in as `admin` with the printed password, choose your own, then add a nurse under Users. The `/docs` page lists every endpoint.

### Full walk-through on the compose stack
After `docker compose up` (with the GPU file on an NVIDIA machine, see compose.md), one patient from sign-in to being called in. Bash shown; on Windows use Git Bash. The cookie jar `jar` keeps the session, and every change sends `X-Mizani: 1`. The nurse account comes from an admin (the page's Users screen, or `docker compose run --rm --no-deps app mizani-admin`); here `amina` has already chosen a password.
```
M='-b jar -c jar -H X-Mizani:1 -H content-type:application/json'
# 0. Sign in
curl -s $M -X POST localhost:8090/api/auth/login -d '{"username":"amina","password":"<her password>"}'
# 1. Intake: returns the run id
curl -s $M -X POST localhost:8090/api/intake -d '{"patient_label":"Test Patient 01","complaint":"Fell from the roof of a two-storey house 20 minutes ago, back pain.","vitals":{"respiratory_rate":18,"heart_rate":100,"systolic_bp":135,"temperature":36.6,"avpu":"alert","mobility":"stretcher","trauma":true}}'
# 2. The agent's steps live, then the proposal (expected: orange, high energy transfer). Ctrl+C after "proposal".
curl -N -b jar localhost:8090/api/triage/<run_id>/stream
# 3. The nurse approves the proposal on screen (its triage_id is in the "proposal" event); the reply is the committed triage
curl -s $M -X POST localhost:8090/api/triage/<run_id>/decision -d '{"action":"approve","triage_id":"<triage_id>"}'
# 4. The stream again: it replays the run and ends with the fhir_create step, then "committed"
curl -N -b jar localhost:8090/api/triage/<run_id>/stream
# 5. Handover: the model drafts (returns draft_id and text), the nurse releases it
curl -s $M -X POST localhost:8090/api/queue/<triage_id>/handover
curl -s $M -X POST localhost:8090/api/queue/<triage_id>/handover/<draft_id>/release
# 6. The record, the queue and the audit chain
curl -s -b jar localhost:8090/api/queue/<triage_id>
curl -s -b jar localhost:8090/api/queue
curl -s -b jar localhost:8090/api/audit/verify     # {"intact":true,"first_broken_row":null}
# 7. The patient goes in to be seen (a nurse here; a doctor's "Seeing this patient" calls the same), and the queue is empty again
curl -s $M -X POST localhost:8090/api/queue/<triage_id>/called-in
```
The FHIR records can be read back from HAPI at `http://127.0.0.1:18080/fhir/Encounter`.

## Known limitations
- The watch polls every 30 s, so a re-check flag can appear up to 30 s after the target time passes.
- The stream events of a run live in memory; after a restart they are rebuilt from the checkpoint, so a run that failed with an error shows its steps but not the error.
- One process: runs started on one API instance are not shared with another.

## Extending it
A new endpoint goes in `create_app()` with the role it needs (`Nurse`, `Staff`, `Doctor`, `Admin`, `Anyone`, or `allow(...)`), a test through `TestClient`, and a row in `ENDPOINTS` in `tests/test_auth.py`. If it changes a shape the page uses, update `web/src/types.ts`, `web/src/mock/` and this file in the same change.
