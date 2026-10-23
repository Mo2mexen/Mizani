# Web page (the nurse's screen)

The page a triage nurse works in: enter a patient, watch the agent work, read the proposal with the rule behind every number, approve or override it under their own name, and keep an eye on the waiting room. It only shows what the API sends; it never computes, changes or reorders a colour.

## Where it lives
- Code: `web/` (React 19, Vite, TypeScript); all API calls in `web/src/api.ts`, shapes in `web/src/types.ts`
- Served by: the API at `/` from `web/dist` (`MIZANI_WEB_DIR`); the Dockerfile builds it in a Node 22 stage, so `docker compose up` serves it at `http://127.0.0.1:8090`
- Tests: `npm run build` (type check and build, also in CI); `tests/test_api.py` checks the API serves the page and still answers under `/api`
- Settings: `VITE_USE_MOCK=true` in `web/.env.local` answers every call from `web/src/mock/` (fake values, marked as such)

## How it works
The numbers are the nurse's steps: 1 intake and 2 proposal side by side (the proposal is the widest column), 3 the waiting room below. The agent's own steps ("How Mizani got there") come after the waiting room, in a section the nurse can hide; the choice is kept in this browser, and while the agent works the proposal panel says which step it is on. On a phone everything stacks in the same order. "Next patient" empties the intake and puts the cursor in the patient label; the entry stays on screen until then. Several re-measure flags show as one line naming every field to measure. The Details button on a card shows what was recorded, read-only from `GET /api/queue/{triage_id}`: the complaint, the measured vitals and those not measured, the TEWS total, the chart signs with the patient's words, and the decision (approved, or overridden with the reason, by whom, when). A search box over the waiting room filters cards by patient label (it hides, never reorders); a long colour column scrolls on its own, so no patient is ever on a second page. The audit drawer draws the newest 100 calls, with "Show 100 more". Times are 12-hour everywhere ("3:13:48 PM"). Patient label and complaint are marked required; the vitals hint says they are optional. While the agent works, a green breathing dot (still when the system asks for reduced motion) and the current step show it is alive. Fields in the vitals grid share rows (CSS subgrid), so labels, inputs and notes line up whatever wraps. The page uses system fonts only, so it works offline. The logo (a drawn balance scale, `web/public/favicon.svg`; *mizani* means scale) is the browser tab icon and sits before the name in the header. A white "?" next to Shadow mode explains it on hover or keyboard focus (you choose first; for training, or to see how often you and Mizani agree). A card's Details, Re-check and Handover buttons stay pressed while their panel is open; the handover panel's button is "Draft handover with AI". Look and behaviour follow docs/DESIGN.md.
1. **Intake.** Patient label, complaint (free text, `dir="auto"` for Arabic) and the vitals. Every vital is optional; an empty one means "not measured" and the backend asks for a re-measure. SpO₂ is labelled as not scored. Submit (or Enter) calls `POST /api/intake` and gets a run id; the button waits until the API has answered once.
2. **Proposal.** While the agent works, the panel shows how long it has taken, and after 20 s that the model may still be loading (the first patient after a start can take a minute or two). Then: the colour, big, with its name, label and the sourced reason; a red "nurse to confirm" box when a red colour rests on the complaint alone (`confirm_sign`); the complaint; the TEWS table (each vital, its points and the rule); the chart signs, each with the complaint's words behind it (`quote`) and its source; and the flags, each with its source. Shadow mode first asks the nurse for their own colour (`POST /api/triage/{run_id}/assessment`) and only then shows Mizani's; until then the trace hides every step's input and output too. A dropped connection shows "lost the connection; retrying" and the stream resumes on its own; a run the API lost in a restart says so, and the nurse starts the intake again.
3. **Decision.** A nurse name is required before Approve or Override is enabled, and Enter in the name field approves. With a "nurse to confirm" box, Approve also needs the nurse to tick that they checked the sign with the patient; Override does not. An override needs a colour (from `/api/colours`) and a reason. "Enter re-measured vitals" highlights the fields the backend flagged and sends `POST /api/triage/{run_id}/vitals`; a new proposal follows on the same stream. Approve and override send the proposal's `triage_id`, so a decision made on a proposal that was re-scored in the meantime is refused ("the proposal changed"). Any refusal is shown with the server's reason. After saving, the panel shows who saved which colour and whether the FHIR record was written.
4. **Agent trace.** An `EventSource` on `/api/triage/{run_id}/stream` lists every model call and tool call as it happens: time, kind, name, latency, tokens for the model, and one readable line each for the input and the output (the signs and quotes the model asked for, the TEWS points, the signs the server applied or refused, the proposed colour and its reason, the commit and the FHIR ids). Click a row for the raw JSON.
5. **Waiting room.** One column per colour, in the rule pack's order, with the target time and its source; cards in the order the API sends (refreshed every 5 s): time waited, time since the last triage against the target time, and a "re-check due" badge from the re-triage watch. Each card can be re-checked with fresh vitals (`POST /api/queue/{triage_id}/retriage`, which starts a new run shown above; a double click starts one run) and can get a doctor handover: the model drafts it, the nurse reads it and releases it under their name.
6. **Audit drawer.** Every logged tool call, newest first (time, tool, approver, model, tokens, latency, cost, refused or not), whether the hash chain is intact, and the shadow-mode agreement. It takes focus when opened; Escape or Close returns focus to the button that opened it.

Accessibility: every input has a visible label (or a hidden one tied to it), every action is a button or a form reachable by keyboard with a visible focus ring, results are announced through one polite live region, errors use `role="alert"`, text meets WCAG AA contrast, and motion stops under `prefers-reduced-motion`.

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
- Nothing is saved without a named nurse: Approve, Override and handover release stay disabled until a name is typed, and the server checks it again. A red colour from the complaint alone also needs the nurse's tick before Approve.
- Every number shows its source: each TEWS point, chart sign, flag and target time prints the rule or input field it came from, and each sign from the complaint shows the words behind it.
- Patient text is data: the complaint, quotes and the handover are rendered as plain text, never as HTML, with `dir="auto"`.
- No approval token ever reaches the page; the commit step shows it as "(hidden)".
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
