# Approval tokens

Proof that a signed-in person decided on one triage, and that their role may do it. `commit_triage` will not run without one, so the model can propose but never commit.

## Where it lives
- Code: `src/mizani/approval.py`
- Tests: `tests/test_mcp_server.py` (every refusal, the role of each purpose, an account deactivated or changed after signing), `tests/test_auth.py` (the generated secret)
- Setting: `MIZANI_APPROVAL_SECRET`, shared by the MCP server and the API; the model has no access to it. Unset (the default since 16 Nov 2026), each install makes its own on the first start: 64 random hex characters in `approval.secret` beside the database (`/data` in Docker, the volume both servers share), readable only by the server's user.

## How it works
1. The signed-in nurse presses Approve or Override in the page. The API validates a `NurseDecision` (an override needs a colour and a reason; `confirmed` says the nurse checked a "nurse to confirm" sign with the patient, and goes into the token; a typed name is refused).
2. The API calls `issue_approval(triage_id, account, decision, purpose)` for the account behind the request. The token holds the account (`account_id`, the full name as `approver`, the `role` and the `chief` flag), the decision, the triage id, its purpose, an expiry (2 minutes: the token is used about a second after the click) and a random nonce, signed with HMAC-SHA256. A token unlocks only its own purpose:

   | Purpose | Unlocks | Who may |
   |---|---|---|
   | `commit` | `commit_triage` | a nurse |
   | `handover` (with the `draft_id` read) | `release_handover` | a nurse |
   | `called_in` | `record_called_in` | a nurse or a doctor |
   | `chart` | activating a triage chart (item 4 of the production build) | the chief doctor |

3. The agent passes the token to `commit_triage`. `verify_approval` checks the signature, the triage id, the purpose and the expiry, then reads the account from the database as it is now: it must exist, be active, still have the role and name it had when the token was signed, and that role must be allowed for the purpose. The store then checks the triage is still pending and the nonce unused, and commits in one transaction.
4. `ensure_secret()` runs in `main()` of both servers. When the setting is empty it reads `approval.secret`, or writes a new one with an exclusive create and a hard link, so two servers starting together end up with the same secret.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `issue_approval` | triage id, `Account`, `NurseDecision` (default: approve), purpose (`commit` default, `handover` with `draft_id`, `called_in`, `chart`) | token string | none |
| `verify_approval` | token, triage id, database connection, purpose | signed payload, or `ApprovalError` with the reason | none |
| `ensure_secret` | | | sets `MIZANI_APPROVAL_SECRET` for this process; may create `approval.secret` |

## Depends on / used by
- Depends on: `auth.py` (the account as it is now), `schemas.NurseDecision` and `schemas.Account`, the standard library.
- Used by: `commit_triage`, `release_handover` and `record_called_in` (MCP server), the API endpoints that issue the token for each (the decision, the handover release, called in), and the evaluation runner's checks (its nurse is an account no one can sign in to).

## Safety rules this system enforces
- A signed-in nurse approves every committed priority; overrides carry a reason. The name on the record is the account's, never typed.
- The MCP server checks the role itself, so a request that reaches it some other way (another agent, a script) still cannot commit without a token from a nurse's account, and a doctor's or admin's token is refused with the reason.
- Deactivating an account, or changing its role, stops its tokens at once, even one signed a second before.
- A red colour that rests on the complaint alone (`confirm_sign`) is committed as proposed only with the nurse's confirmation in the token; otherwise the nurse overrides.
- Tokens cannot be forged without the secret, cannot move to another triage, purpose or draft, expire, and work once. A malformed token is refused like any other bad token.
- Tokens never sit in the audit log: the `approval_token` input is stored as `***`.
- Every install has its own secret unless `.env` sets one; the MCP server warns at start-up if it is the old public demo value.

## Running and testing it alone
```
uv run pytest tests/test_mcp_server.py -k "commit or called or handover"
uv run pytest tests/test_auth.py -k secret
```

## Known limitations
- Tokens live 2 minutes; a decision older than that must be made again.
- Someone who can read the data volume can read the secret, as they can read the database; the server is trusted (backup.md says how to keep the audit head elsewhere).

## Extending it
A new consequential action gets its own purpose: a line in `ALLOWED` and `WHAT`, the API endpoint that issues it for the signed-in account, and the MCP tool that verifies it, with a test of a wrong role.
