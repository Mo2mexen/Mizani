from pathlib import Path

import pytest
from test_agent_graph import FakeModel, reply

from mizani.evals.generator import MESS, SCORES_ZERO, messy_variants
from mizani.evals.runner import load_cases, run_suite
from mizani.rules import engine
from mizani.rules.pack import load_pack
from mizani.schemas import SignClaim, Vitals

CASES = load_cases(Path(__file__).parent.parent / "evals" / "cases")
PACK = load_pack()
IDS = {d.id for d in PACK.discriminators}
COLOURS = {c.colour for c in PACK.colours}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MIZANI_DB", str(tmp_path / "mizani.db"))
    monkeypatch.setenv("MIZANI_APPROVAL_SECRET", "test-secret")


def by_id(case_id):
    return next(c for c in [*CASES, *messy_variants(CASES)] if c["id"] == case_id)


def test_every_task_has_a_source_and_checkable_expectations():
    assert len(CASES) >= 20 and len({c["id"] for c in CASES}) == len(CASES)
    for case in CASES:
        expected = case["expected"]
        assert case["source"] and case["category"] and expected["colour"] in COLOURS
        for key in ("signs", "any_signs", "forbidden"):
            assert set(expected.get(key, [])) <= IDS, (case["id"], key)
        assert set(expected.get("remeasure", [])) <= set(Vitals.model_fields)


@pytest.mark.parametrize(
    "case", [c for c in CASES if not c.get("vitals_in_complaint")], ids=lambda c: c["id"]
)
def test_hand_worked_expectations_agree_with_the_rules_engine(case):
    # Two independent routes to the same answer: the chart worked by hand and the engine.
    expected = case["expected"]
    ids = expected.get("signs", []) + expected.get("any_signs", [])[:1]
    signs = [SignClaim(id=i, quote=case["complaint"]) for i in ids]
    proposal = engine.propose("t", Vitals(**case["vitals"]), signs, PACK, case["complaint"])
    assert (proposal.colour, proposal.tews.total) == (expected["colour"], expected["tews"])
    if "later_tews" in expected:
        later = Vitals(**{**case["vitals"], **case["later_vitals"]})
        assert engine.compute_tews(later, PACK).total == expected["later_tews"]
    if "later_colour" in expected:
        later = Vitals(**{**case["vitals"], **case["later_vitals"]})
        later_colour = engine.propose("t", later, signs, PACK, case["complaint"]).colour
        assert later_colour == expected["later_colour"]


def test_messy_variants_are_reproducible_and_keep_the_expectation_derivable():
    first, again = messy_variants(CASES), messy_variants(CASES)
    assert first == again and {c["id"].split("~")[1] for c in first} <= set(MESS)
    for variant in first:
        base = by_id(variant["id"].split("~")[0])
        mess = variant["id"].split("~")[1]
        if mess in ("fahrenheit", "drop_vital"):
            assert "incomplete" in variant["expected"]["flags"]
            (field,) = set(variant["expected"]["remeasure"]) - set(
                base["expected"].get("remeasure", [])
            )
            assert SCORES_ZERO[field](base["vitals"][field])  # it scored 0: TEWS unchanged
        assert variant["expected"]["tews"] == base["expected"]["tews"]
        assert variant["expected"]["colour"] == base["expected"]["colour"]


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_the_runner_passes_right_answers_and_fails_wrong_ones(anyio_backend):
    chest = by_id("chest-pain-01")
    right = [reply(ids=["chest_pain"])] * 2
    wrong = [reply(ids=["severe_pain"])] * 2
    passed = await run_suite(FakeModel(messages=iter(right)), [chest], runs=2, log=lambda _: None)
    failed = await run_suite(FakeModel(messages=iter(wrong)), [chest], runs=2, log=lambda _: None)
    assert (passed[0]["passes"], passed[0]["distinct_outcomes"]) == (2, 1)
    assert failed[0]["passes"] == 0 and failed[0]["failed_checks"]["sign chest_pain"] == 2


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
@pytest.mark.parametrize("task", ["commit-without-approval-01", "retriage-01", "remeasure-01"])
async def test_the_runner_drives_the_extra_steps(anyio_backend, task):
    signs = by_id(task)["expected"].get("signs", [])
    model = FakeModel(messages=iter([reply(ids=signs, quote=by_id(task)["complaint"])]))
    (summary,) = await run_suite(model, [by_id(task)], runs=1, log=lambda _: None)
    assert summary["passes"] == 1, summary["failed_checks"] or summary["errors"]


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_a_false_claim_fails_on_colour_but_reaches_the_nurse_flagged(anyio_backend):
    task = by_id("false-claim-01")
    believed = reply(ids=["seizure_current"], quote="I am having a fit right now")
    model = FakeModel(messages=iter([believed]))
    (summary,) = await run_suite(model, [task], runs=1, log=lambda _: None)
    assert summary["passes"] == 0
    assert set(summary["failed_checks"]) == {"colour green", "not seizure_current"}
