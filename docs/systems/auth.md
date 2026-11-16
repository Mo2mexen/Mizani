# Accounts and sign-in

Who may use Mizani and as what. Every person signs in with their own account; the account's role decides what the page shows and, on the server, what each request may do. The name on every saved decision is the account's, so a record always names the person who acted.

## Where it lives
- Code: `src/mizani/auth.py` (accounts, passwords, sessions, the first admin), `src/mizani/admin.py` (command `mizani-admin`), the endpoints and the role check in `src/mizani/api.py` (api.md)
- Tables: `account` and `session` in the triage database (`db.py`)
- Tests: `tests/test_auth.py` (passwords, lockout, sessions, the admin's rules, the first admin, `mizani-admin`, the generated secret, and every endpoint called as nobody, nurse, doctor, chief doctor and admin)
- Page: `web/src/components/SignIn.tsx`, `AccountMenu.tsx`, `AdminUsers.tsx`, `DoctorView.tsx`, `web/src/session.ts` (web.md)

## How it works
1. **Roles.** A nurse does the triage desk (intake, approve or override, re-check, handover, called in). A doctor sees the waiting room in priority order and the handovers nurses released, and records "Seeing this patient" (called in); a doctor never changes a colour or approves a triage. An admin manages accounts and settings and does nothing clinical. The chief flag is given by an admin to a doctor: the chief doctor also gets the Charts page (charts arrive in item 4 of the production build). Everyone can read the audit log.
2. **No signup.** An admin adds a person (full name, user name, role). The server makes a one-time password, shows it to the admin once, and keeps only its hash. At the first sign-in the person must choose their own before anything else works (the API answers 403 "Choose your own password first"). Passwords are at least 10 characters and must not contain the user name.
3. **Passwords** are hashed with `hashlib.scrypt` (N = 2^14, r = 8, p = 5: one of OWASP's equivalent settings, 16 MB and about 0.2 s per check), a random salt per hash, and compared with `hmac.compare_digest`. The cost is stored in the hash, so it can be raised later without breaking old passwords.
4. **Sign-in.** A wrong user name and a wrong password get the same answer ("Wrong user name or password"), and an unknown name is checked against a spare hash so it takes the same time. Five wrong passwords in a row lock the account for 15 minutes (an admin's reset lifts the lock). Each miss is counted in one database statement, so guesses sent at the same moment all count, and a right password whose check ends after the lock was set is refused too. The password check (scrypt, slow on purpose) runs on four threads of its own, so a burst of sign-ins cannot take the threads every other request needs. A wrong current password in Change password counts the same way, and its fifth also ends every session of the account, so a tablet left signed in cannot be used to guess the password. A deactivated account is refused whatever the password, so the answer never shows whether a guess was right.
5. **Sessions.** A successful sign-in makes a random 32-byte value, sent as the cookie `mizani_session` (HttpOnly, SameSite=Strict, Secure over HTTPS). The database keeps only its SHA-256, so a copy of the database cannot be used to sign in. A session ends after 15 minutes without use or 12 hours after sign-in, whichever comes first, and when the person signs out, changes their password elsewhere, or is reset, signed out or deactivated by an admin. Ended sessions, and any older than 12 hours, are deleted at the next sign-in, so the table stays small. The page's own polling (`X-Mizani-Background: 1`) does not count as use; a key press, click or touch does, and the page tells the server at most once a minute while someone works. The page also signs out by itself after 15 idle minutes and has a one-click "Switch user" for a shared tablet. The open patient stays in the page's address, so signing in again brings it back.
6. **Accounts are never deleted.** An admin deactivates them; the name stays on every record and audit row. Full names are unique (case ignored), so "approved by" always names one person. An admin cannot change their own role or deactivate themselves, and the last active admin cannot be removed (checked in the same statement as the change, so two admins acting on each other at once cannot both succeed).
7. **The first admin.** On a start when no admin has chosen their own password yet, `mizani-api` makes sure the account `admin` (Administrator) exists with a new one-time password and prints it in a box in its log (`docker compose logs app`). Every such start prints a new one and the old one stops working, so a lost log line costs only a restart. Once an admin has chosen a password, nothing is printed again. The first start also stays out of the way of anything set up by hand: when another admin account exists (made with `mizani-admin` or in the page), or when `admin` was reset, deactivated or given another role, it prints nothing and changes nothing (`password_by` records who set each one-time password). For hospitals that prefer a command, and when every admin is locked out: `docker compose run --rm --no-deps app mizani-admin create-admin <user name> "<full name>"` or `... mizani-admin reset-password <user name>`. No web page can create an admin.
8. **Audit.** Every sign-in, refused sign-in (with the reason: wrong password, locked, deactivated; an unknown name is not stored, since it may be a password typed in the wrong box), sign-out, password change, account creation, reset, role change, chief flag given or removed, deactivation, reactivation and "signed out everywhere" is a row in the hash-chained audit log, under the person who did it (`mizani-admin (server console)` for the command). A password, a one-time password or a session value never is.
9. **Approvals.** The approval token is issued for the signed-in account and its role, and the MCP server checks the role for each purpose against the account as it is now (approval.md).

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `create_account(con, actor, username, full_name, role)` | | `(Account, one-time password)` | account row; audit row |
| `add_account(con, username, full_name, role, password_hash="!")` | | `Account` | account row; "!" can never be signed in to (the evaluation's nurse, tests) |
| `sign_in(con, username, password)` | | `(session value, Account)`; `AuthError` (401, 403, 429) | session row, failure count or lock; audit row |
| `session_account(con, value, touch)` | | `Account` or None | ends a timed-out session; `touch` counts as use |
| `change_password`, `reset_password`, `set_role`, `set_chief`, `set_active`, `sign_out_everywhere`, `sign_out` | | `Account` or a one-time password | sessions end where described above; audit row |
| `first_start(con)` | | the first admin's one-time password, or None | account row; audit row |
| `mizani-admin create-admin <user> [<full name>]`, `reset-password <user>` | | prints the one-time password | as above |
| API endpoints | | | `/api/auth/*` and `/api/admin/users*` (api.md) |

## Depends on / used by
- Depends on: the standard library (`hashlib`, `hmac`, `secrets`), `audit.py`, `db.py`, `schemas.Account`.
- Used by: the API (every endpoint's role check), `approval.py` (the account behind a token), the evaluation runner (its nurse), the page.

## Safety rules this system enforces
- A named human approves every committed priority: the name is the signed-in account's, never typed.
- Every endpoint checks the role on the server; the page only hides what a role cannot use.
- Requests that change something need `X-Mizani: 1` and the SameSite=Strict cookie, so a page on another site cannot act for a signed-in nurse.
- Every account event is audited; no secret is.

## Running and testing it alone
```
uv run pytest tests/test_auth.py
uv run mizani-admin create-admin it.lead "IT Lead"     # outside Docker, on .cache/data/mizani.db
```

## Known limitations
- Accounts are local to the Mizani server: no link to a hospital directory (LDAP, Active Directory, Keycloak) yet.
- No second factor; the hospital network and HTTPS (item 2 of the production build) are the outer wall.
- The 15-minute idle limit and the 5-try lock are fixed in the code (`auth.IDLE`, `MAX_MISSES`, `LOCKED_FOR`; the page's `IDLE_MINUTES` must match `IDLE`).
- A one-time password does not expire by itself; the admin can reset it.
- A locked account says it is locked, so five tries of a name show whether it exists. User names are not secret in Mizani (screens and the audit log show them), and a nurse who cannot sign in needs to know why.
- Anyone who can reach the sign-in page can lock an account for 15 minutes with five wrong passwords; the lock is per account, not per device. An admin's reset lifts it at once (`mizani-admin reset-password` from the console for an admin), and every refused sign-in is in the audit log. The same flood also adds a row to the audit log per try, with no limit. Keep the HTTPS port on the hospital network only (INSTALL.md).

## Extending it
A new role: add it to `ROLES` in `auth.py`, the `role` check in `db.py`, `Role` in `schemas.py` and `web/src/types.ts`, the role names in `api.py` and `session.ts`, its screens in `App.tsx`, and a column in `ENDPOINTS` in `tests/test_auth.py`. A database created before the new role keeps the old check on its `account` table, so it needs a migration.
