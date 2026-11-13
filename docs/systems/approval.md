# Approval tokens

Proof that a named nurse approved or overrode one triage. `commit_triage` will not run without one, so the model can propose but never commit.

## Where it lives
- Code: `src/mizani/approval.py`
- Tests: `tests/test_mcp_server.py` (every refusal)
- Setting: `MIZANI_APPROVAL_SECRET`, shared by the MCP server and the API; the model has no access to it

## How it works
1. The nurse presses Approve or Override in the UI. The API validates a `NurseDecision` (a name is required; an override needs a colour and a reason; `confirmed` says the nurse checked a "nurse to confirm" sign with the patient, and goes into the token).
2. The API calls `issue_approval(triage_id, decision)`. The token is the decision, the triage id, its purpose (`commit`; `handover` for releasing a handover draft, which must also name the `draft_id` the nurse read; or `called_in` for taking a patient off the waiting list), an expiry (2 minutes: the token is used about a second after the nurse clicks) and a random nonce, signed with HMAC-SHA256. A token unlocks only its own purpose.
3. The agent passes the token to `commit_triage`. `verify_approval` checks the signature, the triage id, the expiry and the approver name; the store then checks the triage is still pending and the nonce unused, and commits in one transaction.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `issue_approval` | triage id, `NurseDecision`, purpose (`commit` default, `handover` with `draft_id`, or `called_in`) | token string | none |
| `verify_approval` | token, triage id, purpose | signed payload, or `ApprovalError` with the reason | none |

## Depends on / used by
- Depends on: `schemas.NurseDecision`, the standard library.
- Used by: `commit_triage`, `release_handover` and `record_called_in` (MCP server), and the API endpoints that issue the token for each: the decision, the handover release and called in.

## Safety rules this system enforces
- A named human approves every committed priority; overrides carry a reason.
- A red colour that rests on the complaint alone (`confirm_sign`) is committed as proposed only with the nurse's confirmation in the token; otherwise the nurse overrides.
- Tokens cannot be forged without the secret, cannot move to another triage, purpose or draft, expire, and work once. A malformed token is refused like any other bad token.
- Tokens never sit in the audit log: the `approval_token` input is stored as `***`.
- The server prints a warning at start-up if the secret is unset or the public demo value.

## Running and testing it alone
```
uv run pytest tests/test_mcp_server.py -k commit
```

## Known limitations
- The nurse's name is typed, not authenticated against a staff directory (no login in this build).
- Tokens live 2 minutes; a decision older than that must be made again.

## Extending it
Staff login would put the authenticated user id into the payload in place of the typed name; nothing else changes.
