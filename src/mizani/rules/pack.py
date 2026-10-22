"""Load and validate a rule pack. A rule without a source fails here, before anything runs."""

from importlib.resources import files
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Source = Annotated[str, Field(min_length=1)]


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

    def label(self) -> str:
        if self.gte is not None and self.lte is not None:
            return f"{self.gte:g}-{self.lte:g}"
        if self.lt is not None:
            return f"<{self.lt:g}"
        return f">{self.gt:g}"


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

    def rank(self, colour: str) -> int:
        """0 is the most urgent colour."""
        return [c.colour for c in self.colours].index(colour)

    def cite(self, source: str) -> str:
        """The `source` string shown to the nurse, e.g. 'sats_adult · chart: Adult TEWS, HR row'."""
        return f"{self.id} · {source}"


def load_pack(name: str = "sats_adult") -> RulePack:
    text = files("mizani.rules").joinpath(f"{name}.yaml").read_text(encoding="utf-8")
    return RulePack.model_validate(yaml.safe_load(text))
