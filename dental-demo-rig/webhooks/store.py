"""The demo calendar and call log.

Supabase in a real demo, in-memory when no credentials are configured. The
in-memory fallback is not a test double — it is what makes the rig runnable on a
laptop with no accounts, so a rep can rehearse the tool responses offline and an
engineer can run the suite without provisioning anything.

The booking write is the moment the demo lands: the prospect books on the phone
and the slot fills on screen while they are still talking. Everything here exists
to make that write fast and visible.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

from clone.settings import get_settings

SLOTS = "demo_slots"
BOOKINGS = "demo_bookings"
CALLS = "demo_calls"


class SlotUnavailable(RuntimeError):
    """The slot was taken between find_appointment and book_appointment."""


class CalendarStore(Protocol):
    def find_slots(
        self, prospect_id: str, provider_role: str, *, limit: int = 3, after: datetime | None = None
    ) -> list[dict[str, Any]]: ...

    def get_slot(self, slot_id: str) -> dict[str, Any] | None: ...

    def book(self, slot_id: str, booking: dict[str, Any]) -> dict[str, Any]: ...

    def seed(self, prospect_id: str, slots: list[dict[str, Any]]) -> None: ...

    def record_call(self, call: dict[str, Any]) -> None: ...


# ---------------------------------------------------------------------------
# Supabase (PostgREST)
# ---------------------------------------------------------------------------
class SupabaseStore:
    def __init__(self, url: str, service_key: str, *, client: httpx.Client | None = None) -> None:
        self._base = url.rstrip("/") + "/rest/v1"
        self._headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        }
        self._client = client or httpx.Client(timeout=5.0)

    def _get(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = self._client.get(
            f"{self._base}/{table}", params=params, headers=self._headers
        )
        response.raise_for_status()
        return list(response.json())

    def find_slots(
        self, prospect_id: str, provider_role: str, *, limit: int = 3, after: datetime | None = None
    ) -> list[dict[str, Any]]:
        cutoff = (after or datetime.now(UTC)).isoformat()
        return self._get(
            SLOTS,
            {
                "prospect_id": f"eq.{prospect_id}",
                "provider_role": f"eq.{provider_role}",
                "status": "eq.open",
                "starts_at": f"gte.{cutoff}",
                "order": "starts_at.asc",
                "limit": str(limit),
            },
        )

    def get_slot(self, slot_id: str) -> dict[str, Any] | None:
        rows = self._get(SLOTS, {"id": f"eq.{slot_id}", "limit": "1"})
        return rows[0] if rows else None

    def book(self, slot_id: str, booking: dict[str, Any]) -> dict[str, Any]:
        # Conditional update: `status=eq.open` in the filter makes the claim atomic,
        # so two callers racing for the last Wednesday slot cannot both win.
        response = self._client.patch(
            f"{self._base}/{SLOTS}",
            params={"id": f"eq.{slot_id}", "status": "eq.open"},
            json={"status": "booked"},
            headers={**self._headers, "Prefer": "return=representation"},
        )
        response.raise_for_status()
        claimed = response.json()
        if not claimed:
            raise SlotUnavailable(slot_id)

        record = {**booking, "id": str(uuid.uuid4()), "slot_id": slot_id}
        created = self._client.post(
            f"{self._base}/{BOOKINGS}",
            json=record,
            headers={**self._headers, "Prefer": "return=representation"},
        )
        created.raise_for_status()
        return {**claimed[0], "booking": record}

    def seed(self, prospect_id: str, slots: list[dict[str, Any]]) -> None:
        self._client.delete(
            f"{self._base}/{SLOTS}",
            params={"prospect_id": f"eq.{prospect_id}"},
            headers=self._headers,
        ).raise_for_status()
        if slots:
            self._client.post(
                f"{self._base}/{SLOTS}", json=slots, headers=self._headers
            ).raise_for_status()

    def record_call(self, call: dict[str, Any]) -> None:
        self._client.post(
            f"{self._base}/{CALLS}",
            json=call,
            headers={**self._headers, "Prefer": "resolution=merge-duplicates"},
        ).raise_for_status()


# ---------------------------------------------------------------------------
# In-memory
# ---------------------------------------------------------------------------
class MemoryStore:
    def __init__(self) -> None:
        self.slots: dict[str, dict[str, Any]] = {}
        self.bookings: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def find_slots(
        self, prospect_id: str, provider_role: str, *, limit: int = 3, after: datetime | None = None
    ) -> list[dict[str, Any]]:
        cutoff = after or datetime.now(UTC)
        matches = [
            s
            for s in self.slots.values()
            if s["prospect_id"] == prospect_id
            and s["provider_role"] == provider_role
            and s["status"] == "open"
            and _parse(s["starts_at"]) >= cutoff
        ]
        matches.sort(key=lambda s: s["starts_at"])
        return matches[:limit]

    def get_slot(self, slot_id: str) -> dict[str, Any] | None:
        return self.slots.get(slot_id)

    def book(self, slot_id: str, booking: dict[str, Any]) -> dict[str, Any]:
        slot = self.slots.get(slot_id)
        if slot is None or slot["status"] != "open":
            raise SlotUnavailable(slot_id)
        slot["status"] = "booked"
        record = {**booking, "id": str(uuid.uuid4()), "slot_id": slot_id}
        self.bookings.append(record)
        return {**slot, "booking": record}

    def seed(self, prospect_id: str, slots: list[dict[str, Any]]) -> None:
        self.slots = {k: v for k, v in self.slots.items() if v["prospect_id"] != prospect_id}
        for slot in slots:
            self.slots[slot["id"]] = dict(slot)

    def record_call(self, call: dict[str, Any]) -> None:
        self.calls.append(call)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


_store: CalendarStore | None = None


def get_store() -> CalendarStore:
    global _store
    if _store is None:
        settings = get_settings()
        _store = (
            SupabaseStore(settings.supabase_url, settings.supabase_service_key)
            if settings.supabase_configured
            else MemoryStore()
        )
    return _store


def set_store(store: CalendarStore | None) -> None:
    global _store
    _store = store


# ---------------------------------------------------------------------------
# Seeding a realistic week
# ---------------------------------------------------------------------------
def generate_week(
    prospect_id: str,
    hours: dict[str, str],
    providers: list[dict[str, Any]],
    timezone: str = "America/Chicago",
    *,
    start: datetime | None = None,
    weeks: int = 2,
) -> list[dict[str, Any]]:
    """Build a plausible two weeks of slots: some open, some already taken.

    A calendar that is wall-to-wall open reads as a practice with no patients,
    which is the opposite of the impression the demo wants. Friday afternoons and
    the first two days are deliberately busy.
    """
    tz = ZoneInfo(timezone)
    day_names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    now = start or datetime.now(tz)
    cursor = now.replace(hour=0, minute=0, second=0, microsecond=0)
    out: list[dict[str, Any]] = []

    for offset in range(weeks * 7):
        day = cursor + timedelta(days=offset)
        span = hours.get(day_names[day.weekday()], "closed")
        if span == "closed":
            continue
        open_at, close_at = span.split("-")
        oh, om = (int(x) for x in open_at.split(":"))
        ch, cm = (int(x) for x in close_at.split(":"))
        slot_time = day.replace(hour=oh, minute=om)
        day_end = day.replace(hour=ch, minute=cm)

        while slot_time < day_end:
            for index, provider in enumerate(providers):
                # Deterministic "busy" pattern: no randomness, so a rehearsal run
                # and the live demo show the same calendar.
                busy = (
                    (day.weekday() == 4 and slot_time.hour >= 12)  # Friday afternoons
                    or (offset < 2)  # the next two days are nearly full
                    or ((slot_time.hour + index + offset) % 3 == 0)
                )
                if slot_time <= now:
                    busy = True
                out.append(
                    {
                        "id": str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"{prospect_id}:{slot_time.isoformat()}:{provider['name']}",
                            )
                        ),
                        "prospect_id": prospect_id,
                        "starts_at": slot_time.astimezone(UTC).isoformat(),
                        "local_time": slot_time.isoformat(),
                        "provider_name": provider["name"],
                        "provider_role": provider.get("role", "dentist"),
                        "duration_minutes": 60,
                        "status": "booked" if busy else "open",
                    }
                )
            slot_time += timedelta(minutes=60)
    return out
