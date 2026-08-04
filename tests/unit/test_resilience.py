from __future__ import annotations

import asyncio
from typing import Any

import pytest

from apps.api.domain.state import CallState
from apps.api.media.budget_guard import BudgetGuard, BudgetPhase
from apps.api.tools.dispatch import FunctionCall, dispatch_with_masking
from apps.api.tools.registry import ToolRegistry, ToolSpec


async def _always_timeout(**_: Any) -> Any:
    await asyncio.sleep(0.2)
    return {"status": "ok", "data": {}, "speak_hint": None}


class Realtime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def say_out_of_band(self, line: str) -> None:
        self.calls.append(("filler", line))

    async def send_function_output(self, call_id: str, output: str) -> None:
        self.calls.append(("function_output", output))

    async def create_response(self, response: dict[str, Any] | None = None) -> None:
        self.calls.append(("response", response))


async def test_timeout_never_returns_a_successful_booking_claim() -> None:
    realtime = Realtime()
    registry = ToolRegistry(
        [
            ToolSpec(
                name="book_appointment",
                description="book",
                json_schema={"type": "object"},
                handler=_always_timeout,
                timeout_ms=1,
                on_failure="degrade",
                filler_phrase="Locking that in now.",
            )
        ]
    )
    invocation = await dispatch_with_masking(
        FunctionCall("book_appointment", "fc1", {}), registry=registry, realtime=realtime
    )
    assert invocation.result["status"] == "unavailable"
    assert invocation.result["speak_hint"]
    assert '"status": "ok"' not in str(realtime.calls[0][1])


def test_budget_guard_wraps_up_once_then_expires(config) -> None:
    guard = BudgetGuard(config.budget)
    assert guard.check(1).phase is BudgetPhase.NORMAL
    assert guard.needs_wrap_up_now(155).phase is BudgetPhase.WRAP_UP
    guard.mark_wrap_up_sent()
    assert guard.needs_wrap_up_now(400) is None
    assert guard.check(200).phase is BudgetPhase.EXPIRED


@pytest.mark.parametrize(
    "text",
    ["my card is 4111 1111 1111 1111", "4111-1111-1111-1111", "4111111111111111", "cvv 123"],
)
def test_redaction_is_applied_to_caller_input(text: str) -> None:
    from apps.api.security.redaction import redact_pan

    redacted = redact_pan(text)
    assert "4111" not in redacted or "REDACTED" in redacted
    if "cvv" in text:
        assert "[REDACTED_CVV]" in redacted


def test_call_state_has_no_automatic_disposition() -> None:
    state = CallState(call_id="c", client_id="demo", from_e164="+1")
    assert not hasattr(state, "disposition")
