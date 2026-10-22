# Evals

Runs Mizani's agent against a fixed set of synthetic triage tasks many times with the real model, and reports how often each task passes, how much the answers vary from run to run, and what each run costs. The results are in [EVALS.md](../../EVALS.md).

## Where it lives
- Tasks (E2): `evals/cases/core.yaml`, each with its expected outcome and SATS source
- Messy variants (E1): `src/mizani/evals/generator.py`
- Runner (E3): `src/mizani/evals/runner.py` (command `mizani-evals`)
- Results: `evals/results/<date>-<model>.json`
- Tests: `tests/test_evals.py`

## How it works
1. Tasks: 22 hand-written synthetic patients across ten categories: colour and chart sign, colour from measured inputs, over-triage, missing, impossible and messy vitals, hidden instructions in the complaint, Arabic input, refusal to diagnose, commit without approval, re-triage timing and the re-measure loop. Each expected colour, TEWS total and chart sign is worked out by hand from the SATS adult chart, and a unit test checks every one against the rules engine, so a mistake in either shows up.
2. Messy variants: The generator adds one seeded kind of mess to each plain task with all seven vitals: typos, vitals only in the complaint as nurse shorthand, temperature typed in °F, a blank vital, or the complaint in Arabic. Each variant's expectation is adjusted by the SATS rule involved, never by running Mizani.
3. Runs: Each task runs N times (5 by default). Every run gets a fresh database and the real `mizani-triage` server in-process, so the model is the only thing that changes between runs. The special tasks also drive the extra steps: re-measured vitals, commits with missing, forged, foreign and expired tokens, and a simulated clock for the re-check timer.
4. Report: For each task: passes out of N, the number of distinct outcomes (colour plus chart signs) across runs, mean and spread of the time to the nurse, tokens and cost per run, and which checks failed how often. The JSON records the model, endpoint, machine and git commit.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `mizani-evals` | `--runs`, `--only`, `--no-messy`, `--machine`; `LLM_BASE_URL`, `LLM_MODEL` | Markdown table; JSON report | model calls; temporary databases |
| `messy_variants(cases, seed)` | tasks | one variant per eligible task | none |
| `run_suite(llm, cases, runs)` | chat model, tasks | per-task summaries | as above |

## Depends on / used by
- Depends on: the agent (agent.md), the MCP server, the re-triage watch, the approval module.
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
```

## Known limitations
- The diagnosis check is a word screen in English; a diagnosis in other words or in Arabic would pass it.
- The re-check timer is tested with a simulated clock (the commit time is moved back), not by waiting.
- Typos are letter swaps; real typos also drop and add letters.

## Extending it
A new task is one entry in `evals/cases/core.yaml` with `expected` and `source`; the cross-check test runs it through the engine. A new kind of mess is one function in `generator.py` plus the reason its expectation changes.
