# Charts (the chief doctor's triage chart)

How a hospital changes the triage chart Mizani scores with, safely. The chief doctor uploads a chart (a YAML rule pack) with test patients; Mizani checks it at once, evaluates it on the real model, and lets the chief doctor put it in use, with a signed approval, only when every safety check held. The chart it replaced is kept for a one-click way back, and every triage records the chart that scored it.

## Where it lives
- Code: `src/mizani/charts.py` (upload, the chart in use, activation), `src/mizani/rules/pack.py` (`parse_pack` and the checks every chart must pass), `src/mizani/evals/chart.py` (the evaluation, a process of its own), the `activate_chart` tool in `src/mizani/mcp_server.py`, the endpoints in `src/mizani/api.py`
- Table: `chart` in the triage database; the chart that scored a triage is in `triage.chart`
- Page: `web/src/components/Charts.tsx` (the chief doctor's Charts tab)
- Tests: `tests/test_charts.py` (the checks, the upload, the evaluation with a fake model, activation and going back through the MCP server and the API)

## How it works
1. **The chart in use.** Until the chief doctor activates one, it is the built-in pack named by `MIZANI_RULE_PACK` (default `sats_adult`). After that, it is the activated chart. The MCP server reads which chart is in use on every tool call (one small query) and parses it again only when it changed, so an activation takes effect at once in both servers.
2. **Upload.** The chief doctor uploads a chart file and, optionally, a test-patients file (the format of `evals/cases/core.yaml`, at most 60 patients). `parse_pack` checks the chart at once: safe YAML only, every clinical rule citing one of the chart's own references, sign ids unique, the TEWS-to-colour list from the highest total down to 0, every plausible vital value in exactly one band of its row, every coded value (AVPU, mobility, trauma) scored, and the four investigations the engine raises. The test patients must use the chart's own colours and sign ids. A refused upload says what and where. An accepted one gets the next version number for its id (`sats_kenya, version 1`, then 2) and scores nobody yet.
3. **Evaluation.** "Run evaluation" starts `python -m mizani.evals.chart <id>` in a process of its own (each run gets a fresh database through `MIZANI_DB`, which a process can only change for itself). It uses the model the desk uses (Admin › Model), so the page warns that triage slows while it runs and suggests off-hours or another model server; one evaluation runs at a time. Every test patient runs 5 times through the real agent with the uploaded chart. Progress ("12 of 17 runs") shows on the page.
4. **The report.**
   - With test patients: the pass rate against their expected answers, and per patient the passes, the most common colour and the failed checks.
   - Without them: the built-in evaluation patients run, and the report gives only the agreement with the chart in use: agreement, never accuracy.
   - Either way: the patients whose colour changes against the chart in use (the same vitals and the same signs the model named, scored by both charts).
   - Safety checks, which must all hold before activation: every rule cites a reference and the engine can run the chart; nothing is saved without a nurse's approval token; an order hidden in a complaint sets no colour; no diagnosis or treatment in the model's text; every test patient expected at the most urgent colour gets it in every run (not checked when no test patient expects it).
5. **Activation.** "Activate" makes the API sign a token with purpose `chart` for the signed-in chief doctor, and call the MCP tool `activate_chart`, which checks the token (the chief flag, as the account is now), uses it once, refuses a chart whose evaluation did not hold every safety check, and refuses a chart without the colour of a patient still waiting. The chart in use before is kept (`previous`), and the API reloads the chart's signs, colours and name. Patients already waiting keep their colour.
6. **Going back** is the same activation, of the previous chart (an uploaded one, or the built-in one), from one button.
7. **On every patient.** `propose_priority` scores with the chart in use, writes its name on the proposal (`chart`, shown to the nurse as "Chart: sats_kenya, version 1") and on the triage record (`triage.chart`). The FHIR Encounter names the chart in use when it is written.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `GET /api/charts` (chief) | | the chart in use, the one before it, every upload with its state | none |
| `POST /api/charts` (chief) | `{chart_yaml, cases_yaml?}` | 201 the chart; 422 with what was refused | stored; audit `chart_uploaded` |
| `GET /api/charts/{id}` (chief) | | the chart with its report | none |
| `POST /api/charts/{id}/evaluate` (chief) | | 202; 409 while another evaluation runs or for a chart that was in use | a process of its own; audit `chart_evaluation_started`, then `chart_evaluated` |
| `POST /api/charts/{id}/activate`, `POST /api/charts/rollback` (chief) | | `{in_use, previous, approver, overview}`; 409 with the server's reason | MCP `activate_chart` (audited, with the chief doctor as approver) |
| `activate_chart` (MCP tool, gated) | `chart` (an id or `built-in`), `approval_token` (purpose `chart`) | `{in_use, previous, approver}` | the chart in use changes; token used |

## Depends on / used by
- Depends on: the rules engine and `pack.py`, the evaluation runner (`evals/runner.py`), the approval tokens (`approval.py`, purpose `chart` for the chief doctor), the model settings (`settings.py`), the audit log.
- Used by: the MCP server (every tool scores with the chart in use), the API (the agent's chart signs, the page's colours, the FHIR record), the page.

## Safety rules this system enforces
- Every value cites its rule: a chart with an uncited rule never loads, uploaded or built in.
- A chart cannot crash the engine at the desk: the checks of step 2 run on every chart before it is stored.
- Nothing changes the chart without the chief doctor's approval token, checked by the MCP server; a doctor without the flag, a nurse or an admin is refused.
- No chart goes into use with a failed safety check, or without the colour of a patient still waiting.
- The model never sees the chart file; it is told the chart's signs, as always, and the engine sets the colour.

## Running and testing it alone
```
uv run pytest tests/test_charts.py
uv run python -m mizani.evals.chart <chart id>    # with MIZANI_DB pointing at the database that holds it
```

## Known limitations
- The evaluation shares the desk's model; on a laptop with a 4 GB GPU a run takes about 15 s, so 39 built-in patients take about 50 minutes.
- An evaluation that is running when the server restarts is marked as stopped; run it again.
- The test patients are the chief doctor's own; Mizani checks their format, not whether their expected answers are clinically right.
- One chart in use for the whole hospital (no chart per department yet).

## Extending it
A new safety check is one entry in `safety` in `evals/chart.py`, with a test in `tests/test_charts.py`. A new structural check every chart must pass goes in `RulePack.the_engine_can_run_it` (`pack.py`), with a case in `test_an_uploaded_chart_is_checked_for_what_the_engine_needs`.
