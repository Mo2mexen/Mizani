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
2. `compute_tews`: scores each usable vital with the adult TEWS bands (total 0 to 17).
3. `match_discriminators`: takes the chart signs the agent named, each with a quote (the words of the complaint that show it), plus the ones the inputs prove (not alert means reduced consciousness; glucose under 3 means hypoglycaemia; the two diabetic signs). A named sign is refused, with a flag the nurse sees, when:
   - it is not on the chart (`not_on_chart`);
   - the nurse's measurement rules it out (`conflicts_with_vitals`): "reduced consciousness" when AVPU is alert (SATS: any patient not fully alert, manual p9), a glucose sign the measured glucose contradicts, "glucose over 11 and ketonuria" when the dipstick showed no ketones, "glucose over 17, no ketonuria" when it showed ketones;
   - its quote is missing or not in the complaint (`not_in_complaint`). The match ignores case, spacing, punctuation and Arabic vowel marks, and survives a typo (85% similar);
   - its quote sits in, or the sign is named in, text addressed to the software (`ignored_instruction`): a sentence with a note to the AI or system, "ignore the rules", an order to mark a colour or priority, or a chart or tool id in snake_case. Such text also gets one `ignored_instruction` flag that quotes it, so the nurse sees the attempt.
4. `investigations`: the chart's additional investigations as reminders (check SpO2 when RR scores, glucose when not alert, ketones when glucose ≥ 11, ECG for chest pain). They never add points.
5. `propose`: colour = the higher of the TEWS colour (0 to 2 green, 3 to 4 yellow, 5 to 6 orange, 7 or more red) and any discriminator colour. Missing vitals add an `incomplete` flag, since the colour can only go up once they are measured. When the colour is red only because of a sign taken from the complaint (the TEWS colour is lower and no measured input proves a red sign), a `confirm_sign` flag asks the nurse to check that sign with the patient before approving: words can be false or planted, and no rule can tell. The proposal is always `pending`.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `load_pack(name)` | pack name | validated `RulePack` | reads the YAML |
| `validate_vitals` | `Vitals`, pack | `list[Flag]` | none |
| `compute_tews` | `Vitals`, pack | `Tews` | none |
| `match_discriminators` | `SignClaim`s (`{id, quote}`), `Vitals`, pack, complaint | matched rules, `Discriminator`s (with `quote` when from the complaint), flags | none |
| `instructions` | complaint | the sentences addressed to the software | none |
| `propose` | triage id, `Vitals`, `SignClaim`s, pack, complaint | pending `Proposal` with a sourced reason in `source` | none |

## Depends on / used by
- Depends on: `schemas.py`, `pydantic`, `pyyaml`.
- Used by: the MCP tools `validate_vitals`, `compute_tews`, `match_discriminators`, `propose_priority` (M2), and the re-triage watch (W1).

## Safety rules this system enforces
- Grounded in a published scale: every value is from the EMSSA adult chart or manual, cited in the pack.
- No unsourced output: the pack refuses to load a rule without a `source`, and the schemas refuse output without one.
- Only real chart entries: unknown discriminators cannot set a colour.
- Patient text is data: a sign counts only with the complaint's own words behind it, and never from text addressed to the software.
- Measurements beat words: a sign the nurse's readings rule out is dropped, and a red colour that rests on words alone asks the nurse to confirm.
- Rules beat the model: this code computes the colour, and the language model has no say in it.
- Nothing is guessed: missing or impossible values are left out and re-measured.

## Running and testing it alone
```
uv run pytest tests/test_engine.py
uv run python -c "from mizani.rules.engine import propose; from mizani.rules.pack import load_pack; from mizani.schemas import SignClaim, Vitals; print(propose('t1', Vitals(respiratory_rate=24, heart_rate=118), [SignClaim(id='chest_pain', quote='chest pain')], load_pack(), 'chest pain since noon').model_dump_json(indent=1))"
```

## Known limitations
- Adults only (older than 12 or taller than 150 cm). Children need the paediatric charts.
- Discriminators that depend on the complaint (chest pain, burns, pregnancy…) come from the agent. The engine checks that the quoted words are in the complaint, not that they mean the sign: a model that quotes "twisted my ankle" for a dislocation passes the check. The nurse sees each sign with its quote.
- A patient's own false report of a sign ("I am having a fit") reads exactly like a true one. The engine proposes the sign's colour and, for red, asks the nurse to confirm; only the nurse at the bedside can reject it (EVALS.md).
- The instruction check is a pattern list in English and a few Arabic phrases; an instruction phrased differently is not recognised, though its sign still needs a quote outside it.
- The data-entry limits are a typo guard, not clinical thresholds.
- The 2012 manual also lists "eye injury"; we follow the newer 2015 chart, which doesn't.

## Extending it
Another scale is another YAML pack with the same shape plus its tests (band edges, colours, target times). A new discriminator is one line in the pack with its source; give it a test if the inputs can prove it. An optional `hint` holds the manual's definition (cited in the same entry's source); the intake prompt shows it to the model so it picks signs by their meaning, not by a guess.
