# Audit log

Every tool call, in order, in a log that shows if anyone edited it afterwards. It answers "why was this patient given this colour, by whom, and with which model?"

## Where it lives
- Code: `src/mizani/audit.py`; table `audit` in the SQLite file (`src/mizani/db.py`)
- Written by: the `audit_calls` middleware in `src/mizani/mcp_server.py`
- Tests: `tests/test_mcp_server.py`

## How it works
Each row stores an `AuditEntry` (time, tool, inputs, outputs, an `error` flag for failed or refused calls, approver, model, tokens, latency in ms, cost) plus the triage id and the agent's run id, as canonical JSON. Its hash is SHA-256 of the previous row's hash plus this JSON, so the rows form a chain. Writes take an exclusive lock so the chain never forks. `verify_chain` recomputes every hash and returns the first row that doesn't match.

Model, tokens, cost and `run_id` come from the `_meta` the agent sends with a tool call. The run id (one per patient run) is what lets a patient's timeline include the checks the agent made before `propose_priority` created the triage id. Local models cost 0; the agent can also log its own model calls with `audit.record`.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `record(con, entry, triage_id, run_id)` | `AuditEntry` | the row's hash | appends a row |
| `verify_chain(con)` | | first broken row id, or `None` | none |
| `entries(con, triage_id)` | optional triage id | rows, oldest first; for a triage, also every call from the run that proposed it | none |

## Depends on / used by
- Depends on: `schemas.AuditEntry`, SQLite.
- Used by: the MCP server (every call), the `patient://{id}/timeline` resource, John's `/api/audit` and eval runner (tokens, latency, cost per run).

## Safety rules this system enforces
- Every tool call is logged with inputs, outputs, timestamp, approver, model, tokens, latency and cost, refused calls included.
- Tampering is detectable: a test edits a past row and `verify_chain` finds it.

## Running and testing it alone
```
uv run pytest tests/test_mcp_server.py -k audit
```

## Known limitations
- The chain shows tampering; it doesn't stop someone with file access from rewriting the whole chain. Publishing the latest hash elsewhere (printed shift report, second machine) would close that gap.

## Extending it
New fields go in `AuditEntry` (schemas.py) and are hashed automatically. Changing the JSON layout breaks old hashes, so add fields; don't rename them.
