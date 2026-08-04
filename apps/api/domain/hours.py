"""Business hours, timezone math, and emergency-dispatch windows.

Pure functions over `HoursConfig` and a timezone name. No clock is read here —
`now` is always passed in — which is what makes the DST and boundary cases
testable rather than flaky.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from apps.api.config.schema import ClientConfig, HoursConfig

_DAY_FIELDS = ("mon_fri", "mon_fri", "mon_fri", "mon_fri", "mon_fri", "sat", "sun")


class DispatchDecision(StrEnum):
    OPEN = "open"
    EMERGENCY_ONLY = "emergency_only"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class OpenWindow:
    start: time
    end: time

    def contains(self, t: time) -> bool:
        return self.start <= t < self.end


def _parse_range(value: str) -> OpenWindow | None:
    if value == "closed":
        return None
    start_s, end_s = value.split("-")
    return OpenWindow(time.fromisoformat(start_s), time.fromisoformat(end_s))


def window_for(hours: HoursConfig, moment: datetime) -> OpenWindow | None:
    """The open window covering `moment`'s weekday, or None if closed that day."""
    field = _DAY_FIELDS[moment.weekday()]
    return _parse_range(getattr(hours.regular, field))


def local_now(cfg: ClientConfig, now: datetime) -> datetime:
    """Convert an aware instant into the business's own timezone."""
    if now.tzinfo is None:
        raise ValueError("hours math requires an aware datetime")
    return now.astimezone(ZoneInfo(cfg.timezone))


def is_open(cfg: ClientConfig, now: datetime) -> bool:
    local = local_now(cfg, now)
    window = window_for(cfg.hours, local)
    return window is not None and window.contains(local.time())


def dispatch_decision(cfg: ClientConfig, now: datetime, *, is_emergency: bool) -> DispatchDecision:
    """What this call is allowed to book, given the clock and the emergency flag.

    `emergency_dispatch: after_hours_only` reads oddly at first: it means the
    after-hours *emergency* channel exists but the business does not want
    emergency pricing applied during normal hours, when a routine booking is the
    right answer.
    """
    if is_open(cfg, now):
        return DispatchDecision.OPEN

    policy = cfg.hours.emergency_dispatch
    if not is_emergency or policy == "never":
        return DispatchDecision.CLOSED
    if policy in ("always", "after_hours_only"):
        return DispatchDecision.EMERGENCY_ONLY
    return DispatchDecision.CLOSED


def next_open(cfg: ClientConfig, now: datetime, *, horizon_days: int = 14) -> datetime | None:
    """First instant the business is open at or after `now`, in business tz."""
    local = local_now(cfg, now)
    for offset in range(horizon_days + 1):
        day = local + timedelta(days=offset)
        window = window_for(cfg.hours, day)
        if window is None:
            continue
        candidate = day.replace(
            hour=window.start.hour, minute=window.start.minute, second=0, microsecond=0
        )
        if offset == 0 and window.contains(local.time()):
            return local
        if candidate >= local:
            return candidate
    return None


def applies_emergency_fee(cfg: ClientConfig, now: datetime, *, is_emergency: bool) -> bool:
    """The after-hours diagnostic fee is quoted only when it actually applies."""
    if not is_emergency or cfg.booking.emergency_fee_usd is None:
        return False
    return not is_open(cfg, now)
