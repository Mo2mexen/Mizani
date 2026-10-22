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
   │                   raise_recheck_flag
   │            resources: rules://sats/adult · rules://sats/targets · queue://current
   │                       patient://{id}/timeline        prompts: triage_intake · handover_summary
   │            ├─ SATS rules engine (YAML rule pack, every rule cited)
   │            └─ SQLite: triages · used tokens · hash-chained audit log
   │
   └─ MCP ──► wso2 fhir-mcp-server (borrowed) ──► HAPI FHIR (local)
```

## Agent shape
One agent, built as a LangGraph state graph: extract the inputs from the nurse's entry → validate vitals (ask for a re-measure when one is missing or impossible) → score → match chart discriminators → propose → **interrupt and wait for the nurse** → commit → write the FHIR record. LangGraph's SQLite checkpointer keeps each patient's run, so it resumes after the nurse decides. The agent is the MCP client for both servers, through `langchain-mcp-adapters` over streamable HTTP; it tags every call with a run id so the audit log can show a patient's whole run, including the checks made before the triage existed. The model reads free text and plans tool calls; it never sets a colour. When the model and the rules disagree, the rules win and the case is flagged.

## The MCP server we built: `mizani-triage`
No existing MCP server encodes SATS, and we wanted the safety rules enforced by the server itself, so they hold whatever the agent or model does:
- **Grounded scoring.** The rule pack (`src/mizani/rules/sats_adult.yaml`) encodes the EMSSA adult chart; every TEWS point, discriminator, flag and colour returns the rule it came from.
- **Human approval in the server.** `commit_triage` refuses unless it gets a token signed for that triage by the API after a named nurse decides. Tokens expire and work once. The model never holds the signing secret.
- **Audit.** Middleware writes every tool call (inputs, outputs, time, approver, model, tokens, latency, cost) to a SHA-256 hash chain; editing any past row breaks the chain.
- **Patient text is data.** Complaints are stored as-is; the intake prompt fences them and tells the model to ignore instructions inside them.
- **Watch, never act.** The queue reports when a patient's SATS target time has passed; `raise_recheck_flag` only alerts the nurse (and is refused before the timer runs out), and a re-score with fresh vitals is just another pending proposal for the nurse to approve.
- **Signs by meaning, not guesswork.** The intake prompt lists every chart sign with the EMSSA manual's definition where it has one, and forbids inferring a diagnosis (a twisted ankle is not a fracture).

Details: [docs/systems/mcp-server.md](systems/mcp-server.md), [retriage-watch.md](systems/retriage-watch.md), [rules-engine.md](systems/rules-engine.md), [approval.md](systems/approval.md), [audit.md](systems/audit.md).

## The MCP server we borrowed: wso2 `fhir-mcp-server`
After a triage is committed, the agent writes Patient, Encounter and Observation resources to a local HAPI FHIR server through wso2's MCP server. FHIR is how hospital record systems exchange data, and a maintained, tested FHIR MCP server (Apache-2.0) saves us writing and securing our own FHIR client.

## Models
Default `qwen3.5:4b-q4_K_M` (fits a 4 GB GPU or a 16 GB Apple Silicon laptop); `qwen3.5:9b-q4_K_M` on an 8–12 GB GPU. Any OpenAI-compatible endpoint works through `LLM_BASE_URL` and `LLM_MODEL`. Ollama runs with an 8k context and cloud features off.

## Data
Synthetic patients only. One local SQLite file holds triages, used tokens and the audit log; LangGraph checkpoints and FHIR data stay on the same machine. Nothing is sent to an outside service.

## Running
`docker compose up` starts the model server (Ollama), pulls the model once, and starts `mizani-triage`. The API, web UI and FHIR services join the same compose file as they are built.

## Status
| Part | State |
|---|---|
| SATS adult rule pack and engine | built, 85 tests |
| `mizani-triage` MCP server: 6 tools, 4 resources, 2 prompts, approval gate, audit chain | built, tested through an MCP client |
| Model server in compose | built, tested with GPU |
| LangGraph agent, FastAPI, web UI | in progress |
| wso2 fhir-mcp-server + HAPI FHIR | planned |
| Re-triage watch (timers, re-check flag, re-score) | built on the server; the agent's watch step is in progress |
| Handover draft, shadow mode | planned |
