# MCP server (`mizani-triage`)

Our own MCP server. It gives the agent the SATS tools, stores triages, refuses to commit without the approval of a signed-in nurse (and checks the role of every approval itself), and logs every tool call. Built with the MCP Python SDK v2 (`MCPServer`) over streamable HTTP.

## Where it lives
- Server: `src/mizani/mcp_server.py` (command `mizani-mcp`)
- Triage records: `src/mizani/store.py`; database: `src/mizani/db.py`
- Tests: `tests/test_mcp_server.py` (a real MCP client, in memory)
- Container: `docker/Dockerfile`, service `mizani-mcp` in `docker-compose.yml`
- Settings: `MIZANI_DB` (default `.cache/data/mizani.db`, `/data/mizani.db` in Docker), `MIZANI_APPROVAL_SECRET` (optional: unset, the install makes its own beside the database; approval.md), `MIZANI_MCP_HOST`, `MIZANI_MCP_PORT`, `MIZANI_ALLOWED_HOSTS` (the names it answers to, comma-separated, default `127.0.0.1,localhost`; compose adds `mizani-mcp`, the name the app uses)

## How it works
Every tool scores with the chart in use: the chief doctor's activated chart, else the built-in pack (charts.md); `propose_priority` writes its name on the proposal and the record. The agent reads the rules (`rules://sats/adult`), checks vitals, names chart discriminators, each with the words of the complaint that show it, and calls `propose_priority`, which runs the rules engine and stores a pending triage. The signed-in nurse approves or overrides in the page; the API turns that into an approval token for the nurse's account (approval.md), and every gated tool checks the token's account in the database: active, the same role, and a role allowed for that tool. The agent then calls `commit_triage` with the token. A middleware (`audit_calls`) writes every tool call to the hash-chained audit log (audit.md), including refused calls.

