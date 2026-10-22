# Rules engine (SATS adult)

Scores a patient on the South African Triage Scale (SATS) adult chart and proposes a colour. It is plain Python with no model: the same input always gives the same output, and every point, sign, flag and colour names the rule it came from.

## Where it lives
- Rule pack: `src/mizani/rules/sats_adult.yaml`
- Loader: `src/mizani/rules/pack.py`
- Engine: `src/mizani/rules/engine.py`
- Tests: `tests/test_engine.py`

## How it works
The five SATS steps (training manual p6), minus the ones a person does:
1. `validate_vitals`: asks for a re-measure when a TEWS vital is missing, or when a value is outside wide data-entry limits (a pulse of 900, a temperature typed in °F). A very low pulse, breathing rate or blood pressure adds "take them to resus now". Out-of-range values are never scored.
2. `compute_tews`: scores each usable vital with the adult TEWS bands (total 0–17).
3. `match_discriminators`: takes the chart signs the agent or nurse named, plus the ones the inputs prove (not alert → reduced consciousness; glucose under 3 → hypoglycaemia; the two diabetic signs). Names not on the chart are refused with a `not_on_chart` flag.
4. `investigations`: the chart's additional investigations as reminders (check SpO2 when RR scores, glucose when not alert, ketones when glucose ≥ 11, ECG for chest pain). They never add points.
5. `propose`: colour = the higher of the TEWS colour (0–2 green, 3–4 yellow, 5–6 orange, 7+ red) and any discriminator colour. Missing vitals add an `incomplete` flag, since the colour can only go up once they are measured. The proposal is always `pending`.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `load_pack(name)` | pack name | validated `RulePack` | reads the YAML |
| `validate_vitals` | `Vitals`, pack | `list[Flag]` | none |
| `compute_tews` | `Vitals`, pack | `Tews` | none |
| `match_discriminators` | chart ids, `Vitals`, pack | matched rules, `Discriminator`s, flags | none |
| `propose` | triage id, `Vitals`, chart ids, pack | pending `Proposal` with a sourced reason in `source` | none |

## Depends on / used by
- Depends on: `schemas.py`, `pydantic`, `pyyaml`.
- Used by: the MCP tools `validate_vitals`, `compute_tews`, `match_discriminators`, `propose_priority` (M2), and the re-triage watch (W1).

## Safety rules this system enforces
- Grounded in a published scale: every value is from the EMSSA adult chart or manual, cited in the pack.
- No unsourced output: the pack refuses to load a rule without a `source`, and the schemas refuse output without one.
- Only real chart entries: unknown discriminators cannot set a colour.
- Rules beat the model: the colour is computed here, never by the language model.
- Nothing is guessed: missing or impossible values are left out and re-measured.

## Running and testing it alone
```
uv run pytest tests/test_engine.py
uv run python -c "from mizani.rules.engine import propose; from mizani.rules.pack import load_pack; from mizani.schemas import Vitals; print(propose('t1', Vitals(respiratory_rate=24, heart_rate=118), ['chest_pain'], load_pack()).model_dump_json(indent=1))"
```

## Known limitations
- Adults only (older than 12 or taller than 150 cm). Children need the paediatric charts.
- Discriminators that depend on the complaint (chest pain, burns, pregnancy…) come from the agent or the nurse; the engine only checks they are real chart entries.
- The data-entry limits are a typo guard, not clinical thresholds.
- The 2012 manual also lists "eye injury"; we follow the newer 2015 chart, which doesn't.

## Extending it
Another scale is another YAML pack with the same shape plus its tests (band edges, colours, target times). A new discriminator is one line in the pack with its source; give it a test if the inputs can prove it. An optional `hint` holds the manual's definition (cited in the same entry's source); the intake prompt shows it to the model so it picks signs by their meaning, not by a guess.
