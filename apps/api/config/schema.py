"""Schema for `config/clients/*.yaml` - the entire per-client tuning surface.

Adding a vertical means adding a YAML file, never a code path. The dashboard's
config editor validates against these models before writing.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_TIME_RANGE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnDetectionConfig(StrictModel):
    type: Literal["semantic_vad", "server_vad"] = "semantic_vad"
    interrupt_response: bool = True


class RealtimeConfig(StrictModel):
    model: str = "gpt-realtime-2.1"
    voice: str = "cedar"
    prompt_id: str | None = None
    prompt_version: str | None = None
    # Fallback when no server-stored prompt is pinned. Stored prompts are
    # preferred - a bad persona is then a one-integer rollback.
    instructions: str | None = None
    turn_detection: TurnDetectionConfig = Field(default_factory=TurnDetectionConfig)
    variables: dict[str, str] = Field(default_factory=dict)
    transcription_model: str = "gpt-4o-mini-transcribe"
    greeting: str | None = None

    @model_validator(mode="after")
    def _needs_a_persona(self) -> RealtimeConfig:
        if not self.prompt_id and not self.instructions:
            raise ValueError("realtime requires either prompt_id or instructions")
        return self


class RegularHours(StrictModel):
    mon_fri: str | Literal["closed"] = "08:00-18:00"
    sat: str | Literal["closed"] = "closed"
    sun: str | Literal["closed"] = "closed"

    @field_validator("mon_fri", "sat", "sun")
    @classmethod
    def _valid_range(cls, v: str) -> str:
        if v != "closed" and not _TIME_RANGE.match(v):
            raise ValueError(f"expected 'HH:MM-HH:MM' or 'closed', got {v!r}")
        return v


class HoursConfig(StrictModel):
    regular: RegularHours = Field(default_factory=RegularHours)
    emergency_dispatch: Literal["always", "never", "after_hours_only"] = "always"


class ServiceAreaConfig(StrictModel):
    postcodes: list[str] = Field(default_factory=list)
    out_of_area_action: Literal["capture_and_refer", "decline", "transfer"] = "capture_and_refer"

    @field_validator("postcodes", mode="before")
    @classmethod
    def _stringify(cls, v: object) -> object:
        # YAML renders bare 60601 as an int; postcodes are strings everywhere else.
        if isinstance(v, list):
            return [str(p).strip() for p in v]
        return v


class EscalationConfig(StrictModel):
    triggers: list[str] = Field(default_factory=list)
    safety_keywords: list[str] = Field(default_factory=list)
    target_number: str | None = None
    after_hours_target: str = "voicemail_with_page"
    max_consecutive_tool_failures: int = 3
    max_negative_sentiment_turns: int = 2

    @field_validator("target_number")
    @classmethod
    def _e164(cls, v: str | None) -> str | None:
        if v and not E164.match(v):
            raise ValueError(f"target_number must be E.164, got {v!r}")
        return v

    @field_validator("safety_keywords")
    @classmethod
    def _lower(cls, v: list[str]) -> list[str]:
        return [k.lower() for k in v]


class BudgetConfig(StrictModel):
    max_call_seconds: int = Field(default=480, gt=0, le=3600)
    max_call_cost_usd: float = Field(default=1.20, gt=0)
    wrap_up_at_pct: int = Field(default=80, ge=1, le=100)


class BookingConfig(StrictModel):
    event_type_id: int | None = None
    default_duration_minutes: int = 60
    emergency_fee_usd: float | None = None


class ClientConfig(StrictModel):
    client_id: str
    display_name: str
    phone_number: str
    timezone: str = "America/Chicago"
    realtime: RealtimeConfig
    hours: HoursConfig = Field(default_factory=HoursConfig)
    service_area: ServiceAreaConfig = Field(default_factory=ServiceAreaConfig)
    tools_enabled: list[str] = Field(default_factory=list)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    booking: BookingConfig = Field(default_factory=BookingConfig)
    knowledge_base: str | None = None

    @field_validator("phone_number")
    @classmethod
    def _e164(cls, v: str) -> str:
        if not E164.match(v):
            raise ValueError(f"phone_number must be E.164, got {v!r}")
        return v

    @field_validator("timezone")
    @classmethod
    def _known_tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v
