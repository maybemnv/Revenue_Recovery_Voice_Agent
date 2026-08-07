"""Provider retry boundaries: only replay-safe requests may be repeated."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from apps.api.resilience import RetryPolicy
from apps.api.tools.calcom import CalcomClient, CalcomError
from apps.api.tools.crm import HubSpotCRM


class ResponseSequence:
    def __init__(self, responses: list[tuple[int, dict[str, Any]]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    async def request(self, method: str, url: str, **_: Any) -> httpx.Response:
        self.calls.append((method, url))
        status, body = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        return httpx.Response(
            status,
            json=body,
            request=httpx.Request(method, url),
        )


ZERO_DELAY = RetryPolicy(name="test", attempts=2, base_ms=0, cap_ms=0)


async def test_calcom_does_not_retry_booking_creation() -> None:
    client = ResponseSequence([(503, {})])
    calcom = CalcomClient(
        api_key="cal_test",
        base_url="https://cal.test",
        client=client,  # type: ignore[arg-type]
    )

    with pytest.raises(CalcomError):
        await calcom.create_booking(
            event_type_id=1,
            start=datetime(2026, 8, 5, 15, tzinfo=UTC),
            attendee_name="Alex Caller",
            attendee_phone="+13125551111",
            timezone="America/Chicago",
            idempotency_key="call-1",
        )

    assert client.calls == [("POST", "https://cal.test/bookings")]


async def test_calcom_does_not_retry_reservation_creation() -> None:
    client = ResponseSequence([(503, {})])
    calcom = CalcomClient(
        api_key="cal_test",
        base_url="https://cal.test",
        client=client,  # type: ignore[arg-type]
    )

    result = await calcom.reserve_slot(
        event_type_id=1,
        slot_start=datetime(2026, 8, 5, 15, tzinfo=UTC),
        duration_minutes=60,
    )

    assert result is None
    assert client.calls == [("POST", "https://cal.test/slots/reservations")]


async def test_hubspot_search_post_remains_retryable() -> None:
    client = ResponseSequence([(503, {}), (200, {"results": []})])
    crm = HubSpotCRM(
        access_token="hubspot_test",
        base_url="https://hubspot.test",
        client=client,  # type: ignore[arg-type]
        retry_policy=ZERO_DELAY,
    )

    result = await crm.find_by_phone("+13125551111")

    assert result is None
    assert client.calls == [
        ("POST", "https://hubspot.test/crm/v3/objects/contacts/search"),
        ("POST", "https://hubspot.test/crm/v3/objects/contacts/search"),
    ]


async def test_hubspot_contact_creation_post_is_not_retried() -> None:
    client = ResponseSequence([(200, {"results": []}), (503, {})])
    crm = HubSpotCRM(
        access_token="hubspot_test",
        base_url="https://hubspot.test",
        client=client,  # type: ignore[arg-type]
        retry_policy=ZERO_DELAY,
    )

    with pytest.raises(RuntimeError):
        await crm.upsert_contact(phone_e164="+13125551111", full_name="Alex Caller")

    assert client.calls == [
        ("POST", "https://hubspot.test/crm/v3/objects/contacts/search"),
        ("POST", "https://hubspot.test/crm/v3/objects/contacts"),
    ]


async def test_hubspot_call_creation_post_is_not_retried() -> None:
    client = ResponseSequence([(503, {})])
    crm = HubSpotCRM(
        access_token="hubspot_test",
        base_url="https://hubspot.test",
        client=client,  # type: ignore[arg-type]
        retry_policy=ZERO_DELAY,
    )

    result = await crm.log_call(
        crm_id="42",
        summary="completed",
        outcome="booked",
        duration_seconds=30,
    )

    assert result is None
    assert client.calls == [("POST", "https://hubspot.test/crm/v3/objects/calls")]


async def test_hubspot_contact_patch_remains_retryable() -> None:
    client = ResponseSequence(
        [
            (
                200,
                {
                    "results": [
                        {
                            "id": "42",
                            "properties": {"phone": "+13125551111"},
                        }
                    ]
                },
            ),
            (503, {}),
            (200, {"id": "42", "properties": {"phone": "+13125551111"}}),
        ]
    )
    crm = HubSpotCRM(
        access_token="hubspot_test",
        base_url="https://hubspot.test",
        client=client,  # type: ignore[arg-type]
        retry_policy=ZERO_DELAY,
    )

    result = await crm.upsert_contact(phone_e164="+13125551111", full_name="Alex Caller")

    assert result.crm_id == "42"
    assert client.calls == [
        ("POST", "https://hubspot.test/crm/v3/objects/contacts/search"),
        ("PATCH", "https://hubspot.test/crm/v3/objects/contacts/42"),
        ("PATCH", "https://hubspot.test/crm/v3/objects/contacts/42"),
    ]
