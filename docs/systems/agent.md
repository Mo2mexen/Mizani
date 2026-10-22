# Agent

The part of Mizani that talks to the language model and to our MCP server, and runs each patient from the nurse's entry to a committed triage. The model reads the nurse's free text and asks for tool calls; the tools on `mizani-triage` do the scoring; a named nurse decides.

## Where it lives
- Triage flow (LangGraph): `src/mizani/agent/graph.py`
- Model client: `src/mizani/agent/model.py`
- MCP tools: `src/mizani/agent/mcp_tools.py`
- Re-check watch (W1): `src/mizani/agent/watch.py`
- Tests: `tests/test_agent_graph.py`, `tests/test_agent_model.py`, `tests/test_agent_tools.py`, `tests/test_agent_watch.py`
- Settings: `LLM_BASE_URL`, `LLM_MODEL` (model), `MIZANI_MCP_URL` (our MCP server)

## The triage flow
One LangGraph run per patient, keyed by a `thread_id`, with a checkpointer so a run survives the wait for the nurse (the API uses LangGraph's SQLite checkpointer).

```
extract (model) ─► validate ─► score ─► discriminators ─► propose ─► nurse (pause)
   ▲  │ rejected:         │ refused: back to extract          ▲          │
   └──┘ retry (3 tries)   │                                   │          ├─ re-measured vitals ─► validate
   └─► fallback ──────────┘                                   │          └─ approval token ─► commit ─► end
                                                              └───────────── commit refused ◄─┘
```

1. **extract**: one model call with two tools bound, `validate_vitals` and `match_discriminators`. The prompt gives the nurse-entered vitals, the chart discriminator ids (read from `rules://sats/adult`) and the complaint inside `<complaint>` tags, and tells the model to ignore instructions in it. The model answers with both tool calls: the vitals (the nurse's values plus any written in the complaint that the nurse left empty) and the chart ids the complaint describes. The nurse's own values always override the model's.
2. **validate**, **score**, **discriminators**: the server's tools check the vitals, compute TEWS and match the chart ids. If the model skipped a tool or the server rejects its arguments, the flow asks the model again with the reason, up to three model calls. After that it falls back to the nurse's vitals with no discriminators, and the trace says so.
3. **propose**: `propose_priority` stores a pending triage. The rules engine sets the colour; ids that are not on the chart come back as `not_on_chart` flags and set nothing.
4. **nurse**: the run pauses (LangGraph `interrupt`) with the proposal. The nurse answers with re-measured vitals, which re-run validate to propose without another model call, or with an approval token issued by the API.
5. **commit**: `commit_triage` with the token. A refusal (expired, forged, reused) goes back to the nurse with the server's reason.

Every step adds a trace row (`time`, `kind`, `name`, `input`, `output`, `latency_ms`; the UI's `TraceStep`). The approval token is shown as `(hidden)` in the trace and never enters the model's messages.

**Re-check runs (W1).** `watch()` asks the server every 30 s to flag patients past their SATS target time (`raise_recheck_flag`; the server checks the timer). It never changes a colour. When the nurse re-measures a flagged patient, the API starts a run with `retriage_of`, the fresh vitals and the patient's current chart signs. That run starts at validate and never calls the model, so only the vitals can change the colour; `propose_priority(retriage_of=...)` adds a `retriage` flag saying whether it changed, and the nurse approves it like any proposal.

## Model client and MCP tools
1. `chat_model()` builds one OpenAI-compatible chat model from `LLM_BASE_URL` and `LLM_MODEL`. Every request has thinking off (`reasoning_effort: "none"`) and temperature 0. Without settings it uses Ollama on the same machine with `qwen3.5:4b-q4_K_M`.
2. `call_model(llm, messages)` runs one model call and returns the reply with the model name, total tokens, latency in ms and cost (0 for a local model).
3. `load_tools()` connects to `mizani-triage` with the MCP Python SDK client, lists its tools and wraps each one as a LangChain tool with the server's own argument schema. The agent binds these to the model (`llm.bind_tools(tools)`), so the model sees exactly the tools the server offers, including new ones.
4. Tools run with `tool.ainvoke(call, with_meta(meta))`. The wrapper opens a connection, calls the tool with `_meta: {model, tokens, cost, run_id}` and returns the tool's text plus its structured result. The server's audit log copies model, tokens and cost into the row, so every logged call shows which model the run used and what it cost.
5. A refused or failed tool call (for example `commit_triage` without an approval token) comes back as an error tool message carrying the server's reason, so the agent can retry or report it instead of crashing.

We use the MCP SDK client directly because `langchain-mcp-adapters` only supports MCP SDK 1.x and our server is built on 2.x. Its successor, `langchain.mcp`, does not send `_meta` with a tool call, and the audit log needs it.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `build_graph(llm, tools, chart_ids, checkpointer)` | chat model, MCP tools, chart ids | compiled flow | none |
| run: `ainvoke({patient_label, complaint, nurse_vitals}, thread)` | the nurse's entry | state paused at the nurse: `__interrupt__` with `{proposal, error}`, `trace` | model call, tool calls, pending triage |
| resume: `ainvoke(Command(resume=answer), thread)` | `{"action": "remeasure", "vitals": {...}}` or `{"action": "approve" \| "override", "approval_token": ...}` | paused again, or `committed` | tool calls, commit |
| `chat_model()` | `LLM_BASE_URL`, `LLM_MODEL` | chat model, thinking off, temperature 0 | none |
| `call_model(llm, messages)` | bound model, messages | `ModelCall`: reply, model, tokens, latency_ms, cost | one model request |
| `ModelCall.meta()` | | `{model, tokens, cost}` for `_meta` | none |
| `load_tools(server)` | URL (default `MIZANI_MCP_URL`) or in-process server | one LangChain tool per MCP tool | lists tools on the server |
| `with_meta(meta)` | `_meta` dict | run config for `tool.ainvoke` | none |
| `raise_due_rechecks(tools, server)` | MCP tools | flags raised this round | `raise_recheck_flag` calls, audit rows |
| `watch(tools, server)` | MCP tools | runs forever, every 30 s | as above |
| tool `.ainvoke(call, config)` | tool call | tool message: text, structured result as `artifact`, `status` | MCP tool call, audit row on the server |

## Depends on / used by
- Depends on: `langgraph` and `langgraph-checkpoint-sqlite`, `langchain-openai` (OpenAI-compatible client), `langchain-core`, the MCP Python SDK client, the `mizani-triage` server.
- Used by: the API (U1), which starts runs, streams their trace and resumes them with the nurse's answer, and the eval runner (E3).

## Safety rules this system enforces
- The model never sets a colour: it only names vitals and chart ids, the rules engine scores, and signs not on the chart are flagged and ignored (the rules win).
- Nothing is committed without a named nurse: the flow always pauses before `commit_triage`, and only a token from the nurse's decision gets past the server.
- Patient text is data: the complaint is fenced in tags with a closing tag in the text neutralised, and the prompt says to ignore instructions inside it.
- Every tool call is logged with the run's model, and each model call's tokens and cost are on exactly one audit row: the first tool call after it. That includes model answers the flow rejected and retried, so adding up a run's rows gives what the model really used (fixed 22 Oct; before, two rows carried the same model call). Each call also sends `_meta.run_id` (the run's `thread_id`), which the MCP server stores, so a patient's timeline (`patient://{id}/timeline`, `/api/audit?triage_id=`) includes the checks made before the triage existed.
- The model can only call what the server offers, with the server's schemas. The server, not the agent, refuses unsafe calls such as a commit without a nurse's approval, and the agent sees the refusal.
- Nothing leaves the machine by default: the model endpoint is local Ollama, and LangSmith tracing stays off unless someone sets its environment variables.

## Running and testing it alone
```
uv run pytest tests/test_agent_graph.py tests/test_agent_model.py tests/test_agent_tools.py   # no model needed
MIZANI_APPROVAL_SECRET=dev uv run mizani-mcp                         # then, with Ollama running:
uv run python -c "import asyncio; from mizani.agent.mcp_tools import load_tools; print([t.name for t in asyncio.run(load_tools())])"
```

## Known limitations
- On a MacBook Air M4 with `qwen3.5:4b`, a patient reaches the nurse in about 9 to 12 seconds (one model call of about 1,650 tokens). Three live cases on 22 Oct gave the expected colours and the model ignored an instruction hidden in a complaint, but for a fall from a roof it named the compound fracture and missed "high energy transfer". The evals (E2 to E4) measure how often this happens.
- Each re-measure creates a new pending triage with a new id; the earlier pending one stays in the database, uncommitted and out of the queue.
- The approval token passes through the run's checkpoint (it is single-use and expires after 15 minutes).
- `qwen3.5:4b` sometimes sends a nested argument such as `vitals` as a JSON string; the MCP server parses it, but code that reads tool calls must not assume a dict.
- With all five tools bound, a prompt is about 2,000 tokens. Ollama's default context on some machines is 4,096, so set 8,192 on the server (compose already does).
- Each tool call opens its own connection to the server. That costs a few milliseconds locally and keeps the wrapper simple.

## Extending it
A new tool on the MCP server needs no change here: `load_tools()` picks it up with its schema. A second MCP server (wso2 `fhir-mcp-server`, H2) is another `load_tools(url)` call with its own URL setting.
