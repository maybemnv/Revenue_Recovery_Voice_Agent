"""`PracticeProfile` - the entire per-prospect surface.

Everything else in the rig is templated. A clone is this file plus a Retell
agent ID and a demo number; if a field is not here, it does not vary by
prospect. The schema is strict because Claude extracts into it and the rep
reviews the result - loose types would let a hallucinated insurance carrier
through the review gate unnoticed.
"""

from __future__ import annotations

import re
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TIME_RANGE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

ProviderRole = Literal["dentist", "hygienist", "specialist"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Provider(StrictModel):
    name: str
    role: ProviderRole = "dentist"
    accepts_new: bool = True


class AppointmentType(StrictModel):
    name: str
    minutes: int = Field(ge=10, le=240)
    provider_role: ProviderRole = "dentist"


class Hours(StrictModel):
    mon: str = "closed"
    tue: str = "closed"
    wed: str = "closed"
    thu: str = "closed"
    fri: str = "closed"
    sat: str = "closed"
    sun: str = "closed"

    @field_validator("*")
    @classmethod
    def _valid(cls, v: str) -> str:
        if v != "closed" and not _TIME_RANGE.match(v):
            raise ValueError(f"expected 'HH:MM-HH:MM' or 'closed', got {v!r}")
        return v

    def open_days(self) -> list[str]:
        return [d for d in DAYS if getattr(self, d) != "closed"]


class PracticeProfile(StrictModel):
    """What the clone pipeline extracts and the rep reviews.

    `demo_number` and `retell_agent_id` are filled by `push.py`, not by
    extraction - they do not exist until the agent is provisioned.
    """

    prospect_id: str
    practice_name: str
    tagline: str | None = None
    phone_display: str | None = None  # their real number: shown, never dialled
    demo_number: str | None = None  # Retell-provisioned; what they actually call
    website: str | None = None

    providers: list[Provider] = Field(default_factory=list)
    appointment_types: list[AppointmentType] = Field(default_factory=list)

    insurance_accepted: list[str] = Field(default_factory=list)
    insurance_notes: str | None = None

    hours: Hours = Field(default_factory=Hours)
    timezone: str = "America/Chicago"
    address: str | None = None

    services: list[str] = Field(default_factory=list)
    tone: str = "Warm, unhurried, small-practice. Not corporate."
    accent_color: str | None = None  # pulled from their site, drives the demo page

    # Provisioning state - written by push.py, never by extraction.
    retell_agent_id: str | None = None
    retell_kb_id: str | None = None
    demo_page_url: str | None = None

    @field_validator("prospect_id")
    @classmethod
    def _slug(cls, v: str) -> str:
        # A leading underscore is reserved for rig-owned profiles (`_showcase`),
        # which keeps them from colliding with a prospect slug taken from a domain.
        if not re.fullmatch(r"_?[a-z0-9][a-z0-9-]{1,40}", v):
            raise ValueError(f"prospect_id must be a lowercase slug, got {v!r}")
        return v

    @field_validator("demo_number")
    @classmethod
    def _e164(cls, v: str | None) -> str | None:
        if v and not _E164.match(v):
            raise ValueError(f"demo_number must be E.164, got {v!r}")
        return v

    @field_validator("accent_color")
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        if v and not _HEX.match(v):
            raise ValueError(f"accent_color must be a hex colour, got {v!r}")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v

    # -- persistence ------------------------------------------------------
    @classmethod
    def from_yaml(cls, text: str) -> PracticeProfile:
        return cls.model_validate(yaml.safe_load(text) or {})

    def to_yaml(self) -> str:
        return yaml.safe_dump(
            self.model_dump(mode="json", exclude_none=True),
            sort_keys=False,
            allow_unicode=True,
            width=88,
        )


# The subset Claude fills in. Provisioning fields and prospect_id are excluded so
# the model cannot invent a phone number the rep would then have to catch.
EXTRACTION_FIELDS = (
    "practice_name",
    "tagline",
    "phone_display",
    "website",
    "providers",
    "appointment_types",
    "insurance_accepted",
    "insurance_notes",
    "hours",
    "timezone",
    "address",
    "services",
    "tone",
    "accent_color",
)


class ExtractedProfile(StrictModel):
    """Claude's output schema. Mirrors PracticeProfile minus provisioning state."""

    practice_name: str
    tagline: str | None = None
    phone_display: str | None = None
    website: str | None = None
    providers: list[Provider] = Field(default_factory=list)
    appointment_types: list[AppointmentType] = Field(default_factory=list)
    insurance_accepted: list[str] = Field(default_factory=list)
    insurance_notes: str | None = None
    hours: Hours = Field(default_factory=Hours)
    timezone: str = "America/Chicago"
    address: str | None = None
    services: list[str] = Field(default_factory=list)
    tone: str = "Warm, unhurried, small-practice. Not corporate."
    accent_color: str | None = None

    def into_profile(self, prospect_id: str) -> PracticeProfile:
        return PracticeProfile(prospect_id=prospect_id, **self.model_dump())
