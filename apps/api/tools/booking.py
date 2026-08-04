"""`book_appointment` — idempotent on `(call_id, slot_start)`.

Two things make double-booking impossible under retry. The idempotency key is
derived from the call and the slot, so the same request always produces the same
key; and the in-process lock plus a completed-keys cache means two concurrent
dispatches of the same booking collapse into one Cal.com write, with the second
returning the first's result rather than issuing its own.

2,000 ms budget, `degrade` on failure. Degraded means a callback promise. It
never means telling the caller they are booked.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from apps.api.config.schema import ClientConfig
from apps.api.observability.logging import get_logger
from apps.api.settings import get_settings
from apps.api.tools.availability import speak_slot
from apps.api.tools.calcom import Booking, CalcomClient
from apps.api.tools.registry import ToolResult, ToolSpec, failure, ok

log = get_logger(__name__)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "slot_start": {
            "type": "string",
            "description": "ISO 8601 start of the slot the caller chose, as returned by "
            "check_availability.",
        },
        "caller_name": {"type": "string", "description": "Name the caller gave."},
        "notes": {"type": "string", "description": "One line describing the problem."},
    },
    "required": ["slot_start", "caller_name"],
    "additionalProperties": False,
}

DEGRADE_HINT = (
    "The booking did not go through. Do not tell the caller they are booked. Say the "
    "scheduling system is lagging, offer a callback within fifteen minutes, and confirm "
    "their number."
)


def idempotency_key(call_id: str, slot_start: str) -> str:
    return hashlib.sha1(f"{call_id}|{slot_start}".encode(), usedforsecurity=False).hexdigest()


@dataclass(slots=True)
class _BookingGuard:
    """Per-key lock plus result cache. One booking per key, per process."""

    locks: dict[str, asyncio.Lock]
    results: dict[str, Booking]

    def lock_for(self, key: str) -> asyncio.Lock:
        return self.locks.setdefault(key, asyncio.Lock())


_guard = _BookingGuard(locks={}, results={})


def reset_guard() -> None:
    """Test hook: the guard is process-global by design."""
    _guard.locks.clear()
    _guard.results.clear()


async def book_appointment(
    *,
    config: ClientConfig,
    client: CalcomClient,
    call_id: str,
    from_e164: str = "",
    slot_start: str,
    caller_name: str = "Caller",
    notes: str | None = None,
    **_: Any,
) -> ToolResult:
    settings = get_settings()
    event_type_id = config.booking.event_type_id or settings.calcom_event_type_id
    if not event_type_id:
        return failure("unavailable", DEGRADE_HINT, {"reason": "no event_type_id configured"})

    key = idempotency_key(call_id, slot_start)
    tz = ZoneInfo(config.timezone)
    start = datetime.fromisoformat(slot_start)
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)

    async with _guard.lock_for(key):
        if (existing := _guard.results.get(key)) is not None:
            # A concurrent or retried dispatch. Return the first result rather
            # than writing a second appointment.
            log.info("booking_idempotent_hit", key=key, uid=existing.uid)
            return _booked(existing, tz, deduplicated=True)

        booking = await client.create_booking(
            event_type_id=event_type_id,
            start=start,
            attendee_name=caller_name,
            attendee_phone=from_e164,
            timezone=config.timezone,
            idempotency_key=key,
            notes=notes,
        )
        _guard.results[key] = booking

    log.info("booking_created", uid=booking.uid, start=booking.start.isoformat())
    return _booked(booking, tz, deduplicated=False)


def _booked(booking: Booking, tz: ZoneInfo, *, deduplicated: bool) -> ToolResult:
    return ok(
        {
            "booking_uid": booking.uid,
            "start": booking.start.isoformat(),
            "spoken": speak_slot(booking.start, tz),
            "status": booking.status,
            "deduplicated": deduplicated,
        }
    )


def spec(config: ClientConfig, client: CalcomClient) -> ToolSpec:
    async def handler(**kwargs: Any) -> ToolResult:
        return await book_appointment(config=config, client=client, **kwargs)

    return ToolSpec(
        name="book_appointment",
        description=(
            "Book the appointment the caller agreed to. Only call after check_availability "
            "returned the slot and the caller confirmed it."
        ),
        json_schema=SCHEMA,
        handler=handler,
        timeout_ms=2000,
        on_failure="degrade",
        filler_phrase="Okay, locking that in for you now.",
        idempotency_key=lambda args: idempotency_key(
            str(args.get("call_id", "")), str(args.get("slot_start", ""))
        ),
    )
