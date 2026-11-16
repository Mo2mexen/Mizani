# Mizani architecture

Mizani is a triage co-pilot for a hospital front desk. A nurse enters vitals and a complaint; an agent turns them into tool calls; a deterministic rules engine scores the patient on the South African Triage Scale (SATS); the nurse approves or overrides; only then is the triage committed. Everything runs on one machine, offline, with an open-weights model.

```
 Browser: nurse · doctor · admin, each signed in ── intake · approval · queue · trace · inbox · users · audit
        │  HTTPS through Caddy (tablets, desk PCs) + server-sent events; session cookie, role checked on every request
 FastAPI + LangGraph agent  ── pauses at the nurse, resumes after the nurse's approval
   │   accounts and sessions (auth.py, SQLite): scrypt passwords, 15-min idle sign-out
   │   model: any OpenAI-compatible endpoint (default Ollama + Qwen3.5 4B, local)
   │
   ├─ MCP ──► mizani-triage (built by us)
   │            tools: validate_vitals · compute_tews · match_discriminators
   │                   propose_priority · commit_triage (needs approval token)
   │                   raise_recheck_flag · draft_handover · release_handover (needs token)
   │                   record_called_in (needs token) · record_nurse_assessment
   │            resources: rules://sats/adult · rules://sats/targets · queue://current
   │                       handovers://released · patient://{id}/timeline · report://shadow-agreement
   │            prompts: triage_intake · handover_summary
   │            ├─ SATS rules engine (YAML rule pack, every rule cited)
   │            └─ SQLite: triages · used tokens · accounts · hash-chained audit log
   │
   └─ MCP ──► wso2 fhir-mcp-server (borrowed) ──► HAPI FHIR (local)
```

Diagram as an image: [architecture.png](architecture.png).

## Agent shape
One agent, built as a LangGraph state graph. It extracts the inputs from the nurse's entry, validates the vitals (asking for a re-measure when one is missing or impossible), scores the patient, matches chart discriminators and proposes a colour. Then it stops and waits for the nurse. After the nurse's decision it commits and writes the FHIR record. LangGraph's SQLite checkpointer keeps each patient's run, so it resumes after the nurse decides. The model reads free text and plans tool calls. The colour comes from the rules engine. When the model and the rules disagree, the rules win and the case is flagged. If the model server does not answer, the run goes on with the nurse's own vitals, and the proposal tells the nurse that the complaint was not checked against the chart. An optional second look (`MIZANI_SECOND_LOOK=1`, off by default; [EVALS.md](EVALS.md) compares it off and on) shows the model what the server made of its signs and lets it correct or withdraw them once; a withdrawn sign is shown to the nurse, and an emergency sign cannot be withdrawn.

The agent is the MCP client for both servers over streamable HTTP, using the MCP Python SDK's own client (`langchain-mcp-adapters` only supports MCP SDK 1.x). The tools of `mizani-triage` become LangChain tools with the server's own schemas, and the model fills in the arguments of two of them. Every call carries a run id, so the audit log can show a patient's whole run, including the checks made before the triage existed, and the model name, tokens and cost of the model call behind it. wso2's server is not offered to the model: writing the FHIR record is a fixed step after the nurse's commit, so the commit step calls it directly. Details: [docs/systems/agent.md](systems/agent.md).

