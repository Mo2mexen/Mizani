# Agent (model client and MCP tools)

The part of Mizani that talks to the language model and to our MCP server. The model reads the nurse's free text and asks for tool calls; the tools on `mizani-triage` do the scoring. The LangGraph flow that runs these steps for each patient (A2) is added to this doc when it lands.

## Where it lives
- Model client: `src/mizani/agent/model.py`
- MCP tools: `src/mizani/agent/mcp_tools.py`
- Tests: `tests/test_agent_model.py`, `tests/test_agent_tools.py`
- Settings: `LLM_BASE_URL`, `LLM_MODEL` (model), `MIZANI_MCP_URL` (our MCP server)

## How it works
1. `chat_model()` builds one OpenAI-compatible chat model from `LLM_BASE_URL` and `LLM_MODEL`. Every request has thinking off (`reasoning_effort: "none"`) and temperature 0. Without settings it uses Ollama on the same machine with `qwen3.5:4b-q4_K_M`.
2. `call_model(llm, messages)` runs one model call and returns the reply with the model name, total tokens, latency in ms and cost (0 for a local model).
3. `load_tools()` connects to `mizani-triage` with the MCP Python SDK client, lists its tools and wraps each one as a LangChain tool with the server's own argument schema. The agent binds these to the model (`llm.bind_tools(tools)`), so the model sees exactly the tools the server offers, including new ones.
4. When the model asks for a tool, the agent runs it with `tool.ainvoke(call, with_meta(model_call.meta()))`. The wrapper opens a connection, calls the tool with `_meta: {model, tokens, cost}` and returns the tool's text plus its structured result. The server's audit log copies `_meta` into the row, so every logged call shows which model asked for it and what that model call cost.
5. A refused or failed tool call (for example `commit_triage` without an approval token) comes back as an error tool message carrying the server's reason, so the agent can retry or report it instead of crashing.

We use the MCP SDK client directly because `langchain-mcp-adapters` only supports MCP SDK 1.x and our server is built on 2.x. Its successor, `langchain.mcp`, does not send `_meta` with a tool call, and the audit log needs it.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `chat_model()` | `LLM_BASE_URL`, `LLM_MODEL` | chat model, thinking off, temperature 0 | none |
| `call_model(llm, messages)` | bound model, messages | `ModelCall`: reply, model, tokens, latency_ms, cost | one model request |
| `ModelCall.meta()` | | `{model, tokens, cost}` for `_meta` | none |
| `load_tools(server)` | URL (default `MIZANI_MCP_URL`) or in-process server | one LangChain tool per MCP tool | lists tools on the server |
| `with_meta(meta)` | `_meta` dict | run config for `tool.ainvoke` | none |
| tool `.ainvoke(call, config)` | tool call | tool message: text, structured result as `artifact`, `status` | MCP tool call, audit row on the server |

## Depends on / used by
- Depends on: `langchain-openai` (OpenAI-compatible client), `langchain-core`, the MCP Python SDK client, the `mizani-triage` server.
- Used by: the LangGraph flow (A2), the API (U1) and the eval runner (E3).

## Safety rules this system enforces
- Every tool call is logged with the model, tokens and cost of the model call that asked for it (through `_meta`).
- The model can only call what the server offers, with the server's schemas. The server, not the agent, refuses unsafe calls such as a commit without a nurse's approval, and the agent sees the refusal.
- Nothing leaves the machine by default: the model endpoint is local Ollama, and LangSmith tracing stays off unless someone sets its environment variables.

## Running and testing it alone
```
uv run pytest tests/test_agent_model.py tests/test_agent_tools.py   # no model needed
MIZANI_APPROVAL_SECRET=dev uv run mizani-mcp                         # then, with Ollama running:
uv run python -c "import asyncio; from mizani.agent.mcp_tools import load_tools; print([t.name for t in asyncio.run(load_tools())])"
```

## Known limitations
- `qwen3.5:4b` sometimes sends a nested argument such as `vitals` as a JSON string; the MCP server parses it, but code that reads tool calls must not assume a dict.
- With all five tools bound, a prompt is about 2,000 tokens. Ollama's default context on some machines is 4,096, so set 8,192 on the server (compose already does).
- Each tool call opens its own connection to the server. That costs a few milliseconds locally and keeps the wrapper simple.

## Extending it
A new tool on the MCP server needs no change here: `load_tools()` picks it up with its schema. A second MCP server (wso2 `fhir-mcp-server`, H2) is another `load_tools(url)` call with its own URL setting.
