import pytest

from mizani.rules.engine import compute_tews, propose, tews_colour, validate_vitals
from mizani.rules.pack import load_pack
from mizani.schemas import Vitals

PACK = load_pack()
NORMAL = dict(
    respiratory_rate=12,
    heart_rate=80,
    systolic_bp=120,
    temperature=37.0,
    avpu="alert",
    mobility="walking",
    trauma=False,
)


def points(field, value):
    tews = compute_tews(Vitals(**{**NORMAL, field: value}), PACK)
    return next(c.points for c in tews.components if c.field == field)


# Every band edge on the adult TEWS chart (NOTES §9 / chart: Adult TEWS)
@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("respiratory_rate", 1, 2), ("respiratory_rate", 8, 2), ("respiratory_rate", 9, 0),
        ("respiratory_rate", 14, 0), ("respiratory_rate", 15, 1), ("respiratory_rate", 20, 1),
        ("respiratory_rate", 21, 2), ("respiratory_rate", 29, 2), ("respiratory_rate", 30, 3),
        ("heart_rate", 40, 2), ("heart_rate", 41, 1), ("heart_rate", 50, 1),
        ("heart_rate", 51, 0), ("heart_rate", 100, 0), ("heart_rate", 101, 1),
        ("heart_rate", 110, 1), ("heart_rate", 111, 2), ("heart_rate", 129, 2),
        ("heart_rate", 130, 3),
        ("systolic_bp", 70, 3), ("systolic_bp", 71, 2), ("systolic_bp", 80, 2),
        ("systolic_bp", 81, 1), ("systolic_bp", 100, 1), ("systolic_bp", 101, 0),
        ("systolic_bp", 199, 0), ("systolic_bp", 200, 2),
        ("temperature", 34.9, 2), ("temperature", 35.0, 0), ("temperature", 38.4, 0),
        ("temperature", 38.5, 2),
        ("avpu", "alert", 0), ("avpu", "confused", 2), ("avpu", "voice", 1),
        ("avpu", "pain", 2), ("avpu", "unresponsive", 3),
        ("mobility", "walking", 0), ("mobility", "with_help", 1), ("mobility", "stretcher", 2),
        ("trauma", False, 0), ("trauma", True, 1),
    ],
)  # fmt: skip
def test_tews_band_edges(field, value, expected):
    assert points(field, value) == expected


@pytest.mark.parametrize(
    "total, colour", [(0, "green"), (2, "green"), (3, "yellow"), (4, "yellow"),
                      (5, "orange"), (6, "orange"), (7, "red"), (17, "red")],
)  # fmt: skip
def test_tews_total_to_colour(total, colour):
    assert tews_colour(total, PACK).colour == colour


@pytest.mark.parametrize("field", ["respiratory_rate", "heart_rate", "systolic_bp", "temperature"])
def test_bands_cover_the_plausible_range_exactly_once(field):
    low, high = PACK.plausible.ranges[field]
    step = 0.1 if field == "temperature" else 1
    value = low
    while value <= high:
        hits = [b for b in PACK.tews[field].bands if b.contains(round(value, 1))]
        assert len(hits) == 1, (field, value)
        value += step


def test_pack_has_the_chart_discriminators_once_each():
    ids = [d.id for d in PACK.discriminators]
    assert len(ids) == len(set(ids)) == 39
    counts = {
        c: sum(d.colour == c for d in PACK.discriminators) for c in ("red", "orange", "yellow")
    }
    assert counts == {"red": 6, "orange": 23, "yellow": 10}


def test_target_times_from_the_manual():
    assert {c.colour: c.target_minutes for c in PACK.colours} == {
        "red": 0,
        "orange": 10,
        "yellow": 60,
        "green": 240,
    }


def test_normal_patient_is_green_and_everything_is_sourced():
    p = propose("t1", Vitals(**NORMAL), [], PACK)
    assert (p.colour, p.tews.total, p.flags) == ("green", 0, [])
    assert len(p.tews.components) == 7
    assert all(c.source.startswith("sats_adult · chart") for c in p.tews.components)
    assert "TEWS 0 -> green" in p.source


def test_discriminator_raises_colour_above_tews():
    p = propose("t1", Vitals(**NORMAL), ["chest_pain"], PACK)
    assert p.colour == "orange"
    assert (
        "Chest pain" in p.source and "matched from input: complaint" in p.discriminators[0].source
    )
    assert any(f.kind == "investigation" and "ECG" in f.message for f in p.flags)


