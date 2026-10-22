# Web page (the nurse's screen)

The page a triage nurse works in: enter a patient, watch the agent work, read the proposal with the rule behind every number, approve or override it under their own name, and keep an eye on the waiting room. It only shows what the API sends; it never computes, changes or reorders a colour.

## Where it lives
- Code: `web/` (React 19, Vite, TypeScript); all API calls in `web/src/api.ts`, shapes in `web/src/types.ts`
- Served by: the API at `/` from `web/dist` (`MIZANI_WEB_DIR`); the Dockerfile builds it in a Node 22 stage, so `docker compose up` serves it at `http://127.0.0.1:8090`
- Tests: `npm run build` (type check and build, also in CI); `tests/test_api.py` checks the API serves the page and still answers under `/api`
- Settings: `VITE_USE_MOCK=true` in `web/.env.local` answers every call from `web/src/mock/` (fake values, marked as such)

## How it works
1. **Intake.** Patient label, complaint (free text, `dir="auto"` for Arabic) and the vitals. Every vital is optional; an empty one means "not measured" and the backend asks for a re-measure. SpO₂ is labelled as not scored. Submit calls `POST /api/intake` and gets a run id.
2. **Agent trace.** An `EventSource` on `/api/triage/{run_id}/stream` lists every model call and tool call as it happens: time, kind, name, inputs, outputs, tokens and latency. Click a row for the full inputs and outputs.
3. **Proposal.** The colour, big and with its name and label, and the sourced reason it came from; the TEWS table (each vital, its points and the rule); the chart signs and flags, each with its source. Shadow mode first asks the nurse for their own colour (`POST /api/triage/{run_id}/assessment`) and only then shows Mizani's; until then the trace hides every step's output too. Approve and override send the proposal's `triage_id`, so a decision made on a proposal that was re-scored in the meantime is refused.
4. **Decision.** A nurse name is required before Approve or Override is enabled; an override also needs a colour (from `/api/colours`) and a reason. "Enter re-measured vitals" highlights the fields the backend flagged and sends `POST /api/triage/{run_id}/vitals`; a new proposal follows on the same stream. A refused commit (for example an override colour not on the chart) is shown with the server's reason.
5. **Waiting room.** One column per colour, in the rule pack's order, cards in the order the API sends (refreshed every 5 s): time waited, time since the last triage against the target time, and a "re-check due" badge from the re-triage watch. Each card can be re-checked with fresh vitals (`POST /api/queue/{triage_id}/retriage`, which starts a new run shown in the trace) and can get a doctor handover: the model drafts it, the nurse reads it and releases it under their name.
6. **Audit drawer.** Every logged tool call, newest first (time, tool, approver, model, tokens, latency, cost, refused or not), whether the hash chain is intact, and the shadow-mode agreement.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `http://127.0.0.1:8090/` | browser | the page | calls the API below |
| API calls | see docs/systems/api.md | | intake, decisions, re-measures, re-triage, handover, shadow assessment |

## Depends on / used by
- Depends on: the API (api.md) and its event stream; nothing else, no fonts or scripts from the internet.
- Used by: the triage nurse at the desk; the demo video.

## Safety rules this system enforces
- The page never decides: colours, target times, the colour list and the queue order come from the API; there is no drag and drop.
- Nothing is saved without a named nurse: Approve, Override and handover release stay disabled until a name is typed, and the server checks it again.
- Every number shows its source: each TEWS point, chart sign and flag prints the rule or input field it came from.
- Patient text is data: the complaint and the handover are rendered as plain text, never as HTML.
- Colour is never the only cue: every colour is printed with its name.

## Running and testing it alone
```
cd web && npm ci
VITE_USE_MOCK=true npm run dev           # http://localhost:5173 with fake data, no backend
npm run dev                              # same page against the API on 127.0.0.1:8090 (Vite proxies /api)
npm run build                            # type check + build into web/dist; `uv run mizani-api` then serves it at /
```

## Known limitations
- No login: the nurse types their name (approval.md).
- The queue refreshes by polling every 5 s, not by a push stream.
- Handover drafts are only shown for the current session; the record view (`GET /api/queue/{triage_id}`) has the history.
- English interface only; complaints and handovers can be Arabic and display right to left.

## Extending it
A new screen is a component in `web/src/components/` with its calls added to `web/src/api.ts` and `web/src/mock/`. If it needs a new API shape, change the API first and record it in the API contract.
