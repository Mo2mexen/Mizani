# FHIR output (borrowed MCP server)

After a nurse commits a triage, it is written as standard FHIR R4 records to a local HAPI FHIR server through wso2's `fhir-mcp-server`, so other hospital systems can read it. This is the MCP server we borrowed rather than built.

## Where it lives
- Mapping and write: `src/mizani/fhir.py`
- Services: `hapi-fhir` and `fhir-mcp` in `docker-compose.yml`
- Tests: `tests/test_fhir.py` (mapping, no Docker); `tests/test_stack.py` (live stack, `MIZANI_E2E=1`)

## How it works
`write_triage(client, record)` takes a committed triage (from `patient://{id}/timeline`) and an MCP client connected to `fhir-mcp`, then calls wso2's `create` tool:
1. Patient: the synthetic label as identifier and name text, and nothing else.
2. Encounter: status `triaged`, class `EMER`, priority = the SATS colour and who approved it, period start = arrival time.
3. Observations, linked to both: respiratory rate 9279-1, heart rate 8867-4, blood pressure panel 85354-9 with systolic 8480-6, temperature 8310-5, SpO2 2708-6, glucose 15074-8 (LOINC, UCUM units, FHIR R4 vital-signs profile), plus AVPU, mobility, trauma, ketonuria and the TEWS total with every point's source, under the local code system `urn:mizani:sats-adult` (they have no standard code).

It returns the new FHIR ids. A pending triage is refused.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `write_triage(client, record)` | MCP client to `fhir-mcp`, committed triage record | `{Patient, Encounter, Observation[]}` ids | creates FHIR resources in HAPI |
| `fhir-mcp` (wso2 0.10.0) | MCP over streamable HTTP | tools `create`, `read`, `search`, `update`, `delete`, `get_capabilities` | reads/writes HAPI |
| `hapi-fhir` (v8.12.0-2) | FHIR REST | R4 resources | stores them (built-in database) |

Addresses: `http://fhir-mcp:8000/mcp/` inside compose, `http://127.0.0.1:8001/mcp/` and HAPI at `http://127.0.0.1:18080/fhir` from this machine only.

## Depends on / used by
- Depends on: wso2 `fhir-mcp-server` built from the pinned release commit `78d750ba` (0.10.0, Apache-2.0), HAPI FHIR, our triage records.
- Used by: the agent's commit step through `write_committed(triage_id, url)`, right after `commit_triage` succeeds (set `MIZANI_FHIR_MCP_URL`). It reads the record from `patient://{id}/timeline`, writes it, and adds a `fhir_create` row to the audit chain, since the call goes to the borrowed server and our middleware never sees it. A failure is reported in the trace and the audit, not raised: the triage is already committed.

## Safety rules this system enforces
- Only nurse-committed triages leave our server; pending proposals never reach FHIR.
- Synthetic data only: the Patient carries the test label and nothing else.
- Local only: both services listen on this machine; wso2 auth is off because HAPI is unreachable from outside.

## Running and testing it alone
```
docker compose up -d mizani-mcp fhir-mcp hapi-fhir      # HAPI needs about a minute to boot
MIZANI_E2E=1 uv run pytest tests/test_stack.py          # propose, approve, commit, write, read back
```
Browse the records at `http://127.0.0.1:18080/fhir/Encounter`.

## Known limitations
- wso2's server uses MCP SDK 1.x; a 2.x client must connect with `mode="legacy"` and the trailing-slash URL, or wso2's server errors on the first request and needs a restart. `write_triage` itself doesn't care which client is used.
- Records are created one by one, not as one FHIR transaction; a failure part-way leaves earlier resources in place.
- Re-triages create a new Encounter rather than updating the old one.
- On a cold start HAPI needs from about 20 s (M4) to a few minutes (slow disk) before it answers. Compose holds the `app` back until a FHIR search answers through `fhir-mcp`'s healthcheck (compose.md); run outside compose, the first write can still time out, and the step then shows the error while the triage stays saved.

## Extending it
Another resource (for example a Flag for re-checks) is one more builder in `fhir.py` and one more `create` call, with a test in `tests/test_fhir.py`.
