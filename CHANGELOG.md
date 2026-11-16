# Changelog

What changed in each version of Mizani. The version is in `pyproject.toml`; the page shows it in its footer and `GET /api/health` returns it. To update an installation, see [docs/INSTALL.md](docs/INSTALL.md), section 7.

## 1.0.0 (16 November 2026)

The production release: Mizani as software a hospital runs on its own server, for nurses and doctors on tablets and desk PCs.

**Accounts and roles**
- Everyone signs in with their own account; there is no signup. An admin adds each person with a one-time password, which they replace at their first sign-in.
- Roles: nurse (the triage desk), doctor (the waiting room by priority, the handovers nurses release, "Seeing this patient"), admin (accounts and settings, nothing clinical), and a chief flag an admin gives one doctor.
- Passwords hashed with scrypt; five wrong passwords lock an account for 15 minutes; a page signs out after 15 minutes without use, and "Switch user" hands a shared tablet over in one click.
- The API checks the role on every request, and the MCP server checks it again behind every approval. The name on every decision is the signed-in person's; nothing is typed.
- The first admin's one-time password is printed in the server log on the first start; `mizani-admin` creates an admin or resets a password from the server's console.
- Every sign-in, refused sign-in and account change is in the hash-chained audit log.
- Each installation makes its own approval secret when `.env` sets none.

**Tablets and the hospital network**
- HTTPS through Caddy, on the server only until `.env` opens it to the network under the hospital's name; Caddy's own certificate authority, or the hospital's certificate.
- [docs/INSTALL.md](docs/INSTALL.md) for hospital IT.

**Where the model runs**
- Admin › Model: this server, a GPU server in the hospital, or a cloud GPU server, with a connection test and no restart. The API key is never shown again; a cloud server needs `https://` and an explicit confirmation that complaint text leaves the hospital.

**The chief doctor's charts**
- Upload a triage chart (a YAML rule pack) with test patients. Mizani checks it, evaluates it on the real model with safety checks that must all hold, and the chief doctor activates it with a signed approval; one click goes back. Every proposal and record names the chart that scored it.
- Every chart, the built-in one included, is checked for what the engine relies on before it scores anyone.
- A triage is saved only under the chart that scored it: if the chart changed while the proposal waited, the nurse re-measures for a new one. A re-check after a chart change tells the nurse to check the complaint against the new chart by hand.
- If an update makes a chart in use unloadable, Mizani takes it out of use with the reason and scores with the built-in chart; a waiting patient whose colour that chart lacks is shown in its own column, never hidden.

**Also**
- `/api/health` and the page show the version.
- README: "Known limitations" cut to five points; "Later" removed.

Patients, the waiting room and the audit log of an earlier version are kept: the new tables and columns are added on the first start. After the update, sign in as `admin` with the one-time password from `docker compose logs app`, and add the nurses and doctors.

## 0.1.0 (23 October 2026)

The version submitted to the African Agentic AI Design Challenge: the SATS rules engine with a source on every point, our MCP server with the approval gate and the audit chain, the LangGraph agent on a local model, the nurse's page, FHIR records through wso2's server, the re-check watch, the handover and shadow mode, and the evaluation suite. It and the improvements made up to 16 November (tokens per model call, health checks, the chart chosen by a setting, backups) had no login: the nurse typed a name.
