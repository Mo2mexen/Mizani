# Mizani

Mizani is an offline triage co-pilot for the front desk of a busy public hospital. A nurse types in a patient's vital signs and complaint, in English or Arabic. Mizani scores the patient on the South African Triage Scale (SATS), shows the exact rule behind every point, and suggests a priority colour. Nothing is saved until a named nurse confirms or overrides it. While patients wait, Mizani watches the queue and asks for fresh vitals when someone passes the target time for their colour.

*Mizani* means "scale" or "balance" in Swahili (*mizani*) and Arabic (*mīzān*).

> **Status:** in development for the [African Agentic AI Design Challenge](https://agentic-africa-challenge.lovable.app/), Healthcare track ("The Triage Desk"). The quickstart below arrives with the first working build. See [What works today](#what-works-today).

## The problem

In many public outpatient and emergency departments across Africa, one or two nurses sort every patient who walks in. They use a paper triage chart that turns vital signs and symptoms into a colour. When the queue is long, the chart is applied from memory and under pressure, and studies across the continent keep finding patients scored lower than their condition warrants. An urgent patient can end up waiting behind a routine follow-up, and the hospital keeps no record of why a patient was ranked where they were.

The chart is sound. The errors come from applying it by hand when the desk is busiest. Sources for the figures behind this will be listed in `docs/RESEARCH.md`.

## What Mizani does at the desk

1. The nurse enters vitals (respiratory rate, pulse, blood pressure, temperature, oxygen saturation, level of consciousness, mobility, trauma) and a free-text complaint.
2. The agent extracts the facts, checks the vitals, and asks the nurse to re-measure any value that is missing or impossible.
3. A rules engine computes the Triage Early Warning Score (TEWS) and matches the complaint against the SATS discriminator list. The suggested colour is the higher of the two, and every point carries a citation.
4. The nurse confirms or overrides the suggestion with a reason. The MCP server refuses to save a triage without an approval from a named person.
5. The confirmed triage goes to the queue, the audit log and a local FHIR record.
6. While patients wait, Mizani compares their waiting time with the SATS target for their colour, raises a re-check flag when they are overdue, and re-scores with new vitals. Any change of colour goes back to the nurse.
7. On request, it drafts a three-line handover for the doctor: findings, what set the colour, and time waited.

In shadow mode the nurse records their own colour before seeing Mizani's, and an agreement report shows where the two differ.

## What Mizani never does

- Diagnose a condition or suggest treatment. Its outputs are fixed fields: a colour, the rules that produced it, flags and a factual handover.
- Move a patient in the queue or change a colour without a named nurse's approval.
- Produce a priority without a source. When the language model and the rules disagree, the rules win and the case is flagged.
- Follow instructions written into a complaint field. Patient text is treated as data.
- Use real patient data or send anything off the machine. All development and testing use synthetic patients.

## How it works

```
Nurse UI (React): intake · queue board · approval dialog · agent trace · audit log
        │
FastAPI + LangGraph agent (pauses for the nurse, resumes after approval)
  ├─ Model: any OpenAI-compatible endpoint → Ollama + Qwen3.5 locally
  ├─ MCP ─► mizani-triage (our server): tools · resources · prompts
  └─ MCP ─► wso2 fhir-mcp-server ─► HAPI FHIR (local)
Audit log (SQLite, hash-chained): every tool call with inputs, outputs, time, approver, model, tokens, latency, cost
```

### Our MCP server

| Tool | Kind | Purpose |
|---|---|---|
| `validate_vitals` | read | Range, unit and completeness checks; returns re-measure requests |
| `compute_tews` | read | TEWS with a source for each component |
| `match_discriminators` | read | Maps extracted symptoms to SATS discriminators; only real chart entries are accepted |
| `propose_priority` | action | Creates a pending triage with colour and citations |
| `commit_triage` | action, needs approval | Saves the triage; requires an approval token and the approver's name |
| `raise_recheck_flag` | action | Flags an overdue or deteriorating patient for the nurse |
| `draft_handover` | action, needs approval | Drafts the doctor handover (findings only) |
| `record_nurse_assessment` | action | Stores the nurse's own colour for shadow mode |

Rule packs, target times, the live queue and each patient's timeline are exposed as MCP resources. The intake and handover instructions are MCP prompts.

### The borrowed MCP server

[wso2/fhir-mcp-server](https://github.com/wso2/fhir-mcp-server) connects the agent to a local [HAPI FHIR](https://hapifhir.io/) server. FHIR is the format hospital record systems use to exchange data, and using a maintained, tested FHIR MCP server saved us from writing our own FHIR client.

### Models

Scoring is done by deterministic rules, so the language model only reads free text, plans tool calls and writes explanations. That keeps a small open-weights model practical. The default is Qwen3.5 4B, which runs on a laptop with a 4 GB GPU or on a 16 GB Apple Silicon laptop. We also test Qwen3.5 9B on a desktop GPU. The same family goes up to 397B parameters, so a machine with more memory can run a larger model by changing `LLM_BASE_URL` and `LLM_MODEL`. The safety checks do not depend on which model runs.

### Rule packs

Triage scales are YAML files, and each rule records its source. To add a scale (paediatric SATS, the WHO/ICRC/MSF Integrated Interagency Triage Tool, Manchester), you add a YAML file. The engine code stays the same.

## Evaluation

`EVALS.md` will report each test task run five times, with pass rate, run-to-run variation, latency, tokens and cost per run. The tasks cover correct colour and discriminator, recovery from missing or impossible vitals, re-triage timing, refusal to diagnose, instructions hidden in complaint text, attempts to commit without approval, and Arabic input. At least one failure we have not fixed will stay documented, along with what we would try next.

## Running it

`git clone`, then `docker compose up`. Today this starts the model server (Ollama, pulling the model once) and our MCP server at `http://127.0.0.1:8000/mcp`, which you can try by hand with `npx @modelcontextprotocol/inspector`. The agent and web UI join the same command as they land. Everything you need to install, for running and for development, is listed in [packages.txt](packages.txt). How the parts fit: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Develop and test

You need Python 3.12, [uv](https://docs.astral.sh/uv/) and Docker (see [packages.txt](packages.txt)).

```
uv sync                          # install dependencies from uv.lock
uv run pytest                    # all tests; none of them need a language model
uv run ruff check . && uv run ruff format --check .
MIZANI_APPROVAL_SECRET=dev uv run mizani-mcp      # MCP server at http://127.0.0.1:8000/mcp
npx @modelcontextprotocol/inspector              # call its tools by hand in the browser
```

On Windows PowerShell, set the secret with `$env:MIZANI_APPROVAL_SECRET="dev"` first. On a Mac, run Ollama natively rather than in Docker (Docker on macOS has no GPU, and the model runs 4–5 times slower there) and set `LLM_BASE_URL=http://host.docker.internal:11434/v1` in `.env`. How each part works is in [docs/systems/](docs/systems/).

## What works today

| Part | State |
|---|---|
| Design, rule-pack format, tool list | done |
| Rules engine (SATS adult), every rule cited | done, 85 tests |
| MCP server `mizani-triage`: 5 tools, 4 resources, 2 prompts, approval gate, hash-chained audit | done, tested through an MCP client |
| Agent (LangGraph): reads the entry, checks vitals, scores through the MCP tools, pauses for the nurse, commits with the nurse's token; retries and falls back when the model's tool calls are wrong; model, tokens and cost logged with every call | done, tested with a fake model and live with Qwen3.5 4B |
| API, web UI | in progress |
| FHIR integration | not started |
| Re-triage watch: target-time timers, `raise_recheck_flag`, re-score as a new pending proposal | done (server side) |
| Handover, shadow mode | not started |
| Evaluation suite | not started |

This table is updated as parts land.

## Later

Paediatric SATS and the WHO/ICRC/MSF triage tool as rule packs, voice input for complaints, WhatsApp check-in before arrival, Bluetooth pulse oximeters, daily counts to DHIS2, a training mode for new nurses, and a multi-hospital deployment on Postgres.

## Third-party components

| Component | Licence |
|---|---|
| MCP Python SDK | MIT |
| LangGraph, LangChain (langchain-core, langchain-openai) | MIT |
| FastAPI | MIT |
| Pydantic | MIT |
| PyYAML | MIT |
| React, Vite | MIT |
| Ollama | MIT |
| Qwen3.5 open-weights models | Apache-2.0 |
| wso2 fhir-mcp-server | Apache-2.0 |
| HAPI FHIR | Apache-2.0 |
| SQLite | Public domain |

The South African Triage Scale is published by the Emergency Medicine Society of South Africa. Mizani encodes its rules with citations and does not reproduce the SATS manual.

## Team

Built by a team of three students at the Arab Academy for Science, Technology and Maritime Transport (AAST), Egypt: [@Mo2mexen](https://github.com/Mo2mexen), [@i-am-Ali](https://github.com/i-am-Ali), [@JohnAtef907](https://github.com/JohnAtef907).

## Licence

[Apache-2.0](LICENSE)