## Interfaces
| Name | Kind | Input | Output | Side effects |
|---|---|---|---|---|
| `GET /health` | plain HTTP beside `/mcp` | none | 200 `{status: "ok"}` when the triage database can be read (it reads the audit log's last row, not just an open connection); 503 otherwise | none, not audited (it is not a tool call); Docker's health check (compose.md) |
| `validate_vitals` | tool, read | `vitals` | re-measure flags | audit row |
| `compute_tews` | tool, read | `vitals` | TEWS with a source per point | audit row |
| `match_discriminators` | tool, read | `discriminators` (`[{id, quote}]`), `vitals`, `complaint` | chart signs with their quotes, and a flag for each refused sign (`not_on_chart`, `conflicts_with_vitals`, `not_in_complaint`, `ignored_instruction`; rules-engine.md) | audit row |
| `propose_priority` | tool, action | `patient_label`, `complaint`, `vitals`, `discriminators` (`[{id, quote}]`), optional `retriage_of`, optional `withdrawn` (`[{id, quote}]`: signs the agent named and then took back), optional `signs_checked` (false when the agent got no chart signs from the model; adds a `signs_not_checked` flag) | pending `Proposal` (a `confirm_sign` flag when red rests on the complaint alone; a `retriage` flag when re-scoring; a `sign_withdrawn` flag for a withdrawn sign that would have counted; a withdrawn emergency sign still counts) | new triage row, audit row |
| `commit_triage` | tool, action, gated | `triage_id`, `approval_token` | committed colour, approver; refused unless the token is from an active nurse's account; refused to approve a proposal with a `confirm_sign` flag unless the nurse confirmed it (an override needs no confirmation) | triage committed (a re-triage replaces the old entry), token used, audit row |
| `draft_handover` | tool, action | `triage_id`, `text` (1 to 3 lines) | unreleased draft; refused if a number is not in the record or the stated waiting time is wrong | stores it; audit row (handover-and-shadow.md) |
| `release_handover` | tool, action, gated | `triage_id`, `draft_id`, `approval_token` (purpose handover, naming that draft, from a nurse's account) | released text, approver | marks it released, token used; audit row |
| `record_called_in` | tool, action, gated | `triage_id`, `approval_token` (purpose `called_in`, from a nurse's or a doctor's account) | `{triage_id, approver, called_in_at, waited_minutes}` | the patient leaves the waiting queue and the re-check timer stops; the record stays; token used; audit row |
| `activate_chart` | tool, action, gated | `chart` (an uploaded chart's id, or `built-in`), `approval_token` (purpose `chart`, from the chief doctor) | `{in_use, previous, approver}` | the chart in use changes for every new triage; refused without a held evaluation or without a waiting patient's colour; token used; audit row (charts.md) |
| `record_nurse_assessment` | tool, action | `triage_id`, `nurse_name`, `colour`, `reason` | stored assessment | one per triage; audit row |
| `raise_recheck_flag` | tool, action | `triage_id` | `recheck_due` flag citing the target time | refused unless the timer ran out; records the flag; audit row |
| `rules://sats/adult` | resource | | the chart in use (JSON): the chief doctor's activated chart, else the built-in pack `MIZANI_RULE_PACK` names (default the adult SATS chart); its `id` says which. The address keeps its name so agents written against it keep working | none |
| `rules://sats/targets` | resource | | colours and target minutes | none |
| `queue://current` | resource | | waiting patients by colour, then arrival, with time waited, `recheck_due` and `recheck_raised` (retriage-watch.md) | none |
| `handovers://released` | resource | | the doctors' inbox: the latest released handover of each patient still waiting, newest first, with colour, time waited and target | none |
| `patient://{triage_id}/timeline` | resource | | the triage, its handover drafts and its audit rows | none |
| `report://shadow-agreement` | resource | | nurse-vs-Mizani agreement rate and every disagreement | none |
| `triage_intake` | prompt | `complaint` | intake instructions: every chart sign with its id, colour and, where the EMSSA manual defines it, its meaning; a quote from the complaint for each sign; never infer a diagnosis. The rules for picking a sign are the agent's own, from `src/mizani/prompts.py`, so the two cannot drift apart | none |
| `handover_summary` | prompt | `triage_id` | findings-only handover instructions | none |

Optional `_meta` on a tool call is copied into the audit row: `run_id` (one per patient run; links the checks made before the triage existed to that triage's timeline), and `model`, `tokens`, `tokens_in`, `tokens_out`, `cost` (which model asked for the call, what it read and wrote, and what it cost; a value that is not a whole number of 0 or more is stored as empty). Refused or failed calls get `error: true`.

## Depends on / used by
- Depends on: the rules engine, `schemas.py`, `approval.py`, `audit.py`, SQLite, `mcp`.
- Used by: the LangGraph agent (MCP Python SDK client, see agent.md), and MCP Inspector for manual testing.

## Safety rules this system enforces
- No commit, handover release or called-in record without a signed-in person whose role may do it, each with a token for that purpose only: commit and handover release by a nurse, called in by a nurse or a doctor. `commit_triage` refuses a missing, forged, expired, other-triage or reused token, a token from a doctor or an admin, and a token whose account was deactivated or changed role after it was signed. Tests prove each refusal.
- No unsourced output: every tool result carries sources (the schemas reject output without them).
- Only chart entries: unknown discriminators are refused with a flag.
- Words behind every sign: a sign needs a quote that is in the complaint and outside text addressed to the software; a red colour from the complaint alone carries a `confirm_sign` flag for the nurse.
- Handover numbers from the record: `draft_handover` refuses a number that is not in the patient's record, and a waiting time that is not the real one.
- Patient text is data: the complaint is stored as-is, and the intake prompt escapes it inside its tags (so it can't close them) and tells the model to ignore instructions inside it. Patient label (≤ 80), complaint (≤ 2,000 characters) and discriminator list (≤ 20) are capped.
- The container runs as an unprivileged user.
- It answers only to the names in `MIZANI_ALLOWED_HOSTS`. The MCP SDK guards the `Host` and `Origin` headers by itself only when it listens on 127.0.0.1; in Docker it listens on 0.0.0.0, so `main()` always passes the names. A web page on another site cannot reach it through a browser on this machine by pointing its own domain at 127.0.0.1 (DNS rebinding).
- Only patients on the waiting list: a re-triage, a handover draft or release, a re-check flag and called in are refused for a patient who was re-triaged or called in meanwhile (`store.waiting`, and the queue for the re-check flag). A re-check proposal still pending when its patient is called in can no longer be committed.
- Read-only queue: the order comes from committed colours and arrival times; no tool reorders it. A patient leaves it only when a signed-in nurse or doctor records, with their own token, that the patient was called in (`record_called_in`). The agent never calls that tool.
- Every call logged: inputs, outputs, time, approver, model, tokens, latency, cost.

## Running and testing it alone
```
uv run pytest tests/test_mcp_server.py
uv run mizani-mcp                                       # http://127.0.0.1:8000/mcp
npx @modelcontextprotocol/inspector                     # connect to that URL, call tools by hand
docker compose up mizani-mcp                            # same server in Docker
```

## Known limitations
- One SQLite file; fine for one desk, not for many hospitals (Postgres later).
- Once a patient is recorded as called in, they can no longer get a handover draft or a re-check, so draft the handover first. The FHIR Encounter is not updated at that point.

## Extending it
A new tool is one decorated function in `mcp_server.py` that calls the engine or the store, plus a test through the in-memory client. It is audited automatically. If it changes what the agent or the page builds on, update agent.md, api.md and `web/src/types.ts` in the same change.
