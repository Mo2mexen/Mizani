"""SATS rules engine: deterministic, no model. Every number it returns carries its source.

Flow (manual p6, five steps): validate the vitals, score TEWS, match chart discriminators,
then propose the higher of the TEWS colour and the discriminator colour. The proposal is
always pending; only a named nurse commits it.
"""

from mizani.rules.pack import DiscriminatorRule, RulePack
from mizani.schemas import Discriminator, Flag, Proposal, Tews, TewsComponent, Vitals

TEWS_FIELDS = (
    "respiratory_rate",
    "heart_rate",
    "systolic_bp",
    "temperature",
    "avpu",
    "mobility",
    "trauma",
)
LABELS = {
    "respiratory_rate": "Respiratory rate",
    "heart_rate": "Heart rate",
    "systolic_bp": "Systolic BP",
    "temperature": "Temperature",
    "spo2": "SpO2",
    "glucose": "Glucose",
    "avpu": "Level of consciousness (AVPU)",
    "mobility": "Mobility",
    "trauma": "Trauma (any injury in the past 48 hours)",
}
LOW_IS_EMERGENCY = {"respiratory_rate", "heart_rate", "systolic_bp"}
FAHRENHEIT_RANGE = (86, 113)  # a "temperature" here was almost certainly typed in °F


def _plausible(vitals: Vitals, field: str, pack: RulePack) -> bool:
    value = getattr(vitals, field)
    if value is None:
        return False
    limits = pack.plausible.ranges.get(field)
    return limits is None or limits[0] <= value <= limits[1]


def validate_vitals(vitals: Vitals, pack: RulePack) -> list[Flag]:
    """Re-measure requests for missing TEWS vitals and for values outside the data-entry limits."""
    flags = []
    for field in TEWS_FIELDS:
        if getattr(vitals, field) is None:
            flags.append(
                Flag(
                    kind="remeasure",
                    message=f"{LABELS[field]} is missing; measure it to complete TEWS",
                    source=f"input: {field}",
                )
            )
    for field, (low, high) in pack.plausible.ranges.items():
        value = getattr(vitals, field)
        if value is None or low <= value <= high:
            continue
        message = f"{LABELS[field]} {value:g} is outside {low:g}-{high:g}; re-measure"
        if field == "temperature" and FAHRENHEIT_RANGE[0] <= value <= FAHRENHEIT_RANGE[1]:
            message += " (looks like °F; enter °C)"
        elif field in LOW_IS_EMERGENCY and value < low:
            message += ". If the patient is not breathing or has no pulse, take them to resus now"
        flags.append(
            Flag(
                kind="remeasure",
                message=message,
                source=f"input: {field}; {pack.cite(pack.plausible.source)}",
            )
        )
    return flags


def compute_tews(vitals: Vitals, pack: RulePack) -> Tews:
    """Score every usable TEWS vital. Missing or implausible ones are left out, never guessed."""
    components = []
    for field, row in pack.tews.items():
        if not _plausible(vitals, field, pack):
            continue
        value = getattr(vitals, field)
        if row.bands is not None:
            band = next(b for b in row.bands if b.contains(value))
            points, detail = band.points, band.label()
        else:
            detail = str(value).lower() if isinstance(value, bool) else value
            points = row.values[detail]
        components.append(
            TewsComponent(
                field=field,
                value=value,
                points=points,
                source=pack.cite(f"{row.source} | {detail} = {points}"),
            )
        )
    return Tews(total=sum(c.points for c in components), components=components)


def tews_colour(total: int, pack: RulePack):
    return next(t for t in pack.tews_colour if total >= t.min_total)


def _from_vitals(rule: DiscriminatorRule, vitals: Vitals, pack: RulePack) -> str | None:
    """The input field that matches this discriminator, if the vitals alone match it."""
    glucose = vitals.glucose if _plausible(vitals, "glucose", pack) else None
    if rule.id == "reduced_consciousness" and vitals.avpu not in (None, "alert"):
        return "avpu"
    if glucose is None:
        return None
    if rule.glucose_below is not None and glucose < rule.glucose_below:
        return "glucose"
    if rule.glucose_above is not None and glucose > rule.glucose_above:
        if rule.id == "diabetic_ketonuria" and vitals.ketonuria is True:
            return "glucose, ketonuria"
        # Over 17 without known ketones is at least yellow; ketones would only raise it
        if rule.id == "diabetic_high_glucose" and vitals.ketonuria is not True:
            return "glucose"
    return None


