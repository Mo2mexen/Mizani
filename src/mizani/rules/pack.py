"""Load and validate a rule pack. A rule without a source fails here, before anything runs.

`MIZANI_RULE_PACK` names the pack (default `sats_adult`): a YAML file in this folder, so a new
triage scale is a new file and the engine stays. Every clinical rule must cite one of the pack's
own references (`sources`), so a value cannot reach the nurse without the rule behind it.
"""

import os
import re
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

DEFAULT_PACK = "sats_adult"
PACK_NAME = re.compile(r"[a-z0-9_]{1,64}")  # a file name only: no folders, no "..", no extension
MAX_UPLOAD = 200_000  # characters; the adult SATS chart is about 11,000
# The coded vitals and every value the intake can send (schemas.Vitals), each needing points
CODED = {
    "avpu": ("alert", "confused", "voice", "pain", "unresponsive"),
    "mobility": ("walking", "with_help", "stretcher"),
    "trauma": ("true", "false"),
}

Source = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ColourLevel(Rule):
    colour: str
    label: str
    target_minutes: int = Field(ge=0)
    source: Source


class TewsColour(Rule):
    min_total: int = Field(ge=0)
    colour: str
    source: Source


class Band(Rule):
    lt: float | None = None
    lte: float | None = None
    gt: float | None = None
    gte: float | None = None
    points: int = Field(ge=0)

    def contains(self, value: float) -> bool:
        return (
            (self.lt is None or value < self.lt)
            and (self.lte is None or value <= self.lte)
            and (self.gt is None or value > self.gt)
            and (self.gte is None or value >= self.gte)
        )

    @model_validator(mode="after")
    def has_a_bound(self) -> "Band":
        if all(v is None for v in (self.lt, self.lte, self.gt, self.gte)):
            raise ValueError("a band needs at least one of lt, lte, gt, gte")
        return self

    def label(self) -> str:
        if self.gte is not None and self.lte is not None:
            return f"{self.gte:g}-{self.lte:g}"
        bounds = (("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="))
        return " and ".join(
            f"{sign}{getattr(self, name):g}"
            for name, sign in bounds
            if getattr(self, name) is not None
        )


class TewsRow(Rule):
    source: Source
    bands: list[Band] | None = None
    values: dict[str, int] | None = None

    @model_validator(mode="after")
    def bands_or_values(self) -> "TewsRow":
        if (self.bands is None) == (self.values is None):
            raise ValueError("a TEWS row needs either bands or values")
        return self


class DiscriminatorRule(Rule):
    id: str
    name: str
    colour: str
    source: Source
    glucose_below: float | None = None
    glucose_above: float | None = None
    hint: str | None = None


class Investigation(Rule):
    message: str
    source: Source
    glucose_from: float | None = None


class Plausible(Rule):
    source: Source
    ranges: dict[str, tuple[float, float]]


