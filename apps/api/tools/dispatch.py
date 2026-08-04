"""Dispatch a model function call while keeping the line alive.

A calendar lookup costs 400-900 ms and silence on a phone line reads as a
dropped call. GA's out-of-band response (`input: []`) is exactly the primitive
needed: speak a fixed line without it entering conversation state, so the model
cannot improvise a fake answer while the real one is still in flight.

Fillers are constants, never model-generated — a generated filler is one more
inference round-trip inside the window we are trying to hide.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from apps.api.observability.logging import get_logger
from apps.api.tools.registry import Invocation, ToolRegistry, failure, run_tool

log = get_logger(__name__)


class RealtimeLike(Protocol):
    async def say_out_of_band(self, line: str) -> None: ...
    async def send_function_output(self, call_id: str, output: str) -> None: ...
    async def create_response(self, response: dict[str, Any] | None = None) -> None: ...


@dataclass(frozen=True, slots=True)
class FunctionCall:
    name: str
    call_id: str
    arguments: dict[str, Any]

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> FunctionCall:
        raw = event.get("arguments") or "{}"
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except json.JSONDecodeError:
            log.warning("function_call_bad_arguments", name=event.get("name"))
            arguments = {}
        return cls(
            name=event.get("name", ""),
            call_id=event.get("call_id", ""),
            arguments=arguments,
        )


UNKNOWN_TOOL = failure(
    "denied",
    "That is not something this line can do. Offer to take a message instead.",
)


async def dispatch_with_masking(
    call: FunctionCall,
    *,
    registry: ToolRegistry,
    realtime: RealtimeLike,
    context: dict[str, Any] | None = None,
    on_invocation: Callable[[Invocation], Awaitable[None]] | None = None,
) -> Invocation:
    spec = registry.get(call.name)
    if spec is None:
        # A model hallucinating a tool name is a `denied`, not a crash.
        log.warning("unknown_tool_called", tool=call.name)
        invocation = Invocation(
            name=call.name, arguments=call.arguments, result=UNKNOWN_TOOL, latency_ms=0
        )
        await _return_to_model(realtime, call, invocation)
        if on_invocation is not None:
            await on_invocation(invocation)
        return invocation

    filler_played = False
    if spec.masks_latency:
        # Fire before awaiting the handler — the whole point is to occupy the
        # window, not to report on it afterwards.
        assert spec.filler_phrase is not None  # noqa: S101 - masks_latency implies it
        await realtime.say_out_of_band(spec.filler_phrase)
        filler_played = True

    invocation = await run_tool(spec, call.arguments, context=context)
    invocation.filler_played = filler_played

    log.info(
        "tool_dispatched",
        tool=spec.name,
        status=invocation.result["status"],
        latency_ms=invocation.latency_ms,
        attempts=invocation.attempts,
        filler=filler_played,
    )

    await _return_to_model(realtime, call, invocation)
    if on_invocation is not None:
        await on_invocation(invocation)
    return invocation


async def _return_to_model(
    realtime: RealtimeLike, call: FunctionCall, invocation: Invocation
) -> None:
    await realtime.send_function_output(call.call_id, json.dumps(invocation.result))
    await realtime.create_response()
