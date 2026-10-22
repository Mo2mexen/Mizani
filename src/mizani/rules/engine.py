"""SATS rules engine: deterministic, no model. Every number it returns carries its source.

Flow (manual p6, five steps): validate the vitals, score TEWS, match chart discriminators,
then propose the higher of the TEWS colour and the discriminator colour. The proposal is
always pending; only a named nurse commits it.
"""

import re
import unicodedata
from difflib import SequenceMatcher

from mizani.rules.pack import DiscriminatorRule, RulePack
from mizani.schemas import Discriminator, Flag, Proposal, SignClaim, Tews, TewsComponent, Vitals

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
QUOTE_MATCH = 0.85  # how closely a quote must match the complaint's words (typos, spacing)
# Text written to the software rather than about the patient: a note or label for the AI or
# system, "ignore the rules", an order to set a colour or priority, or a chart or tool id
# (snake_case, which a patient never writes). Patient text is data, so such text never counts.
INSTRUCTION = re.compile(
    r"\b(note|message|instructions?|reminder)s? (to|for) (the )?(ai|system|assistant|model|bot)"
    r"|\b(ai|system|assistant|model)\s*:"
    r"|\bignore\b.{0,40}\b(rules|instructions|prompt|guidelines)\b"
    r"|\b(mark|set|classify|label)\b.{0,40}\b(as|to)\b.{0,20}"
    r"\b(red|orange|yellow|green|emergency|urgent|priority|vip)\b"
    r"|\b[a-z]+_[a-z_]+\b"
    r"|الذكاء الاصطناعي|ملاحظة للنظام|تعليمات للنظام|تجاهل (التعليمات|القواعد)",
    re.IGNORECASE,
)


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


def _words(text: str) -> str:
    """Lower case, no accents or Arabic vowel marks, words (split at "_" too) separated by
    single spaces."""
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(re.findall(r"[^\W_]+", text))


def _found(quote: str, text: str) -> bool:
    """The quote is in the text: word for word, or close enough to survive a typo."""
    q, t = _words(quote), _words(text)
    if not q:
        return False
    if f" {q} " in f" {t} ":
        return True
    words, n = t.split(), len(q.split())
    return any(
        SequenceMatcher(None, " ".join(words[i : i + size]), q).ratio() >= QUOTE_MATCH
        for size in {max(1, n - 1), n, n + 1}
        for i in range(len(words) - size + 1)
    )


def instructions(complaint: str) -> list[str]:
    """Sentences of the complaint that address the software instead of describing the patient."""
    sentences = (s.strip() for s in re.split(r"[.!?؟;\n]+", complaint))
    return [s for s in sentences if s and INSTRUCTION.search(s)]


def _names(rule: DiscriminatorRule, text: str) -> bool:
    t = f" {_words(text)} "
    return any(f" {_words(n)} " in t for n in (rule.id.replace("_", " "), rule.name))


def _unsupported(
    rule: DiscriminatorRule, claim: SignClaim, complaint: str, orders: list[str], pack: RulePack
) -> Flag | None:
    """Why the complaint doesn't support a sign the agent named, as a flag; None if it does."""
    told = next((s for s in orders if _names(rule, s)), None)
    data = complaint
    for order in orders:
        data = data.replace(order, " ")
    if told is None and _found(claim.quote, data):
        return None
    if told is not None or _found(claim.quote, complaint):
        text = told or claim.quote
        return Flag(
            kind="ignored_instruction",
            message=(
                f"'{rule.name}' was not applied: the complaint names it in text addressed to "
                f'the system ("{text}"), which is patient data, not an instruction'
            ),
            source=f"input: complaint; {pack.cite(rule.source)}",
        )
    words = f'the words given for it ("{claim.quote}") are not' if claim.quote else "no words"
    return Flag(
        kind="not_in_complaint",
        message=f"'{rule.name}' was not applied: {words} in the complaint",
        source=f"input: complaint; {pack.cite(rule.source)}",
    )


