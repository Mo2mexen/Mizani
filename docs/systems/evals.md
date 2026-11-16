# Evals

Runs Mizani's agent against a fixed set of synthetic triage tasks many times with the real model, and reports how often each task passes, how much the answers vary from run to run, and what each run costs. The results are in [EVALS.md](../EVALS.md).

## Where it lives
- Tasks: `evals/cases/core.yaml`, each with its expected outcome and SATS source
- Messy variants: `src/mizani/evals/generator.py`
- Runner: `src/mizani/evals/runner.py` (command `mizani-evals`)
- Results: `evals/results/<date>-<model>.json`
- Tests: `tests/test_evals.py`

## How it works
1. Tasks: 23 hand-written synthetic patients across thirteen categories: colour and chart sign, colour from measured inputs, over-triage, missing vitals, impossible vitals, vitals written only in the complaint, hidden instructions in the complaint, Arabic input, refusal to diagnose, commit without approval, re-triage timing, the re-measure loop and a patient's false claim of a chart sign. Each expected colour, TEWS total and chart sign is worked out by hand from the SATS adult chart. A unit test computes the colour and TEWS of the 22 tasks whose vitals are on the form with the rules engine, so a mistake in either shows up.
2. Messy variants: The generator adds one seeded kind of mess to each plain task with all seven vitals: typos, vitals only in the complaint as nurse shorthand, temperature typed in °F, a blank vital, or the complaint in Arabic. Each variant's expectation is adjusted by the SATS rule involved, never by running Mizani.
3. Runs: Each task runs N times (5 by default). Every run gets a fresh database and the real `mizani-triage` server in-process, so the model is the only thing that changes between runs. The special tasks also drive the extra steps: re-measured vitals, commits with missing, forged, foreign and expired tokens, a simulated clock for the re-check timer, and, for the false claim (`kind: false_claim`), whether the nurse is asked to confirm the sign, nothing is saved before the nurse decides, and the nurse's override is what gets saved.
4. Report: For each task: passes out of N, the number of distinct outcomes (colour plus chart signs) across runs, mean and spread of the time to the nurse, tokens (in, out and total) and cost per run, and which checks failed how often. The JSON records the model, endpoint, machine and git commit. With `--second-look` the agent's second look is on (agent.md); the report records the setting and the file name ends in `-second-look`, so a run with it and a run without it can be compared.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `mizani-evals` | `--runs`, `--only`, `--no-messy`, `--machine`, `--second-look`; `LLM_BASE_URL`, `LLM_MODEL` | Markdown table; JSON report | model calls; temporary databases |
| `messy_variants(cases, seed)` | tasks | one variant per eligible task | none |
| `run_suite(llm, cases, runs, second_look)` | chat model, tasks, the second-look switch | per-task summaries | as above |

## Depends on / used by
- Depends on: the agent (agent.md), the MCP server, the re-triage watch, the approval module, and `auth.py`: the checks approve as "Eval Nurse", an account the runner adds to each run's own database that no one can sign in to, so the server's role check sees an active nurse as it would at the desk. Measured results are unchanged by this: no task reads the account.
- Used by: EVALS.md, and anyone comparing models or prompts (run it before and after a change).

## Safety rules this system enforces
- Synthetic data only: every label, complaint and number is made up.
- Tests the safety rules directly: commits without a valid token, instructions hidden in a complaint, diagnosis requests, signs that are not on the chart.
- Honest by construction: every task runs N times and every failure is kept in the report.

## Running and testing it alone
```
uv run pytest tests/test_evals.py                 # no model needed
uv run mizani-evals --runs 5 --machine "<machine>" # Ollama running with LLM_MODEL pulled
uv run mizani-evals --runs 1 --only chest-pain-01  # one quick task
uv run mizani-evals --runs 5 --second-look --machine "<machine>"  # the same suite with the second look on
```

## Known limitations
- The diagnosis check is a word screen in English; a diagnosis in other words or in Arabic would pass it.
- The re-check timer is tested with a simulated clock (the commit time is moved back), not by waiting.
- Typos are letter swaps; real typos also drop and add letters.

## Extending it
A new task is one entry in `evals/cases/core.yaml` with `expected` and `source`; the cross-check test runs it through the engine. A new kind of mess is one function in `generator.py` plus the reason its expectation changes.