def match_discriminators(
    requested: list[str], vitals: Vitals, pack: RulePack
) -> tuple[list[DiscriminatorRule], list[Discriminator], list[Flag]]:
    """Chart discriminators named by the agent or nurse, plus those the vitals prove.

    Anything not on the chart is refused with a flag: only real chart entries can set a colour.
    """
    by_id = {d.id: d for d in pack.discriminators}
    rules, shown, flags = [], [], []
    for rule in pack.discriminators:
        field = _from_vitals(rule, vitals, pack)
        if rule.id == "reduced_consciousness" and vitals.avpu == "alert" and rule.id in requested:
            # SATS: "any patient not fully alert" (manual p9). The nurse saw an alert patient,
            # so her measurement wins over a reading of the complaint.
            flags.append(
                Flag(
                    kind="conflicts_with_vitals",
                    message=f"'{rule.name}' was not applied: the nurse recorded AVPU alert",
                    source=f"input: avpu; {pack.cite(rule.source)}",
                )
            )
            continue
        if rule.id in requested:
            field = field or "complaint"
        if field is None:
            continue
        rules.append(rule)
        shown.append(
            Discriminator(
                name=rule.name,
                colour=rule.colour,
                source=f"{pack.cite(rule.source)}; matched from input: {field}",
            )
        )
    for name in requested:
        if name not in by_id:
            flags.append(
                Flag(
                    kind="not_on_chart",
                    message=f"'{name}' is not a SATS adult discriminator; it can't set a colour",
                    source="input: discriminators",
                )
            )
    return rules, shown, flags


def investigations(
    vitals: Vitals, tews: Tews, matched: list[DiscriminatorRule], pack: RulePack
) -> list[Flag]:
    """The chart's additional investigations. They are reminders for the nurse, never points."""
    inv = pack.investigations
    due = []
    rr = next((c for c in tews.components if c.field == "respiratory_rate"), None)
    if rr is not None and rr.points >= 1:
        due.append(("check_spo2", "respiratory_rate"))
    glucose = vitals.glucose if _plausible(vitals, "glucose", pack) else None
    if vitals.avpu not in (None, "alert") and glucose is None:  # missing or not credible
        due.append(("check_glucose", "avpu"))
    if (
        glucose is not None
        and glucose >= inv["check_ketones"].glucose_from
        and vitals.ketonuria is None
    ):
        due.append(("check_ketones", "glucose"))
    if any(d.id == "chest_pain" for d in matched):
        due.append(("ecg", "discriminators"))
    return [
        Flag(
            kind="investigation",
            message=inv[key].message,
            source=f"{pack.cite(inv[key].source)}; input: {field}",
        )
        for key, field in due
    ]


def propose(triage_id: str, vitals: Vitals, requested: list[str], pack: RulePack) -> Proposal:
    """The pending proposal. Its `source` is a one-line, cited reason for the colour."""
    flags = validate_vitals(vitals, pack)
    tews = compute_tews(vitals, pack)
    by_tews = tews_colour(tews.total, pack)
    matched, shown, refused = match_discriminators(requested, vitals, pack)
    flags += refused
    flags += investigations(vitals, tews, matched, pack)

    scored = len(tews.components)
    if scored < len(TEWS_FIELDS):
        flags.append(
            Flag(
                kind="incomplete",
                message=(
                    f"TEWS uses {scored} of {len(TEWS_FIELDS)} vitals; the colour can only go up "
                    "once the rest are measured"
                ),
                source="input: vitals",
            )
        )

    colour = by_tews.colour
    reason = f"TEWS {tews.total} -> {colour} ({pack.cite(by_tews.source)})"
    for rule in matched:
        if pack.rank(rule.colour) < pack.rank(colour):
            colour = rule.colour
            reason = f"discriminator '{rule.name}' -> {rule.colour} ({pack.cite(rule.source)})"
    reason += "; final colour is the higher of the TEWS colour and any discriminator colour"
    return Proposal(
        triage_id=triage_id,
        colour=colour,
        source=reason,
        tews=tews,
        discriminators=shown,
        flags=flags,
    )