def _contradicted(rule: DiscriminatorRule, vitals: Vitals, pack: RulePack) -> Flag | None:
    """A flag when the nurse's measurement rules out a sign named from the complaint."""
    glucose = vitals.glucose if _plausible(vitals, "glucose", pack) else None
    if rule.id == "reduced_consciousness" and vitals.avpu == "alert":
        # SATS: "any patient not fully alert" (manual p9). The nurse saw an alert patient.
        reason = "the nurse recorded AVPU alert"
    elif glucose is not None and (
        (rule.glucose_below is not None and glucose >= rule.glucose_below)
        or (rule.glucose_above is not None and glucose <= rule.glucose_above)
    ):
        reason = f"the measured glucose is {glucose:g} mmol/L"
    elif rule.id == "diabetic_ketonuria" and vitals.ketonuria is False:
        reason = "the urine dipstick showed no ketones"
    elif rule.id == "diabetic_high_glucose" and vitals.ketonuria is True:
        reason = "the urine dipstick showed ketones"
    else:
        return None  # nothing measured rules it out; the complaint may still report it
    return Flag(
        kind="conflicts_with_vitals",
        message=f"'{rule.name}' was not applied: {reason}",
        source=f"input: vitals; {pack.cite(rule.source)}",
    )


def match_discriminators(
    requested: list[SignClaim], vitals: Vitals, pack: RulePack, complaint: str = ""
) -> tuple[list[DiscriminatorRule], list[Discriminator], list[Flag]]:
    """Chart discriminators named by the agent, plus those the vitals prove.

    A named sign counts only if it is on the chart, the nurse's measurements don't rule it out,
    and the agent's quote for it is in the complaint outside any text addressed to the system.
    """
    by_id = {d.id: d for d in pack.discriminators}
    claims = {c.id: c for c in requested}
    orders = instructions(complaint)
    rules, shown, flags = [], [], []
    for rule in pack.discriminators:
        field = _from_vitals(rule, vitals, pack)
        claim = claims.get(rule.id)
        quote = None
        if field is None and claim is not None:
            refusal = _contradicted(rule, vitals, pack) or _unsupported(
                rule, claim, complaint, orders, pack
            )
            if refusal:
                flags.append(refusal)
                continue
            field, quote = "complaint", claim.quote
        if field is None:
            continue
        rules.append(rule)
        shown.append(
            Discriminator(
                name=rule.name,
                colour=rule.colour,
                quote=quote,
                source=f"{pack.cite(rule.source)}; matched from input: {field}",
            )
        )
    for claim in requested:
        if claim.id not in by_id:
            flags.append(
                Flag(
                    kind="not_on_chart",
                    message=(
                        f"'{claim.id}' is not a SATS adult discriminator; it can't set a colour"
                    ),
                    source="input: discriminators",
                )
            )
    if orders:
        flags.append(
            Flag(
                kind="ignored_instruction",
                message=(
                    "The complaint contains text addressed to the system; it was read as "
                    "patient data only: " + " / ".join(f'"{s}"' for s in orders)
                ),
                source="input: complaint",
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


def _to_confirm(
    tews_colour: str, matched: list[DiscriminatorRule], shown: list[Discriminator], pack: RulePack
) -> list[Flag]:
    """An emergency (red) colour that rests on the complaint alone, with nothing measured behind
    it, asks the nurse to confirm the sign with the patient: words can be false or planted."""
    red = pack.colours[0].colour
    signs = list(zip(matched, shown, strict=True))
    if tews_colour == red or any(r.colour == red and d.quote is None for r, d in signs):
        return []
    return [
        Flag(
            kind="confirm_sign",
            message=(
                f"Nurse to confirm: '{r.name}' ({red}) comes from the complaint alone "
                f'("{d.quote}"); the vitals give {tews_colour}. Check it with the patient '
                "before approving."
            ),
            source=d.source,
        )
        for r, d in signs
        if r.colour == red
    ]


def propose(
    triage_id: str,
    vitals: Vitals,
    requested: list[SignClaim],
    pack: RulePack,
    complaint: str = "",
) -> Proposal:
    """The pending proposal. Its `source` is a one-line, cited reason for the colour."""
    flags = validate_vitals(vitals, pack)
    tews = compute_tews(vitals, pack)
    by_tews = tews_colour(tews.total, pack)
    matched, shown, refused = match_discriminators(requested, vitals, pack, complaint)
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

    flags += _to_confirm(by_tews.colour, matched, shown, pack)

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
