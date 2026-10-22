"""Shared data contract between the rules engine, the MCP server, the agent and the API.

Matches the API contract in the team's UI/UX plan. Any change here is announced to the team.
Colours stay plain strings: the colour list and target times come from the rule pack.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# SATS adds "confused" to AVPU: it scores 2 on the adult TEWS and is a very urgent sign.
Avpu = Literal["alert", "confused", "voice", "pain", "unresponsive"]
Mobility = Literal["walking", "with_help", "stretcher"]


class Contract(BaseModel):
    """Base for every contract model: unknown fields are an error, text is trimmed."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Vitals(Contract):
    """Vitals as entered by the nurse. All optional: the engine asks for a re-measure when a TEWS
    vital is missing, and it (not this schema) checks ranges, so impossible values reach it to be
    flagged."""

    respiratory_rate: int | None = None  # breaths/min
    heart_rate: int | None = None  # beats/min
    systolic_bp: int | None = None  # mmHg
    temperature: float | None = None  # °C
    spo2: int | None = None  # %. Not part of TEWS; only feeds the "check SpO2" flag
    avpu: Avpu | None = None
    mobility: Mobility | None = None
    trauma: bool | None = None  # any injury in the past 48 hours
    # Finger-prick and urine dipstick results, used only by the diabetic discriminators
    glucose: float | None = None  # mmol/L
    ketonuria: bool | None = None


class Sourced(Contract):
    """Anything shown to the nurse carries the rule id or input field it came from."""

    source: str = Field(min_length=1)


class TewsComponent(Sourced):
    field: str = Field(min_length=1)
    value: Any
    points: int = Field(ge=0)


class Tews(Contract):
    total: int = Field(ge=0)
    components: list[TewsComponent]

    @model_validator(mode="after")
    def total_is_sum(self) -> "Tews":
        points = sum(c.points for c in self.components)
        if self.total != points:
            raise ValueError(f"TEWS total {self.total} != sum of component points {points}")
        return self


class Discriminator(Sourced):
    name: str = Field(min_length=1)
    colour: str = Field(min_length=1)
    quote: str | None = None  # the complaint's words that show it; None when the vitals prove it


class SignClaim(Contract):
    """A chart discriminator the agent names, with the words of the complaint that show it.
    The engine applies it only if those words are in the complaint and not in text addressed
    to the system."""

    id: str = Field(min_length=1)
    quote: str = ""


class Flag(Sourced):
    kind: str = Field(min_length=1)  # e.g. "remeasure", "recheck_due"
    message: str = Field(min_length=1)


class Proposal(Sourced):
    """A suggested priority. Always pending: only a nurse decision commits it.
    `source` says why this colour: the TEWS total or the discriminator that set it, with rules."""

    triage_id: str = Field(min_length=1)
    colour: str = Field(min_length=1)
    status: Literal["pending"] = "pending"
    tews: Tews
    discriminators: list[Discriminator] = []
    flags: list[Flag] = []


class NurseDecision(Contract):
    """A named nurse approves the proposal or overrides it with a colour and a reason."""

    nurse_name: str = Field(min_length=1)
    action: Literal["approve", "override"]
    colour: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def override_needs_colour_and_reason(self) -> "NurseDecision":
        if self.action == "override" and not (self.colour and self.reason):
            raise ValueError("an override needs both a colour and a reason")
        if self.action == "approve" and self.colour is not None:
            raise ValueError("an approval keeps the proposed colour; use override to change it")
        return self


class AuditEntry(Contract):
    """One logged tool call. The hash chain is added by the audit store (M4)."""

    time: datetime
    tool: str = Field(min_length=1)
    inputs: dict[str, Any]
    outputs: Any
    error: bool = False  # the call failed or was refused; outputs holds the reason
    approver: str | None = None
    model: str | None = None
    tokens: int | None = Field(default=None, ge=0)
    latency_ms: float = Field(ge=0)
    cost: float = Field(default=0.0, ge=0)
