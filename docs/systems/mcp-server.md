# MCP server (`mizani-triage`)

Our own MCP server. It gives the agent the SATS tools, stores triages, refuses to commit without a nurse's approval, and logs every tool call. Built with the MCP Python SDK v2 (`MCPServer`) over streamable HTTP.

## Where it lives
- Server: `src/mizani/mcp_server.py` (command `mizani-mcp`)
- Triage records: `src/mizani/store.py`; database: `src/mizani/db.py`
- Tests: `tests/test_mcp_server.py` (a real MCP client, in memory)
- Container: `Dockerfile`, service `mizani-mcp` in `docker-compose.yml`
- Settings: `MIZANI_DB` (default `data/mizani.db`, `/data/mizani.db` in Docker), `MIZANI_APPROVAL_SECRET`, `MIZANI_MCP_HOST`, `MIZANI_MCP_PORT`

## How it works
The agent reads the rules (`rules://sats/adult`), checks vitals, names chart discriminators and calls `propose_priority`, which runs the rules engine and stores a **pending** triage. The nurse approves or overrides in the UI; the API turns that into an approval token (approval.md). The agent then calls `commit_triage` with the token. A middleware (`audit_calls`) writes every tool call to the hash-chained audit log (audit.md), including refused calls.

## Interfaces
| Name | Kind | Input | Output | Side effects |
|---|---|---|---|---|
| `validate_vitals` | tool, read | `vitals` | re-measure flags | audit row |
| `compute_tews` | tool, read | `vitals` | TEWS with a source per point | audit row |
| `match_discriminators` | tool, read | `discriminator_ids`, `vitals` | chart signs + `not_on_chart` flags | audit row |
| `propose_priority` | tool, action | `patient_label`, `complaint`, `vitals`, `discriminator_ids`, optional `retriage_of` | pending `Proposal` (with a `retriage` flag when re-scoring) | new triage row, audit row |
| `commit_triage` | tool, action, gated | `triage_id`, `approval_token` | committed colour, approver | triage committed (a re-triage replaces the old entry), token used, audit row |
| `raise_recheck_flag` | tool, action | `triage_id` | `recheck_due` flag citing the target time | refused unless the timer ran out; records the flag; audit row |
| `rules://sats/adult` | resource | | the rule pack (JSON) | none |
| `rules://sats/targets` | resource | | colours and target minutes | none |
| `queue://current` | resource | | waiting patients by colour, then arrival, with time waited, `recheck_due` and `recheck_raised` (retriage-watch.md) | none |
| `patient://{triage_id}/timeline` | resource | | the triage and its audit rows | none |
| `triage_intake` | prompt | `complaint` | intake instructions: every chart sign with its id, colour and, where the EMSSA manual defines it, its meaning; never infer a diagnosis | none |
| `handover_summary` | prompt | `triage_id` | findings-only handover instructions | none |

Optional `_meta` on a tool call is copied into the audit row: `run_id` (one per patient run; links the checks made before the triage existed to that triage's timeline), and `model`, `tokens`, `cost` (which model asked for the call and what it cost). Refused or failed calls get `error: true`.

## Depends on / used by
- Depends on: the rules engine, `schemas.py`, `approval.py`, `audit.py`, SQLite, `mcp`.
- Used by: John's LangGraph agent (MCP client via `langchain-mcp-adapters`), and MCP Inspector for manual testing.

## Safety rules this system enforces
- No commit without a named nurse: `commit_triage` refuses a missing, forged, expired, other-triage or reused token, and a token without an approver name. Tests prove each refusal.
- No unsourced output: every tool result carries sources (the schemas reject output without them).
- Only chart entries: unknown discriminators are refused with a flag.
- Patient text is data: the complaint is stored as-is, and the intake prompt wraps it in tags and tells the model to ignore instructions inside it.
- Read-only queue: the order comes from committed colours and arrival times; no tool reorders it.
- Every call logged: inputs, outputs, time, approver, model, tokens, latency, cost.

## Running and testing it alone
```
uv run pytest tests/test_mcp_server.py
MIZANI_APPROVAL_SECRET=dev uv run mizani-mcp          # http://127.0.0.1:8000/mcp
npx @modelcontextprotocol/inspector                     # connect to that URL, call tools by hand
docker compose up mizani-mcp                            # same server in Docker
```

## Known limitations
- `draft_handover` (W2) and `record_nurse_assessment` (W3) are not built yet.
- One SQLite file; fine for one desk, not for many hospitals (Postgres later).
- The compose default approval secret is for local demos; set your own for any shared machine.

## Extending it
A new tool is one decorated function in `mcp_server.py` that calls the engine or the store, plus a test through the in-memory client. It is audited automatically. If it changes what the agent or UI builds on, follow CLAUDE.md rule 6 (contract docs).
