# Handover gate and shadow mode

Two features on the server side. The handover is a short note for the doctor, drafted by the agent, that reaches the doctor once a nurse releases it. Shadow mode records the colour a nurse picks on her own, before seeing Mizani's, so we can measure how often they agree.

## Where it lives
- Tools and resource: `draft_handover`, `release_handover`, `record_nurse_assessment`, `report://shadow-agreement` in `src/mizani/mcp_server.py`
- Storage: tables `handover` and `nurse_assessment`; functions in `src/mizani/store.py`
- Token purpose: `issue_approval(..., purpose="handover")` in `src/mizani/approval.py`
- Tests: `tests/test_mcp_server.py` (`handover`, `shadow`)

## How it works
### Handover
1. After a triage is committed, the agent reads `patient://{id}/timeline`, follows the `handover_summary` prompt, and calls `draft_handover(triage_id, text)`. The server accepts 1 to 3 lines, at most 600 characters, and only for a committed triage. The draft is stored unreleased.
2. The nurse reads the draft in the UI and presses Release. The API issues a token with `purpose="handover"` and the `draft_id` she read.
3. The agent calls `release_handover(triage_id, draft_id, token)`. The server checks the signature, triage, expiry, approver name, purpose (a commit token is refused, and a handover token can't commit) and that the token names this exact draft, so a newer draft can't ride on her approval of an older one. It refuses drafts for a triage a re-triage has replaced, marks the draft released by that nurse, and uses the token up.

### Shadow mode
Before the UI shows Mizani's colour, the nurse picks her own. `record_nurse_assessment(triage_id, nurse_name, colour, reason)` stores it once per triage; it never changes the proposal or the queue. `report://shadow-agreement` returns the number assessed, the number that agreed, the agreement rate, and every disagreement with Mizani's cited reason and the nurse's.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `draft_handover` | `triage_id`, `text` | draft id, `awaiting_release` | stores the draft; audit row |
| `release_handover` | `triage_id`, `draft_id`, `approval_token` (purpose handover, naming that draft) | released text, approver, time | marks it released, uses the token; audit row with approver |
| `record_nurse_assessment` | `triage_id`, `nurse_name`, `colour`, optional `reason` | stored assessment | one row per triage; audit row |
| `report://shadow-agreement` | | `assessed`, `agreed`, `agreement_rate`, `disagreements[]` | none |

## Depends on / used by
- Depends on: the store, the approval tokens, the rule pack's colour list.
- Used by: John's agent (W2 handover step, W3 shadow flow) and API, the UI's handover view and agreement report.

## Safety rules this system enforces
- Consequential release needs a named nurse: no handover reaches the doctor without a handover-purpose approval token.
- Findings only: the prompt asks for findings and forbids diagnosis or treatment advice; the server caps the size, and the nurse reads it before release.
- Shadow mode observes; it never moves a patient or changes a colour.

## Running and testing it alone
```
uv run pytest tests/test_mcp_server.py -k "handover or shadow"
```

## Known limitations
- The server can't tell a finding from a diagnosis in free text; that rests on the prompt, the evals and the nurse's review before release.
- The agreement report counts exact colour matches; it doesn't weigh how far apart the colours are.

## Extending it
A weighted agreement (for example, over-triage vs under-triage counts) is a change to `shadow_agreement` in `store.py` and its test.
