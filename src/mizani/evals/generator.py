"""Synthetic messy patients (E1): deterministic variants of the hand-written eval tasks.

Each variant adds one kind of mess a busy desk produces and adjusts the expected outcome by the
SATS rule involved, so the expectation is still worked out by hand, never by running Mizani:
- typos: letters swapped in the complaint; expectation unchanged
- vitals_in_text: the numbers move from the form into the complaint as nurse shorthand; unchanged
- fahrenheit: temperature typed in °F; outside the data-entry limits, so a re-measure flag and
  `incomplete`, TEWS unchanged because the base temperature scored 0
- drop_vital: a vital that scored 0 is left blank; re-measure flag and `incomplete`, TEWS unchanged
- arabic: the complaint in Arabic, for tasks that carry `complaint_ar`; unchanged
Synthetic only. Which mess goes with which task is a seeded choice, not a measured rate.
"""

import copy
import random

NUMERIC = ("respiratory_rate", "heart_rate", "systolic_bp", "temperature")
# Adult TEWS values that score 0 (EMSSA adult chart).
SCORES_ZERO = {
    "respiratory_rate": lambda v: 9 <= v <= 14,
    "heart_rate": lambda v: 51 <= v <= 100,
    "systolic_bp": lambda v: 101 <= v <= 199,
    "temperature": lambda v: 35 <= v <= 38.4,
    "avpu": lambda v: v == "alert",
    "mobility": lambda v: v == "walking",
    "trauma": lambda v: v is False,
}
SHORTHAND = {"respiratory_rate": "RR {}", "heart_rate": "P {}"}


def _complete(case: dict) -> bool:
    """All seven TEWS vitals present and every number a plausible reading."""
    v = case["vitals"]
    return all(k in v for k in SCORES_ZERO) and 35 <= v.get("temperature", 0) <= 42


def _typos(case: dict, rng: random.Random) -> dict:
    words = case["complaint"].split(" ")
    long = [i for i, w in enumerate(words) if len(w) >= 6 and w.isalpha()]
    for i in rng.sample(long, min(2, len(long))):
        w, j = words[i], rng.randrange(1, len(words[i]) - 2)
        words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2 :]
    case["complaint"] = " ".join(words)
    return case


def _vitals_in_text(case: dict, rng: random.Random) -> dict:
    parts = [SHORTHAND[k].format(case["vitals"].pop(k)) for k in SHORTHAND]
    systolic = case["vitals"].pop("systolic_bp")
    parts.append(f"BP {systolic}/{round(systolic * 0.6)}")  # made-up diastolic, not scored
    parts.append(f"T {case['vitals'].pop('temperature')}")
    rng.shuffle(parts)
    case["complaint"] += " Card: " + ", ".join(parts) + "."
    case["vitals_in_complaint"] = True
    return case


def _fahrenheit(case: dict, rng: random.Random) -> dict:
    celsius = case["vitals"]["temperature"]
    case["vitals"]["temperature"] = round(celsius * 9 / 5 + 32, 1)
    return _needs_remeasure(case, "temperature")


def _drop_vital(case: dict, rng: random.Random) -> dict:
    zero = [k for k, scores_zero in SCORES_ZERO.items() if scores_zero(case["vitals"][k])]
    return _needs_remeasure(case, rng.choice(zero), drop=True)


def _needs_remeasure(case: dict, field: str, drop: bool = False) -> dict:
    if drop:
        del case["vitals"][field]
    expected = case["expected"]
    expected["remeasure"] = sorted({*expected.get("remeasure", []), field})
    expected["flags"] = sorted({*expected.get("flags", []), "incomplete"})
    return case


def _arabic(case: dict, rng: random.Random) -> dict:
    case["complaint"] = case.pop("complaint_ar")
    return case


MESS = {
    "typos": _typos,
    "vitals_in_text": _vitals_in_text,
    "fahrenheit": _fahrenheit,
    "drop_vital": _drop_vital,
    "arabic": _arabic,
}
WHY = {
    "typos": "letters swapped in the complaint; expectation unchanged",
    "vitals_in_text": "vitals only in the complaint as nurse shorthand; expectation unchanged",
    "fahrenheit": "temperature typed in °F: re-measure + incomplete; it scored 0, TEWS unchanged",
    "drop_vital": "a vital that scored 0 left blank: re-measure + incomplete, TEWS unchanged",
    "arabic": "complaint in Arabic; expectation unchanged",
}


def messy_variants(cases: list[dict], seed: int = 22) -> list[dict]:
    """One messy variant per plain triage task with all seven vitals, chosen by `seed`."""
    rng = random.Random(seed)
    variants = []
    for base in cases:
        if base.get("kind", "triage") != "triage" or not _complete(base):
            continue
        kinds = [k for k in MESS if k != "arabic" or "complaint_ar" in base]
        if not SCORES_ZERO["temperature"](base["vitals"]["temperature"]):
            kinds.remove("fahrenheit")  # dropping a scored temperature would change TEWS
        mess = rng.choice(kinds)
        case = MESS[mess](copy.deepcopy(base), rng)
        case.pop("complaint_ar", None)
        case["id"] = f"{base['id']}~{mess}"
        case["category"] = f"messy input ({mess})"
        case["source"] = f"E1 variant of {base['id']}: {WHY[mess]}. Base: {base['source']}"
        variants.append(case)
    return variants
