"""Cal.com v2 client: slot search, slot hold, and booking.

The hold primitive was the reason Cal.com was chosen over Google Calendar
(`docs/PRD.md:84`), and it exists: `POST /v2/slots/reservations` with
`cal-api-version: 2024-09-04` returns a `reservationUid` and a
`reservationUntil` expiry. Holds are still treated as best-effort — if the
endpoint answers 404/405 on a given deployment the caller falls back to
book-directly, because a missing hold degrades the experience by a few seconds
of race window and a hard failure would drop the booking entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from apps.api.observability.logging import get_logger
from apps.api.settings import get_settings

log = get_logger(__name__)

SLOTS_API_VERSION = "2024-09-04"
BOOKINGS_API_VERSION = "2024-08-13"


class CalcomError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Slot:
    start: datetime
    end: datetime | None = None

    def to_payload(self) -> dict[str, str]:
        return {"start": self.start.isoformat()}


@dataclass(frozen=True, slots=True)
class Reservation:
    uid: str
    slot_start: datetime
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class Booking:
    uid: str
    start: datetime
    status: str


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class CalcomClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key if api_key is not None else settings.calcom_api_key
        self._base = (base_url or settings.calcom_api_base).rstrip("/")
        self._client = client
        self._holds_supported = True

    def _headers(self, api_version: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "cal-api-version": api_version,
            "Content-Type": "application/json",
        }

    async def _request(
        self, method: str, path: str, *, api_version: str, **kwargs: Any
    ) -> httpx.Response:
        url = f"{self._base}{path}"
        headers = self._headers(api_version)
        if self._client is not None:
            return await self._client.request(method, url, headers=headers, **kwargs)
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.request(method, url, headers=headers, **kwargs)

    # -- slots ------------------------------------------------------------
    async def search_slots(
        self,
        *,
        event_type_id: int,
        start: datetime,
        end: datetime,
        timezone: str,
    ) -> list[Slot]:
        response = await self._request(
            "GET",
            "/slots",
            api_version=SLOTS_API_VERSION,
            params={
                "eventTypeId": event_type_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "timeZone": timezone,
            },
        )
        if response.status_code >= 400:
            raise CalcomError(f"slot search failed: {response.status_code} {response.text[:200]}")
        return _parse_slots(response.json())

    async def reserve_slot(
        self, *, event_type_id: int, slot_start: datetime, duration_minutes: int
    ) -> Reservation | None:
        """Hold a slot for the length of the conversation. None if unsupported."""
        if not self._holds_supported:
            return None
        response = await self._request(
            "POST",
            "/slots/reservations",
            api_version=SLOTS_API_VERSION,
            json={
                "eventTypeId": event_type_id,
                "slotStart": slot_start.isoformat(),
                "slotDuration": duration_minutes,
                "reservationDuration": 10,
            },
        )
        if response.status_code in (404, 405, 501):
            # This deployment does not expose holds. Stop asking for the rest of
            # the process rather than paying the round trip on every call.
            self._holds_supported = False
            log.warning("calcom_holds_unsupported", status=response.status_code)
            return None
        if response.status_code >= 400:
            log.warning("calcom_reserve_failed", status=response.status_code)
            return None
        data = response.json().get("data", {})
        until = data.get("reservationUntil")
        return Reservation(
            uid=data["reservationUid"],
            slot_start=_parse_dt(data["slotStart"]),
            expires_at=_parse_dt(until) if until else None,
        )

    async def release_slot(self, reservation_uid: str) -> None:
        """Best-effort release. A hold that outlives the call expires anyway."""
        try:
            await self._request(
                "DELETE",
                f"/slots/reservations/{reservation_uid}",
                api_version=SLOTS_API_VERSION,
            )
        except (httpx.HTTPError, CalcomError):
            log.warning("calcom_release_failed", reservation_uid=reservation_uid)

    # -- bookings ---------------------------------------------------------
    async def create_booking(
        self,
        *,
        event_type_id: int,
        start: datetime,
        attendee_name: str,
        attendee_phone: str,
        timezone: str,
        idempotency_key: str,
        notes: str | None = None,
    ) -> Booking:
        payload: dict[str, Any] = {
            "eventTypeId": event_type_id,
            "start": start.isoformat(),
            "attendee": {
                "name": attendee_name,
                "phoneNumber": attendee_phone,
                "timeZone": timezone,
                "language": "en",
            },
            "metadata": {"idempotencyKey": idempotency_key},
        }
        if notes:
            payload["bookingFieldsResponses"] = {"notes": notes}

        response = await self._request(
            "POST", "/bookings", api_version=BOOKINGS_API_VERSION, json=payload
        )
        if response.status_code >= 400:
            raise CalcomError(f"booking failed: {response.status_code} {response.text[:200]}")
        data = response.json().get("data", {})
        return Booking(
            uid=data.get("uid", ""),
            start=_parse_dt(data.get("start", start.isoformat())),
            status=data.get("status", "accepted"),
        )

    async def find_booking_by_key(self, idempotency_key: str) -> Booking | None:
        """Recover a booking made by a previous attempt of the same request."""
        response = await self._request(
            "GET",
            "/bookings",
            api_version=BOOKINGS_API_VERSION,
            params={"take": 20},
        )
        if response.status_code >= 400:
            return None
        for item in response.json().get("data", []):
            metadata = item.get("metadata") or {}
            if metadata.get("idempotencyKey") == idempotency_key:
                return Booking(
                    uid=item.get("uid", ""),
                    start=_parse_dt(item["start"]),
                    status=item.get("status", "accepted"),
                )
        return None


def _parse_slots(body: dict[str, Any]) -> list[Slot]:
    """Cal.com has shipped two slot response shapes; accept both.

    `{data: {"2026-08-05": [{start: ...}]}}` is the date-keyed form, and
    `{data: [{start: ...}]}` the flat one.
    """
    data = body.get("data", {})
    raw: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for entries in data.values():
            if isinstance(entries, list):
                raw.extend(e for e in entries if isinstance(e, dict))
    elif isinstance(data, list):
        raw = [e for e in data if isinstance(e, dict)]

    slots: list[Slot] = []
    for entry in raw:
        start = entry.get("start") or entry.get("time")
        if not start:
            continue
        end = entry.get("end")
        slots.append(Slot(start=_parse_dt(start), end=_parse_dt(end) if end else None))
    return sorted(slots, key=lambda s: s.start)
