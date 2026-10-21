# Schemas (shared data contract)

The Pydantic models that the rules engine, the MCP server, the agent and the API all use. If two parts of Mizani exchange data, the shape is defined here.

## Where it lives
- Code: `src/mizani/schemas.py`
- Tests: `tests/test_schemas.py`
- Config / data: none. Colours and target times come from the rule pack, so colours are plain strings here.

## How it works
Every model extends `Contract`, which rejects unknown fields (a typo like `pulse` instead of `heart_rate` fails loudly) and trims surrounding whitespace from text.

- `Vitals`: what the nurse enters. Every field is optional. Ranges are not checked here, so an impossible value such as a pulse of 900 reaches the engine, which asks for a re-measure.
- `Sourced`: base for anything shown to the nurse. `source` (rule id or input field) is required and cannot be blank.
- `TewsComponent`, `Tews`: one scored vital and the total. `Tews` rejects a total that differs from the sum of its components.
- `Discriminator`, `Flag`: a matched chart entry or a warning, each with its source.
- `Proposal`: the suggested colour. `status` can only be `"pending"`; committing happens elsewhere, after a nurse decision.
- `NurseDecision`: needs a nurse name. An override needs a colour and a reason; an approval cannot carry a colour.
- `AuditEntry`: one logged tool call (inputs, outputs, time, approver, model, tokens, latency, cost). The audit store adds the hash chain.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `Vitals` | nurse-entered vitals | validated model | none |
| `Proposal` | engine result | pending proposal | none |
| `NurseDecision` | API decision body | validated decision | none |
| `AuditEntry` | one tool call | log record | none |

## Depends on / used by
- Depends on: `pydantic`.
- Used by: rules engine, MCP server, agent, API (and the shapes the web UI expects).

## Safety rules this system enforces
- No unsourced output: every TEWS point, discriminator and flag needs a non-empty `source`.
- No silent commit: a `Proposal` is always pending.
- Named human approval: a decision without a nurse name is rejected, and an override needs a reason.

## Running and testing it alone
```
uv run pytest tests/test_schemas.py
```

## Known limitations
- Colours are free strings until the rule pack exists; the engine and server must check them against it.
- No range checks on vitals by design; that is the engine's job.

## Extending it
Add the field to the model, add a test, update the API contract the UI uses, and tell the team, since every layer imports these models.