def test_tews_wins_when_higher_than_discriminator():
    sick = {**NORMAL, "respiratory_rate": 30, "heart_rate": 130, "systolic_bp": 75}  # 3 + 3 + 2
    p = propose("t1", Vitals(**sick), ["moderate_pain"], PACK)
    assert (p.tews.total, p.colour) == (8, "red")
    assert p.source.startswith("TEWS 8 -> red")


def test_emergency_sign_is_red_even_with_normal_vitals():
    assert propose("t1", Vitals(**NORMAL), ["seizure_current"], PACK).colour == "red"


def test_only_chart_entries_can_set_a_colour():
    p = propose("t1", Vitals(**NORMAL), ["appendicitis"], PACK)
    assert p.colour == "green" and p.discriminators == []
    assert [f.kind for f in p.flags] == ["not_on_chart"]


def test_missing_vitals_are_requested_not_guessed():
    p = propose("t1", Vitals(heart_rate=80), [], PACK)
    kinds = [f.kind for f in p.flags]
    assert kinds.count("remeasure") == 6 and "incomplete" in kinds
    assert [c.field for c in p.tews.components] == ["heart_rate"]


def test_impossible_value_is_not_scored_and_asks_for_remeasure():
    p = propose("t1", Vitals(**{**NORMAL, "heart_rate": 900}), [], PACK)
    assert "heart_rate" not in [c.field for c in p.tews.components]
    assert any(f.kind == "remeasure" and "900" in f.message for f in p.flags)


def test_fahrenheit_temperature_gets_a_unit_hint():
    flags = validate_vitals(Vitals(**{**NORMAL, "temperature": 101.3}), PACK)
    assert any("°F" in f.message for f in flags)


def test_zero_heart_rate_points_to_resus():
    flags = validate_vitals(Vitals(**{**NORMAL, "heart_rate": 0}), PACK)
    assert any("resus" in f.message for f in flags)


def test_spo2_never_adds_points_but_rr_triggers_the_check():
    low_sat = propose("t1", Vitals(**{**NORMAL, "spo2": 80}), [], PACK)
    assert low_sat.tews.total == 0 and low_sat.colour == "green"
    fast = propose("t1", Vitals(**{**NORMAL, "respiratory_rate": 22}), [], PACK)
    assert any(f.message.startswith("Check SpO2") for f in fast.flags)


def test_confused_patient_is_orange_and_needs_glucose():
    p = propose("t1", Vitals(**{**NORMAL, "avpu": "confused"}), [], PACK)
    assert p.colour == "orange"
    assert "matched from input: avpu" in p.discriminators[0].source
    assert any("finger-prick glucose" in f.message for f in p.flags)


@pytest.mark.parametrize(
    "glucose, ketonuria, colour, sign",
    [
        (2.5, None, "red", "Hypoglycaemia"),
        (13.0, True, "orange", "Diabetic - glucose over 11 and ketonuria"),
        (20.0, True, "orange", "Diabetic - glucose over 11 and ketonuria"),
        (20.0, False, "yellow", "Diabetic - glucose over 17 (no ketonuria)"),
        (20.0, None, "yellow", "Diabetic - glucose over 17 (no ketonuria)"),
    ],
)
def test_glucose_discriminators(glucose, ketonuria, colour, sign):
    p = propose("t1", Vitals(**NORMAL, glucose=glucose, ketonuria=ketonuria), [], PACK)
    assert p.colour == colour
    assert [d.name for d in p.discriminators][0].startswith(sign)


def test_high_glucose_without_ketone_result_asks_for_dipstick():
    p = propose("t1", Vitals(**NORMAL, glucose=20.0), [], PACK)
    assert any("ketones" in f.message for f in p.flags)


def test_implausible_glucose_still_gets_the_glucose_reminder():
    p = propose("t1", Vitals(**{**NORMAL, "avpu": "confused", "glucose": 0.2}), [], PACK)
    assert any("finger-prick glucose" in f.message for f in p.flags)


def test_band_labels_cover_every_bound_combination():
    from mizani.rules.pack import Band

    assert Band(gte=30, points=3).label() == ">=30"
    assert Band(gt=100, lte=110, points=1).label() == ">100 and <=110"
    with pytest.raises(ValueError):
        Band(points=1)
