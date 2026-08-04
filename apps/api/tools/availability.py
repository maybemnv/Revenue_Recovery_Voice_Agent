"""`check_availability` — Cal.com slot search, resolved into the client's timezone.

1,200 ms budget, `degrade` on failure. Degraded means offering a callback
window, never inventing slots. Times are formatted for speech here rather than
in the prompt, because "twelve thirty AM" is a formatting decision and not
something to spend an inference round-trip on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apps.api.config.schema import ClientConfig
from apps.api.settings import get_settings
from apps.api.tools.calcom import CalcomClient, Slot
from apps.api.tools.registry import ToolResult, ToolSpec, failure, ok

MAX_SLOTS_SPOKEN = 3

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "urgency": {
            "type": "string",
            "enum": ["emergency", "soon", "flexible"],
            "description": "emergency searches from now; flexible searches the next few days.",
        },
        "after": {
            "type": "string",
            "description": "ISO 8601 earliest acceptable start, if the caller named one.",
        },
    },
    "required": ["urgency"],
    "additionalProperties": False,
}

_HORIZON = {
    "emergency": timedelta(hours=12),
    "soon": timedelta(days=2),
    "flexible": timedelta(days=7),
}

UNAVAILABLE_HINT = (
    "The scheduling system is not responding. Do not offer specific times. Offer a callback "
    "within fifteen minutes and take the caller's name and number."
)


def speak_slot(slot_start: datetime, tz: ZoneInfo) -> str:
    """Render a slot the way a person would say it out loud."""
    local = slot_start.astimezone(tz)
    hour = local.hour % 12 or 12
    meridiem = "AM" if local.hour < 12 else "PM"
    minute = f":{local.minute:02d}" if local.minute else ""
    today = datetime.now(tz).date()
    if local.date() == today:
        day = "today"
    elif local.date() == today + timedelta(days=1):
        day = "tomorrow"
    else:
        day = local.strftime("%A")
    return f"{day} at {hour}{minute} {meridiem}"


async def check_availability(
    *,
    config: ClientConfig,
    client: CalcomClient,
    urgency: str = "soon",
    after: str | None = None,
    now: datetime | None = None,
    **_: Any,
) -> ToolResult:
    settings = get_settings()
    event_type_id = config.booking.event_type_id or settings.calcom_event_type_id
    if not event_type_id:
        return failure("unavailable", UNAVAILABLE_HINT, {"reason": "no event_type_id configured"})

    tz = ZoneInfo(config.timezone)
    start = datetime.fromisoformat(after) if after else (now or datetime.now(UTC))
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    end = start + _HORIZON.get(urgency, _HORIZON["soon"])

    slots: list[Slot] = await client.search_slots(
        event_type_id=event_type_id, start=start, end=end, timezone=config.timezone
    )
    if not slots:
        return {
            "status": "not_found",
            "data": {"slots": []},
            "speak_hint": (
                "Nothing is open in that window. Offer the next available day or take details "
                "for a callback. Do not invent a time."
            ),
        }

    chosen = slots[:MAX_SLOTS_SPOKEN]
    return ok(
        {
            "slots": [
                {"start": s.start.isoformat(), "spoken": speak_slot(s.start, tz)} for s in chosen
            ],
            "timezone": config.timezone,
        }
    )


def spec(config: ClientConfig, client: CalcomClient) -> ToolSpec:
    async def handler(**kwargs: Any) -> ToolResult:
        return await check_availability(config=config, client=client, **kwargs)

    return ToolSpec(
        name="check_availability",
        description="Find open appointment slots. Always call before offering a time.",
        json_schema=SCHEMA,
        handler=handler,
        timeout_ms=1200,
        on_failure="degrade",
        filler_phrase="Let me pull up the schedule, one second.",
    )
