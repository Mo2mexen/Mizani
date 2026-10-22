from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mizani.schemas import AuditEntry, Flag, NurseDecision, Proposal, Tews, TewsComponent, Vitals


def component(field: str, points: int) -> TewsComponent:
    return TewsComponent(field=field, value=1, points=points, source=f"rule.{field}")


def test_vitals_all_optional():
    assert Vitals().heart_rate is None


def test_vitals_keep_impossible_values_for_the_engine_to_flag():
    assert Vitals(heart_rate=900).heart_rate == 900


def test_vitals_reject_unknown_field():
    with pytest.raises(ValidationError):
        Vitals(pulse=80)


def test_vitals_reject_unknown_avpu():
    with pytest.raises(ValidationError):
        Vitals(avpu="drowsy")


def test_vitals_accept_sats_confused_and_glucose_inputs():
    v = Vitals(avpu="confused", glucose=2.8, ketonuria=False)
    assert (v.avpu, v.glucose, v.ketonuria) == ("confused", 2.8, False)


@pytest.mark.parametrize("source", ["", "   "])
def test_every_sourced_item_needs_a_source(source):
    with pytest.raises(ValidationError):
        Flag(kind="remeasure", message="Re-measure pulse", source=source)


def test_tews_total_must_match_components():
    Tews(total=3, components=[component("heart_rate", 2), component("avpu", 1)])
    with pytest.raises(ValidationError):
        Tews(total=4, components=[component("heart_rate", 2), component("avpu", 1)])


def test_proposal_is_always_pending():
    tews = Tews(total=0, components=[])
    assert Proposal(triage_id="t1", colour="green", tews=tews, source="TEWS 0").status == "pending"
    with pytest.raises(ValidationError):
        Proposal(triage_id="t1", colour="green", tews=tews, source="TEWS 0", status="committed")


def test_decision_needs_a_named_nurse():
    with pytest.raises(ValidationError):
        NurseDecision(nurse_name=" ", action="approve")


def test_override_needs_colour_and_reason():
    NurseDecision(nurse_name="Nurse A", action="override", colour="orange", reason="looks worse")
    with pytest.raises(ValidationError):
        NurseDecision(nurse_name="Nurse A", action="override", colour="orange")
    with pytest.raises(ValidationError):
        NurseDecision(nurse_name="Nurse A", action="override", reason="looks worse")


def test_approve_cannot_change_colour():
    with pytest.raises(ValidationError):
        NurseDecision(nurse_name="Nurse A", action="approve", colour="red")


def test_audit_entry_round_trips_as_json():
    entry = AuditEntry(
        time=datetime(2026, 10, 22, tzinfo=UTC),
        tool="compute_tews",
        inputs={"heart_rate": 120},
        outputs={"total": 2},
        model="qwen3.5:4b-q4_K_M",
        tokens=150,
        latency_ms=12.5,
    )
    assert AuditEntry.model_validate_json(entry.model_dump_json()) == entry