## The MCP server we built: `mizani-triage`
No existing MCP server encodes SATS, and we wanted the safety rules enforced by the server itself, so they hold whatever the agent or model does:
- The rule pack (`src/mizani/rules/sats_adult.yaml`) encodes the EMSSA adult chart; every TEWS point, discriminator, flag and colour returns the rule it came from. Sources: [RESEARCH.md](RESEARCH.md). `MIZANI_RULE_PACK` chooses another pack file, and the server refuses one in which a clinical rule does not cite one of the pack's own references, or one that lacks the colour of a patient still waiting.
- `commit_triage` refuses unless it gets a token signed for that triage by the API after a signed-in nurse decides, and it checks the token's account itself: active, still a nurse. Tokens expire and work once. The model never holds the signing secret, and each install makes its own.
- A chart sign the model names counts only with the complaint's own words behind it (`{id, quote}`): a sign whose words are missing, not in the complaint, or in text addressed to the software is refused with a flag, and so is a sign the nurse's measurements rule out. When a red colour rests on the complaint's words alone, the proposal asks the nurse to confirm the sign with the patient, and `commit_triage` will not approve it without that confirmation (an override needs none): a false report reads exactly like a true one.
- Middleware writes every tool call (inputs, outputs, time, approver, model, tokens in and out, latency, cost) to a SHA-256 hash chain; editing any past row breaks the chain.
- Complaints are stored as patient data. The intake prompt escapes them inside their own tags and tells the model to ignore any instructions they contain; text addressed to the software is shown to the nurse and never counts.
- The agent only drafts the doctor handover, and `draft_handover` refuses a number that is not in the patient's record; the waiting time is written from the record, not by the model. The draft reaches the doctor when a nurse releases it with a token issued for that draft; a commit token can't release it, and a handover token can't commit.
- The queue reports when a patient's SATS target time has passed. `raise_recheck_flag` alerts the nurse and is refused before the timer runs out. A re-score with fresh vitals is another pending proposal for the nurse to approve.
- A patient leaves the waiting list only when a signed-in nurse or doctor records that the patient was called in (`record_called_in`, with a token for that purpose; a doctor's "Seeing this patient" does this). The agent never calls it, and the record stays.
- The intake prompt lists every chart sign with the EMSSA manual's definition where it has one, asks for the complaint's words behind each sign, and forbids inferring a diagnosis (a twisted ankle is not a fracture).

Details: [docs/systems/mcp-server.md](systems/mcp-server.md), [auth.md](systems/auth.md), [retriage-watch.md](systems/retriage-watch.md), [handover-and-shadow.md](systems/handover-and-shadow.md), [rules-engine.md](systems/rules-engine.md), [approval.md](systems/approval.md), [audit.md](systems/audit.md).

## The MCP server we borrowed: wso2 `fhir-mcp-server`
After a triage is committed, the agent writes Patient, Encounter and Observation resources to a local HAPI FHIR server through wso2's MCP server (pinned release 0.10.0): the Encounter has status `triaged` and the triage colour, with the chart that set it, as its priority, and the vital signs use the FHIR R4 LOINC codes. FHIR is how hospital record systems exchange data, and a maintained, tested FHIR MCP server (Apache-2.0) saves us writing and securing our own FHIR client. Only nurse-committed triages are written. Details: [docs/systems/fhir.md](systems/fhir.md).

## People and roles
Everyone signs in with their own account; there is no signup. A nurse runs the triage desk, a doctor sees the waiting room by priority and the handovers nurses release (and never changes a colour), an admin manages accounts and settings and does nothing clinical, and a doctor with the chief flag will also manage the triage chart. Passwords are hashed with the standard library's scrypt; a session is a random value in an HttpOnly, SameSite=Strict cookie whose hash is all the database keeps; a shared tablet signs out after 15 idle minutes. The API checks the role on every request, the MCP server checks it again for every consequential action, and every sign-in and account change joins the hash-chained audit log. The first admin's one-time password is printed in the server log on the first start; no web page creates an admin. Details: [systems/auth.md](systems/auth.md).

One server per hospital: SQLite fits one hospital's write load (WAL, tested backups); PostgreSQL is for a group of hospitals sharing one service.

## Models
Default `qwen3.5:4b-q4_K_M` (fits a 4 GB GPU or a 16 GB Apple Silicon laptop). A second 4B model, `qwen3:4b-instruct-2507-q4_K_M`, was evaluated as a settings change and passed fewer runs ([EVALS.md](EVALS.md)). A larger Qwen3.5 model can be swapped in on a machine with more memory (not evaluated); speed and chart-sign accuracy depend on the model, while the rules, approval gate and audit do not. Any OpenAI-compatible endpoint works through `LLM_BASE_URL` and `LLM_MODEL`, with `LLM_API_KEY` for a server that checks keys. Ollama runs with an 8k context and cloud features off, and keeps the model in memory between patients.

## Data
Synthetic patients only. One local SQLite file holds triages, used tokens, accounts, sessions and the audit log; LangGraph checkpoints and FHIR data stay on the same machine. Nothing is sent to an outside service. `docker compose run --rm backup` copies all of it into a dated folder with a SHA-256 manifest and the audit log's latest hash; a restore checks both first ([systems/backup.md](systems/backup.md)).

## Running
`docker compose up` starts the model server (Ollama, pulling the model once and loading it into memory), `mizani-triage`, the API with the agent (`http://127.0.0.1:8090`), wso2's FHIR MCP server and HAPI FHIR; the API waits until HAPI answers, so the first FHIR write after a cold start succeeds. Every long-running service has a Docker health check (our API and MCP server through `/health` addresses that read the database), so `docker compose ps` shows which one is broken. The API also serves the web page at `http://127.0.0.1:8090` (built from `web/` in the image). Caddy serves the same page over HTTPS (`https://localhost:8443` by default); in a hospital, two lines in `.env` open only that port to the network under the hospital's name, with Caddy's own certificate authority or the hospital's certificate, and nothing else is reachable from another machine ([INSTALL.md](INSTALL.md)). `MIZANI_E2E=1 uv run pytest tests/test_stack.py` checks the whole chain from proposal to FHIR record without a model.

## Status
| Part | State |
|---|---|
| SATS adult rule pack and engine; the pack chosen by `MIZANI_RULE_PACK` | built, tested |
| `mizani-triage` MCP server: 10 tools, 6 resources, 2 prompts, approval gate with a role per action, audit chain; quoted chart signs, nurse-to-confirm for red from words alone, handover numbers checked against the record | built, tested through an MCP client |
| Model server in compose | built, tested with GPU |
| LangGraph agent: extract, validate, score, discriminators, propose, wait for the nurse, commit; with retries, fallback and the re-measure loop | built, tested with a fake model and live with Qwen3.5 4B against `mizani-triage` |
| FastAPI: intake, live agent steps (server-sent events), nurse decision to approval token, re-measure, re-triage, handover draft and release, shadow-mode assessment and report, queue, colours, audit | built, tested, run live |
| Accounts and roles: sign-in, one-time passwords, lockout, sessions with idle sign-out, nurse / doctor / admin and the chief flag, Admin › Users, the first admin from the server log or `mizani-admin` | built, tested (every endpoint against every role) |
| Web page (React + Vite, served by the API): sign-in, intake, live agent trace, proposal with sources and quotes, nurse decision, waiting room, handover, the doctor's waiting room and handover inbox, Admin › Users, audit; desk, tablet and phone layouts, keyboard and screen-reader use | built, tested in a browser on the full stack |
| wso2 fhir-mcp-server + HAPI FHIR: the agent's commit step turns a committed triage into a Patient, an Encounter and Observations | built, verified end to end, including a live run with a real model |
| Re-triage watch (timers on the server; the agent's watch raises re-check flags every 30 s; re-triage endpoint re-scores fresh vitals for the nurse to approve) | built, tested, run live |
| Handover (the model drafts, a nurse releases that exact draft), shadow mode (nurse's own colour, agreement report) | built, tested, run live |
| Health checks; backup and restore (`docker compose run --rm backup`) | built, tested, run live (a restore onto new, empty volumes included) |
| HTTPS for tablets (Caddy; its own certificate authority or the hospital's certificate; only HTTPS can be opened to the network) | built, checked live (certificate chain, the network address, the closed ports) |
| Evaluations: 39 tasks × 5 runs, pass rate, run-to-run variation, latency, tokens, cost, and the failure kept on purpose (a patient's false claim of a sign) ([EVALS.md](EVALS.md)) | run on 13 Nov 2026: Qwen3.5 4B 180/195 runs pass (92%), the same with the second look on; Qwen3 4B Instruct 2507 145/195 (74%) |
