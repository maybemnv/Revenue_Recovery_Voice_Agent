from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from apps.api.tools import booking, service_area
from apps.api.tools.availability import check_availability
from apps.api.tools.dispatch import FunctionCall, dispatch_with_masking
from apps.api.tools.factory import ALL_TOOL_NAMES, build_registry
from apps.api.tools.registry import ToolRegistry, ToolSpec, failure, ok, run_tool


class FakeRealtime:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    async def say_out_of_band(self, line: str) -> None:
        self.sent.append(("filler", line))

    async def send_function_output(self, call_id: str, output: str) -> None:
        self.sent.append(("output", call_id, output))

    async def create_response(self, response: dict[str, Any] | None = None) -> None:
        self.sent.append(("response", response))


class FakeCalcom:
    def __init__(self) -> None:
        self.created = 0

    async def search_slots(self, **kwargs: Any) -> list[Any]:
        return []

    async def create_booking(self, **kwargs: Any) -> booking.Booking:
        self.created += 1
        await asyncio.sleep(0)
        return booking.Booking("b1", datetime(2026, 8, 5, 15, tzinfo=UTC), "accepted")


async def _raise(**_: Any) -> Any:
    raise RuntimeError("provider exploded")


async def _success(**_: Any) -> Any:
    return ok({"value": 1})


def _spec(name: str, timeout_ms: int, handler: Any, *, on_failure: str = "degrade") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="test",
        json_schema={"type": "object"},
        handler=handler,
        timeout_ms=timeout_ms,
        on_failure=on_failure,  # type: ignore[arg-type]
        filler_phrase="Please hold." if timeout_ms > 250 else None,
    )


async def test_handler_exception_is_a_safe_envelope() -> None:
    invocation = await run_tool(_spec("broken", 100, _raise), {})
    assert invocation.result["status"] == "unavailable"
    assert invocation.result["speak_hint"]
    assert invocation.attempts == 2


async def test_dispatch_masks_only_slow_tools() -> None:
    realtime = FakeRealtime()
    registry = ToolRegistry([_spec("slow", 600, _success), _spec("fast", 100, _success)])
    slow = await dispatch_with_masking(
        FunctionCall("slow", "call-1", {}), registry=registry, realtime=realtime
    )
    assert slow.filler_played is True
    assert [item[0] for item in realtime.sent] == ["filler", "output", "response"]

    realtime.sent.clear()
    fast = await dispatch_with_masking(
        FunctionCall("fast", "call-2", {}), registry=registry, realtime=realtime
    )
    assert fast.filler_played is False
    assert [item[0] for item in realtime.sent] == ["output", "response"]


async def test_unknown_tool_is_denied_without_an_exception() -> None:
    realtime = FakeRealtime()
    invocation = await dispatch_with_masking(
        FunctionCall("not_enabled", "call-1", {}),
        registry=ToolRegistry(),
        realtime=realtime,
    )
    assert invocation.result["status"] == "denied"
    assert invocation.result["speak_hint"]


def test_factory_filters_the_six_tools(config) -> None:
    registry = build_registry(config, session_factory=lambda: None, calcom=FakeCalcom())
    assert set(registry.names()) == set(config.tools_enabled)
    assert set(ALL_TOOL_NAMES) == {
        "check_service_area",
        "check_availability",
        "book_appointment",
        "lookup_knowledge",
        "transfer_to_human",
        "send_payment_link",
    }


async def test_service_area_is_pure_and_address_aware(config) -> None:
    in_area = await service_area.check_service_area(
        config=config, address="2119 N Halsted, Chicago IL 60601"
    )
    out_area = await service_area.check_service_area(config=config, postcode="99999")
    missing = await service_area.check_service_area(config=config)
    assert in_area["data"]["in_area"] is True
    assert out_area["data"]["in_area"] is False
    assert missing["status"] == "not_found"


async def test_identical_concurrent_bookings_create_once(config) -> None:
    booking.reset_guard()
    client = FakeCalcom()
    args = {
        "config": config,
        "client": client,
        "call_id": "call-1",
        "from_e164": "+13125551111",
        "slot_start": "2026-08-05T15:00:00+00:00",
        "caller_name": "Alex Caller",
    }
    results = await asyncio.gather(
        booking.book_appointment(**args), booking.book_appointment(**args)
    )
    assert client.created == 1
    assert all(result["status"] == "ok" for result in results)
    assert sum(bool(result["data"]["deduplicated"]) for result in results) == 1


async def test_availability_below_provider_data_never_invents_a_slot(config) -> None:
    result = await check_availability(
        config=config,
        client=FakeCalcom(),
        urgency="soon",
        now=datetime(2026, 8, 4, 12, tzinfo=UTC),
    )
    assert result["status"] == "not_found"
    assert result["speak_hint"]


def test_failure_rejects_missing_speak_hint() -> None:
    try:
        failure("unavailable", "")
    except ValueError:
        pass
    else:
        raise AssertionError("missing failure hint was accepted")
