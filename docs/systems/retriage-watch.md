# Re-triage watch

Watches the waiting queue against the SATS target times and tells the nurse when a patient is due a re-check. It can raise a flag and propose a new colour from fresh vitals; it never changes a colour or the queue by itself.

## Where it lives
- Timers and flags: `queue`, `raise_recheck` in `src/mizani/store.py`
- Tools: `raise_recheck_flag`, and `propose_priority(..., retriage_of=...)` in `src/mizani/mcp_server.py`
- Tests: `tests/test_mcp_server.py` (`recheck`, `retriage`)

## How it works
1. Timer: for every committed patient still waiting, the queue (`queue://current`) reports `minutes_since_triage` and `recheck_due`, which turns true once that time passes the target time to be seen for their colour: orange 10 min, yellow 60, green 240, red immediately (SATS training manual p6, Table 3, read from the rule pack).
2. Flag: the agent's watch step reads the queue and calls `raise_recheck_flag(triage_id)` for patients that are due and not yet flagged. The server checks the timer itself and refuses if it hasn't run out. The flag cites the target-time rule, and the queue shows `recheck_raised`.
3. Re-score: after the nurse takes fresh vitals, the agent calls `propose_priority` with `retriage_of` set to the patient's current triage. The engine scores the new vitals; the proposal is pending and carries a `retriage` flag saying whether the colour changed. The patient keeps their original arrival time.
4. Nurse decides: only when a nurse commits the new proposal (approval token as usual) does it replace the old entry in the queue (`superseded_by`).

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `queue://current` | | waiting patients with `waited_minutes`, `minutes_since_triage`, `target_minutes`, `recheck_due`, `recheck_raised` | none |
| `raise_recheck_flag` | `triage_id` | `recheck_due` flag with its source | records the flag time; audit row |
| `propose_priority` | ... plus `retriage_of` | pending proposal with a `retriage` flag | new triage row linked to the old one; audit row |

## Depends on / used by
- Depends on: the rule pack's target times, the store, the rules engine, the approval gate.
- Used by: the agent's watch (`src/mizani/agent/watch.py`, run by the API every 30 s), the API's `POST /api/queue/{triage_id}/retriage` (a re-check run of the LangGraph flow, agent.md and api.md), and the UI queue board (`recheck_due` badge).

## Safety rules this system enforces
- The agent may raise a deterioration flag but can't act on it: it changes no colour and moves no patient.
- A flag is only raised when the timer has really run out, and it cites the SATS rule.
- A new colour needs a named nurse's approval like any other triage.

## Running and testing it alone
```
uv run pytest tests/test_mcp_server.py -k "recheck or retriage"
```

## Known limitations
- The timer measures time since the last triage. It doesn't know when a doctor actually saw the patient; a "seen" step would close that gap.
- Polling is the agent's job; the server has no background timer of its own.

## Extending it
A different re-check policy (for example a fixed interval per colour) changes `queue` in `store.py` and its tests; the tools stay the same.
