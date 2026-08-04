"""The tool envelope, and the registry that binds names to handlers.

`ToolResult` is the mechanism that stops the agent inventing a booking that did
not happen. The model never sees an exception, a traceback, or an HTTP status —
it sees a status and, when that status is not `ok`, an explicit `speak_hint`
telling it what to say. Hoping a model handles an error string gracefully is not
a design; this is.

Every handler is wrapped so that a raise becomes `unavailable` with a hint. A
handler that throws is a bug, but a bug must not become the agent confidently
confirming an appointment.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from apps.api.observability.logging import get_logger

log = get_logger(__name__)

ToolStatus = Literal["ok", "not_found", "unavailable", "denied"]
FailurePolicy = Literal["retry_once", "degrade", "escalate"]

# Below this, masking makes the agent sound slower than it is.
FILLER_THRESHOLD_MS = 250


class ToolResult(TypedDict):
    status: ToolStatus
    data: dict[str, Any] | None
    speak_hint: str | None


ToolHandler = Callable[..., Awaitable[ToolResult]]


def ok(data: dict[str, Any] | None = None) -> ToolResult:
    return {"status": "ok", "data": data, "speak_hint": None}


def failure(status: ToolStatus, speak_hint: str, data: dict[str, Any] | None = None) -> ToolResult:
    """Every non-`ok` result carries a hint. That invariant is asserted in tests."""
    if status == "ok":
        raise ValueError("failure() is for non-ok statuses")
    if not speak_hint:
        raise ValueError("a non-ok ToolResult must carry a speak_hint")
    return {"status": status, "data": data, "speak_hint": speak_hint}


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    json_schema: dict[str, Any]
    handler: ToolHandler
    timeout_ms: int
    on_failure: FailurePolicy
    filler_phrase: str | None = None
    idempotency_key: Callable[[dict[str, Any]], str] | None = None

    @property
    def masks_latency(self) -> bool:
        return self.timeout_ms > FILLER_THRESHOLD_MS and bool(self.filler_phrase)

    def to_openai_tool(self) -> dict[str, Any]:
        """GA function-tool shape for `session.update`."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.json_schema,
        }


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool {spec.name!r} already registered")
        self._specs[spec.name] = spec

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __getitem__(self, name: str) -> ToolSpec:
        return self._specs[name]

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return list(self._specs)

    def filtered(self, enabled: list[str]) -> ToolRegistry:
        """Per-call view driven by `tools_enabled` in the client YAML.

        A name in YAML that has no implementation is dropped with a warning
        rather than raising: a typo in a config file should not take a phone
        line down.
        """
        out = ToolRegistry()
        for name in enabled:
            spec = self._specs.get(name)
            if spec is None:
                log.warning("tool_enabled_but_unimplemented", tool=name)
                continue
            out.register(spec)
        return out

    def openai_tools(self) -> list[dict[str, Any]]:
        return [spec.to_openai_tool() for spec in self._specs.values()]


@dataclass(slots=True)
class Invocation:
    """One call of one tool, including retries. Persisted to `tool_invocations`."""

    name: str
    arguments: dict[str, Any]
    result: ToolResult
    latency_ms: int
    attempts: int = 1
    filler_played: bool = False
    timed_out: bool = False


async def run_tool(
    spec: ToolSpec,
    arguments: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
) -> Invocation:
    """Execute a tool under its timeout, with its retry policy, never raising.

    `retry_once` and `degrade` both get a second attempt — the difference is what
    the model is told afterwards, which lives in the handler's own hint. The
    retry budget is deliberately half the first, matching Trace B in the PRD:
    a second full-length wait would blow the dead-air ceiling.
    """
    kwargs = {**(context or {}), **arguments}
    started = time.perf_counter()
    attempts = 0
    timed_out = False
    result: ToolResult | None = None

    budgets = [spec.timeout_ms]
    if spec.on_failure in ("retry_once", "degrade"):
        budgets.append(max(spec.timeout_ms // 2, 100))

    for budget_ms in budgets:
        attempts += 1
        try:
            result = await asyncio.wait_for(spec.handler(**kwargs), timeout=budget_ms / 1000)
        except TimeoutError:
            timed_out = True
            result = failure(
                "unavailable",
                _timeout_hint(spec),
                {"timeout_ms": budget_ms, "attempt": attempts},
            )
        except Exception as exc:  # a raise must never reach the model
            log.exception("tool_handler_raised", tool=spec.name, attempt=attempts)
            result = failure(
                "unavailable",
                _timeout_hint(spec),
                {"error": type(exc).__name__, "attempt": attempts},
            )
        if result is not None and result["status"] == "ok":
            break

    assert result is not None
    return Invocation(
        name=spec.name,
        arguments=arguments,
        result=result,
        latency_ms=int((time.perf_counter() - started) * 1000),
        attempts=attempts,
        timed_out=timed_out,
    )


def _timeout_hint(spec: ToolSpec) -> str:
    if spec.on_failure == "escalate":
        return (
            "That system is not responding. Apologise briefly and offer to put the caller "
            "through to a person."
        )
    return (
        "That system is slow right now. Do not confirm anything as done. Offer a callback "
        "within fifteen minutes and take the caller's details."
    )


@dataclass(slots=True)
class RegistryBundle:
    """The registry plus the per-call context every handler is given."""

    registry: ToolRegistry
    context: dict[str, Any] = field(default_factory=dict)