class RulePack(Rule):
    id: str
    name: str
    applies_to: str
    sources: dict[str, str]
    colours: list[ColourLevel]
    tews_colour: list[TewsColour]
    tews: dict[
        Literal[
            "respiratory_rate",
            "heart_rate",
            "systolic_bp",
            "temperature",
            "avpu",
            "mobility",
            "trauma",
        ],
        TewsRow,
    ]
    discriminators: list[DiscriminatorRule]
    investigations: dict[str, Investigation]
    plausible: Plausible

    @model_validator(mode="after")
    def colours_are_known(self) -> "RulePack":
        known = {c.colour for c in self.colours}
        used = {t.colour for t in self.tews_colour} | {d.colour for d in self.discriminators}
        if used - known:
            raise ValueError(f"colours not in the colour list: {sorted(used - known)}")
        return self

    @model_validator(mode="after")
    def rules_cite_the_references(self) -> "RulePack":
        """Each clinical rule's source starts with one of `sources` ("manual p6 ...", "chart: ...").
        The plausible ranges are a data-entry check, not a clinical rule, so they are left out."""
        cited = [
            *(("colours", c.source) for c in self.colours),
            *(("tews_colour", t.source) for t in self.tews_colour),
            *((f"tews.{k}", r.source) for k, r in self.tews.items()),
            *((f"discriminators.{d.id}", d.source) for d in self.discriminators),
            *((f"investigations.{k}", i.source) for k, i in self.investigations.items()),
        ]
        refs = tuple(self.sources)  # a source starts with a whole key: "manual p6", not "manuals"
        cites = re.compile("|".join(re.escape(r) + r"\b" for r in refs))
        uncited = [where for where, s in cited if not cites.match(s)]
        if not refs or uncited:
            raise ValueError(f"rules that do not cite one of sources {list(refs)}: {uncited}")
        return self

    @model_validator(mode="after")
    def the_engine_can_run_it(self) -> "RulePack":
        """What the engine relies on, checked before a chart scores anyone: unique sign ids,
        TEWS colours from the highest total down to 0, every plausible value in exactly one band
        of its row, every coded value scored, and the four investigations the engine raises."""
        ids = [d.id for d in self.discriminators]
        if len(ids) != len(set(ids)):
            raise ValueError(
                f"chart sign ids repeat: {sorted({i for i in ids if ids.count(i) > 1})}"
            )
        mins = [t.min_total for t in self.tews_colour]
        if not mins or mins != sorted(mins, reverse=True) or mins[-1] != 0:
            raise ValueError("tews_colour goes from the highest min_total down to 0")
        for field, row in self.tews.items():
            if row.values is not None:
                missing = set(CODED[field]) - set(row.values)
                if missing:
                    raise ValueError(f"tews.{field} gives no points for {sorted(missing)}")
                continue
            if field not in self.plausible.ranges:
                raise ValueError(f"plausible.ranges has no range for tews.{field}")
            low, high = self.plausible.ranges[field]
            step = 0.1 if field == "temperature" else 1
            for i in range(int(round((high - low) / step)) + 1):
                value = round(low + i * step, 1)
                hits = sum(band.contains(value) for band in row.bands)
                if hits != 1:
                    raise ValueError(
                        f"tews.{field}: the value {value:g} falls in {hits} bands, not exactly one"
                    )
        missing = {"check_spo2", "check_glucose", "check_ketones", "ecg"} - set(self.investigations)
        if missing:
            raise ValueError(f"investigations missing: {sorted(missing)}")
        if self.investigations["check_ketones"].glucose_from is None:
            raise ValueError("investigations.check_ketones needs glucose_from")
        return self

    def rank(self, colour: str) -> int:
        """0 is the most urgent colour."""
        return [c.colour for c in self.colours].index(colour)

    def cite(self, source: str) -> str:
        """The `source` string shown to the nurse, e.g. 'sats_adult · chart: Adult TEWS, HR row'."""
        return f"{self.id} · {source}"


def available_packs(folder: Traversable | None = None) -> list[str]:
    folder = folder or files("mizani.rules")
    return sorted(
        f.name.removesuffix(".yaml") for f in folder.iterdir() if f.name.endswith(".yaml")
    )


def load_pack(name: str | None = None, folder: Traversable | None = None) -> RulePack:
    """The pack `name`, else `MIZANI_RULE_PACK`, else `sats_adult`. Refuses a name that is not a
    plain file name, a pack that is not in the folder, and a pack whose `id` is not its name."""
    name = name or os.environ.get("MIZANI_RULE_PACK", "").strip() or DEFAULT_PACK
    if not PACK_NAME.fullmatch(name):
        raise ValueError(f"rule pack name {name!r}: use lower-case letters, digits and _ only")
    folder = folder or files("mizani.rules")
    path = folder.joinpath(f"{name}.yaml")
    if not path.is_file():
        raise ValueError(f"no rule pack {name!r}; available: {available_packs(folder)}")
    pack = RulePack.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if pack.id != name:
        raise ValueError(f"rule pack file {name}.yaml has id {pack.id!r}; they must match")
    return pack


def parse_pack(text: str) -> RulePack:
    """A chart uploaded by the chief doctor, checked like a built-in one: safe YAML only, every
    clinical rule citing one of its references, and everything the engine relies on."""
    if len(text) > MAX_UPLOAD:
        raise ValueError(f"the chart file is over {MAX_UPLOAD:,} characters")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"the chart file is not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError("the chart file must be a YAML mapping (id, name, sources, colours, ...)")
    pack = RulePack.model_validate(data)
    if not PACK_NAME.fullmatch(pack.id):
        raise ValueError(f"chart id {pack.id!r}: use lower-case letters, digits and _ only")
    return pack
