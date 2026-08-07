"""Retry with full jitter, for every outbound call this service makes.

Two profiles, because the two regimes have opposite failure costs.

`IN_CALL` runs while a caller is on the line. It is deliberately tiny — two
attempts, tens of milliseconds of backoff — because `run_tool` has already
wrapped the handler in `asyncio.wait_for(spec.timeout_ms)`. That wrapper is the
real ceiling, which is what makes a retry here safe: it can only ever spend
budget the tool was already allowed, so the worst case is the timeout the caller
would have hit anyway and the best case turns a connection reset into a
completed booking. Anything slower would be cancelled mid-sleep, having spent
the budget on nothing.

`BACKGROUND` runs in Celery, where nobody is waiting and the real enemy is a
thundering herd against a provider that has just come back up. Seconds, not
milliseconds.

Jitter is the whole point. Plain `2 ** attempt` synchronises every retrying
client onto the same instants, so a provider blip comes back as a self-inflicted
spike. This uses AWS's "full jitter" — a uniform draw across the entire window
rather than a nudge around its edge — because that is what actually spreads
load.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from apps.api.observability.logging import get_logger

log = get_logger(__name__)

# 429 and 503 are a provider asking us to come back; 5xx and the timeout family
# are unfinished business. 529 is Anthropic's non-standard "overloaded", which
# is the single most common transient failure the analysis worker sees.
# Everything else — 400, 401, 404, 409 — is a verdict, and retrying a verdict
# just spends the budget to hear it again.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 529})

# `TransportError` also covers `UnsupportedProtocol`, which is a config bug
# rather than a blip, so the retryable set is named explicitly.
RETRYABLE_EXCEPTIONS = (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError)


# For a write with no idempotency key — an SMS — most failures are *ambiguous*:
# a read timeout or a 502 can mean the message was accepted and the response was
# lost, so retrying risks texting the caller twice. These two sets are the
# failures that prove the request never took effect: the provider explicitly
# refused it, or the connection was never established.
PRE_DELIVERY_STATUS = frozenset({429, 503})
PRE_DELIVERY_EXCEPTIONS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

_rng = random.Random()


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    name: str
    attempts: int
    base_ms: int
    cap_ms: int
    # False for non-idempotent writes: retry only on proof of non-delivery.
    retry_when_ambiguous: bool = True

    def delay_ms(self, attempt: int, rng: random.Random | None = None) -> float:
        """Full jitter: uniform across the whole exponential window."""
        window = min(self.cap_ms, self.base_ms * 2 ** max(attempt - 1, 0))
        return (rng or _rng).random() * window

    def retryable_statuses(self) -> frozenset[int]:
        return RETRYABLE_STATUS if self.retry_when_ambiguous else PRE_DELIVERY_STATUS

    def retryable_exceptions(self) -> tuple[type[Exception], ...]:
        return RETRYABLE_EXCEPTIONS if self.retry_when_ambiguous else PRE_DELIVERY_EXCEPTIONS


# The backoff is sized against the *tightest* tool budget (100ms), not the ones
# that actually make HTTP calls, so no future tool can be given a budget this
# profile would spend on sleeping. `test_retry.py` asserts that bound.
IN_CALL = RetryPolicy(name="in_call", attempts=2, base_ms=25, cap_ms=100)
BACKGROUND = RetryPolicy(name="background", attempts=3, base_ms=500, cap_ms=4000)
# Twilio REST sends. Two attempts is enough to ride out a rate limit without
# turning one confirmation into a thread of them.
UNSAFE_WRITE = RetryPolicy(
    name="unsafe_write", attempts=2, base_ms=200, cap_ms=1000, retry_when_ambiguous=False
)


def retry_after_ms(response: httpx.Response, *, cap_ms: int) -> float | None:
    """Honour `Retry-After` when the provider sends one, clamped to our cap.

    Clamped because a rate limiter that asks for 60 seconds is not something a
    live call can wait for, and an unbounded sleep inside a tool budget would be
    cancelled anyway. Accepts both the delay-seconds and HTTP-date forms.
    """
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return min(float(raw) * 1000, float(cap_ms))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    remaining_ms = (when - datetime.now(UTC)).total_seconds() * 1000
    return max(0.0, min(remaining_ms, float(cap_ms)))


def _should_retry(response: httpx.Response, policy: RetryPolicy) -> bool:
    return response.status_code in policy.retryable_statuses()


def _log_retry(label: str, policy: RetryPolicy, attempt: int, delay_ms: float, why: str) -> None:
    log.warning(
        "outbound_retry",
        target=label,
        policy=policy.name,
        attempt=attempt,
        delay_ms=round(delay_ms),
        reason=why,
    )


async def request_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    label: str,
    policy: RetryPolicy = IN_CALL,
) -> httpx.Response:
    """Run `send` until it returns a non-retryable response or attempts run out.

    A failing *response* is returned rather than raised — every client here
    already inspects `status_code` and turns it into a `ToolResult`, and a retry
    helper that started raising would route around that. Only a transport error
    with no response at all propagates, and only from the final attempt.
    """
    last_exc: Exception | None = None
    for attempt in range(1, policy.attempts + 1):
        final = attempt == policy.attempts
        try:
            response = await send()
        except policy.retryable_exceptions() as exc:
            last_exc = exc
            if final:
                raise
            delay = policy.delay_ms(attempt)
            _log_retry(label, policy, attempt, delay, type(exc).__name__)
            await asyncio.sleep(delay / 1000)
            continue

        if final or not _should_retry(response, policy):
            return response

        requested = retry_after_ms(response, cap_ms=policy.cap_ms)
        delay = requested if requested is not None else policy.delay_ms(attempt)
        _log_retry(label, policy, attempt, delay, f"http_{response.status_code}")
        await asyncio.sleep(delay / 1000)

    raise AssertionError(f"unreachable: {label} exhausted without a result ({last_exc!r})")


def request_with_retry_sync(
    send: Callable[[], httpx.Response],
    *,
    label: str,
    policy: RetryPolicy = BACKGROUND,
) -> httpx.Response:
    """Blocking twin of `request_with_retry`, for the Celery workers.

    The workers use `httpx.Client`, and wrapping them in an event loop purely to
    share one code path would buy nothing but a way to deadlock a prefork pool.
    """
    last_exc: Exception | None = None
    for attempt in range(1, policy.attempts + 1):
        final = attempt == policy.attempts
        try:
            response = send()
        except policy.retryable_exceptions() as exc:
            last_exc = exc
            if final:
                raise
            delay = policy.delay_ms(attempt)
            _log_retry(label, policy, attempt, delay, type(exc).__name__)
            time.sleep(delay / 1000)
            continue

        if final or not _should_retry(response, policy):
            return response

        requested = retry_after_ms(response, cap_ms=policy.cap_ms)
        delay = requested if requested is not None else policy.delay_ms(attempt)
        _log_retry(label, policy, attempt, delay, f"http_{response.status_code}")
        time.sleep(delay / 1000)

    raise AssertionError(f"unreachable: {label} exhausted without a result ({last_exc!r})")


def celery_countdown(retries: int, *, base_seconds: int, cap_seconds: int = 600) -> int:
    """Jittered countdown for `self.retry`, replacing linear `base * (n + 1)`.

    Celery's own `retry_jitter` only applies to autoretry with backoff enabled;
    these tasks pass an explicit countdown, so the jitter has to be applied here.
    Floor of one second — a countdown of zero re-runs the task immediately, which
    is how a failing task becomes a hot loop.
    """
    window = min(cap_seconds, base_seconds * 2**retries)
    return max(1, int(_rng.random() * window))
