# Mizani architecture

Mizani is a triage co-pilot for a hospital front desk. A nurse enters vitals and a complaint; an agent turns them into tool calls; a deterministic rules engine scores the patient on the South African Triage Scale (SATS); the nurse approves or overrides; only then is the triage committed. Everything runs on one machine, offline, with an open-weights model.

```
 Nurse (browser) ── intake · approval dialog · queue · agent trace · audit
        │  HTTP + server-sent events
 FastAPI + LangGraph agent  ── pauses at the nurse, resumes after approval
   │   model: any OpenAI-compatible endpoint (default Ollama + Qwen3.5 4B, local)
   │
   ├─ MCP ──► mizani-triage (built by us)
   │            tools: validate_vitals · compute_tews · match_discriminators
   │                   propose_priority · commit_triage (needs approval token)
   │                   raise_recheck_flag · draft_handover · release_handover (needs token)
   │                   record_nurse_assessment
   │            resources: rules://sats/adult · rules://sats/targets · queue://current
   │                       patient://{id}/timeline · report://shadow-agreement
   │            prompts: triage_intake · handover_summary
   │            ├─ SATS rules engine (YAML rule pack, every rule cited)
   │            └─ SQLite: triages · used tokens · hash-chained audit log
   │
   └─ MCP ──► wso2 fhir-mcp-server (borrowed) ──► HAPI FHIR (local)
```

Diagram as an image: [architecture.png](architecture.png).

## Agent shape
One agent, built as a LangGraph state graph. It extracts the inputs from the nurse's entry, validates the vitals (asking for a re-measure when one is missing or impossible), scores the patient, matches chart discriminators and proposes a colour. Then it stops and waits for the nurse. After her decision it commits and writes the FHIR record. LangGraph's SQLite checkpointer keeps each patient's run, so it resumes after the nurse decides. The agent is the MCP client for both servers over streamable HTTP, using the MCP Python SDK's own client wrapped as LangChain tools (`langchain-mcp-adapters` only supports MCP SDK 1.x); it tags every call with a run id so the audit log can show a patient's whole run, including the checks made before the triage existed. The model reads free text and plans tool calls. The colour comes from the rules engine. When the model and the rules disagree, the rules win and the case is flagged.

The agent is the MCP client for both servers. It connects with the MCP Python SDK client, turns each server tool into a LangChain tool with the server's own schema, and sends the model name, tokens and cost of the model call that asked for a tool with that call, so the audit log records them. Details: [docs/systems/agent.md](systems/agent.md).

## The MCP server we built: `mizani-triage`
No existing MCP server encodes SATS, and we wanted the safety rules enforced by the server itself, so they hold whatever the agent or model does:
- The rule pack (`src/mizani/rules/sats_adult.yaml`) encodes the EMSSA adult chart; every TEWS point, discriminator, flag and colour returns the rule it came from. Sources: [RESEARCH.md](RESEARCH.md).
- `commit_triage` refuses unless it gets a token signed for that triage by the API after a named nurse decides. Tokens expire and work once. The model never holds the signing secret.
- Middleware writes every tool call (inputs, outputs, time, approver, model, tokens, latency, cost) to a SHA-256 hash chain; editing any past row breaks the chain.
- Complaints are stored as patient data. The intake prompt escapes them inside their own tags and tells the model to ignore any instructions they contain.
- The agent only drafts the doctor handover. The draft reaches the doctor when a nurse releases it with a token issued for that draft; a commit token can't release it, and a handover token can't commit.
- The queue reports when a patient's SATS target time has passed. `raise_recheck_flag` alerts the nurse and is refused before the timer runs out. A re-score with fresh vitals is another pending proposal for the nurse to approve.
- The intake prompt lists every chart sign with the EMSSA manual's definition where it has one, and forbids inferring a diagnosis (a twisted ankle is not a fracture).

Details: [docs/systems/mcp-server.md](systems/mcp-server.md), [retriage-watch.md](systems/retriage-watch.md), [handover-and-shadow.md](systems/handover-and-shadow.md), [rules-engine.md](systems/rules-engine.md), [approval.md](systems/approval.md), [audit.md](systems/audit.md).

## The MCP server we borrowed: wso2 `fhir-mcp-server`
After a triage is committed, the agent writes Patient, Encounter and Observation resources to a local HAPI FHIR server through wso2's MCP server (pinned release 0.10.0): the Encounter has status `triaged` and the SATS colour as its priority, and the vital signs use the FHIR R4 LOINC codes. FHIR is how hospital record systems exchange data, and a maintained, tested FHIR MCP server (Apache-2.0) saves us writing and securing our own FHIR client. Only nurse-committed triages are written. Details: [docs/systems/fhir.md](systems/fhir.md).

## Models
Default `qwen3.5:4b-q4_K_M` (fits a 4 GB GPU or a 16 GB Apple Silicon laptop). A larger Qwen3.5 model can be swapped in on a machine with more memory (not evaluated); speed and chart-sign accuracy depend on the model, while the rules, approval gate and audit do not. Any OpenAI-compatible endpoint works through `LLM_BASE_URL` and `LLM_MODEL`. Ollama runs with an 8k context and cloud features off.

## Data
Synthetic patients only. One local SQLite file holds triages, used tokens and the audit log; LangGraph checkpoints and FHIR data stay on the same machine. Nothing is sent to an outside service.

## Running
`docker compose up` starts the model server (Ollama, pulling the model once), `mizani-triage`, the API with the agent (`http://127.0.0.1:8090`), wso2's FHIR MCP server and HAPI FHIR. The API also serves the nurse's web page at `http://127.0.0.1:8090` (built from `web/` in the image). `MIZANI_E2E=1 uv run pytest tests/test_stack.py` checks the whole chain from proposal to FHIR record without a model.

## Status
| Part | State |
|---|---|
| SATS adult rule pack and engine | built, tested |
| `mizani-triage` MCP server: 9 tools, 5 resources, 2 prompts, approval gate, audit chain | built, tested through an MCP client |
| Model server in compose | built, tested with GPU |
| LangGraph agent: extract, validate, score, discriminators, propose, wait for the nurse, commit; with retries, fallback and the re-measure loop | built, tested with a fake model and live with Qwen3.5 4B against `mizani-triage` |
| FastAPI: intake, live agent steps (server-sent events), nurse decision to approval token, re-measure, re-triage, handover draft and release, shadow-mode assessment and report, queue, colours, audit | built, tested, run live |
| Web page (React + Vite, served by the API): intake, live agent trace, proposal with sources, nurse decision, waiting room, handover, audit | built, tested |
| wso2 fhir-mcp-server + HAPI FHIR: the agent's commit step turns a committed triage into a Patient, an Encounter and Observations | built, verified end to end, including a live run with a real model |
| Re-triage watch (timers on the server; the agent's watch raises re-check flags every 30 s; re-triage endpoint re-scores fresh vitals for the nurse to approve) | built, tested, run live |
| Handover (the model drafts, a nurse releases that exact draft), shadow mode (nurse's own colour, agreement report) | built, tested, run live |
| Evaluations: 38 tasks × 5 runs, pass rate, run-to-run variation, latency, tokens, cost ([EVALS.md](../EVALS.md)) | run on Qwen3.5 4B: 155/190 runs pass (82%) |
