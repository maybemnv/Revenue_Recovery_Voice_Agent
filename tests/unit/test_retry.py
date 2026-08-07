"""Retry behaviour: what gets retried, what must not, and how long it can take.

Three properties matter more than the mechanics.

*Jitter must actually spread.* A deterministic retry schedule turns one provider
blip into a synchronised stampede, so the delay is asserted to be a distribution
rather than a value.

*The in-call profile must fit inside a tool budget.* `run_tool` wraps every
handler in `asyncio.wait_for(spec.timeout_ms)`; if the retry could out-sleep the
smallest budget it would convert a recoverable blip into a guaranteed timeout.
That bound is asserted against the real specs rather than a copy of the numbers.

*A non-idempotent write must not be retried on an ambiguous failure.* A read
timeout from Twilio can mean the SMS went out and the response was lost. Texting
the caller twice is worse than reporting the first attempt as failed.

The behavioural tests run through `ZERO`, a policy with a zero-width backoff
window: the real retry loop executes, `sleep(0)` costs nothing, and the timing
maths is unit-tested separately instead of being waited on.
"""

from __future__ import annotations

import random

import httpx
import pytest

from apps.api.resilience import (
    BACKGROUND,
    IN_CALL,
    PRE_DELIVERY_STATUS,
    RETRYABLE_STATUS,
    UNSAFE_WRITE,
    RetryPolicy,
    celery_countdown,
    request_with_retry,
    request_with_retry_sync,
    retry_after_ms,
)


class _AlwaysMax(random.Random):
    """Pins full jitter to the top of its window, to assert the window itself."""

    def random(self) -> float:
        return 1.0


ZERO = RetryPolicy(name="test", attempts=3, base_ms=0, cap_ms=0)
ZERO_UNSAFE = RetryPolicy(
    name="test_unsafe", attempts=3, base_ms=0, cap_ms=0, retry_when_ambiguous=False
)


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers or {}, request=httpx.Request("POST", "https://x"))


class Sequence:
    """Yields each scripted outcome in turn, raising the ones that are exceptions.

    Runs out into 200s, so a test only has to script the failures it cares about.
    """

    def __init__(self, *outcomes: httpx.Response | Exception) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def __call__(self) -> httpx.Response:
        self.calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else _response(200)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def asend(self) -> httpx.Response:
        return self()


async def test_transient_status_is_retried_then_succeeds() -> None:
    send = Sequence(_response(503))

    response = await request_with_retry(send.asend, label="t", policy=ZERO)

    assert (response.status_code, send.calls) == (200, 2)


async def test_a_verdict_is_not_retried() -> None:
    """404 is an answer, not a blip."""
    send = Sequence(_response(404))

    response = await request_with_retry(send.asend, label="t", policy=ZERO)

    assert (response.status_code, send.calls) == (404, 1)


async def test_exhausted_attempts_return_the_failure_rather_than_raising() -> None:
    """Callers inspect `status_code` to build a ToolResult; raising would bypass that."""
    send = Sequence(*[_response(503)] * 5)

    response = await request_with_retry(send.asend, label="t", policy=ZERO)

    assert (response.status_code, send.calls) == (503, ZERO.attempts)


async def test_transport_error_is_retried_and_the_last_one_propagates() -> None:
    send = Sequence(*[httpx.ConnectError("down")] * 5)

    with pytest.raises(httpx.ConnectError):
        await request_with_retry(send.asend, label="t", policy=ZERO)

    assert send.calls == ZERO.attempts


@pytest.mark.parametrize("status", sorted(RETRYABLE_STATUS))
async def test_every_declared_retryable_status_is_retried(status: int) -> None:
    send = Sequence(_response(status))

    response = await request_with_retry(send.asend, label="t", policy=ZERO)

    assert (response.status_code, send.calls) == (200, 2)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
async def test_client_errors_are_never_retried(status: int) -> None:
    send = Sequence(_response(status))

    await request_with_retry(send.asend, label="t", policy=ZERO)

    assert send.calls == 1


def test_sync_twin_retries_the_same_way() -> None:
    send = Sequence(_response(500), _response(429))

    response = request_with_retry_sync(send, label="t", policy=ZERO)

    assert (response.status_code, send.calls) == (200, 3)


def test_sync_twin_propagates_a_final_transport_error() -> None:
    send = Sequence(*[httpx.ReadTimeout("slow")] * 5)

    with pytest.raises(httpx.ReadTimeout):
        request_with_retry_sync(send, label="t", policy=ZERO)

    assert send.calls == ZERO.attempts


# --- the non-idempotent write guard ------------------------------------------


@pytest.mark.parametrize("status", [500, 502, 504, 408])
async def test_unsafe_write_is_not_retried_on_an_ambiguous_status(status: int) -> None:
    """A 502 from an SMS send may mean it was accepted and the response was lost."""
    send = Sequence(_response(status))

    response = await request_with_retry(send.asend, label="sms", policy=ZERO_UNSAFE)

    assert (response.status_code, send.calls) == (status, 1)


@pytest.mark.parametrize("status", sorted(PRE_DELIVERY_STATUS))
async def test_unsafe_write_is_retried_when_the_provider_refused(status: int) -> None:
    """429 and 503 are proof the message was never queued, so a resend is safe."""
    send = Sequence(_response(status))

    response = await request_with_retry(send.asend, label="sms", policy=ZERO_UNSAFE)

    assert (response.status_code, send.calls) == (200, 2)


async def test_unsafe_write_is_not_retried_on_a_read_timeout() -> None:
    """The classic duplicate-SMS bug: the send landed, the response did not."""
    send = Sequence(httpx.ReadTimeout("lost"))

    with pytest.raises(httpx.ReadTimeout):
        await request_with_retry(send.asend, label="sms", policy=ZERO_UNSAFE)

    assert send.calls == 1


async def test_unsafe_write_is_retried_when_the_connection_never_opened() -> None:
    send = Sequence(httpx.ConnectError("refused"))

    response = await request_with_retry(send.asend, label="sms", policy=ZERO_UNSAFE)

    assert (response.status_code, send.calls) == (200, 2)


# --- jitter -------------------------------------------------------------------


def test_jitter_spreads_across_the_whole_window() -> None:
    """Full jitter, not a nudge: draws should cover the window, not cluster in it."""
    rng = random.Random(1234)
    draws = [BACKGROUND.delay_ms(2, rng) for _ in range(400)]
    window = min(BACKGROUND.cap_ms, BACKGROUND.base_ms * 2)

    assert all(0 <= d <= window for d in draws)
    # A deterministic or narrowly-jittered schedule fails both of these.
    assert min(draws) < window * 0.1
    assert max(draws) > window * 0.9


def test_backoff_grows_until_it_hits_the_cap() -> None:
    """The window doubles per attempt, then stops — an unbounded wait is a hang."""
    windows = [BACKGROUND.delay_ms(n, _AlwaysMax()) for n in range(1, 8)]

    assert windows[0] == BACKGROUND.base_ms
    assert windows[1] == BACKGROUND.base_ms * 2
    assert windows == sorted(windows)
    assert max(windows) == BACKGROUND.cap_ms


def test_the_in_call_profile_cannot_outlast_the_smallest_tool_budget(config) -> None:
    """The retry has to fit inside the `wait_for` that wraps every handler.

    Otherwise a retry that would have recovered is cancelled mid-sleep, having
    spent the budget on waiting rather than on a second attempt. Read off the
    real specs so a future tool with a tighter budget fails here.
    """
    from apps.api.tools.factory import build_registry

    registry = build_registry(config, session_factory=None)
    budgets = [registry[name].timeout_ms for name in registry.names()]
    assert budgets, "no tool specs to bound the retry against"

    worst_case = sum(IN_CALL.delay_ms(n, _AlwaysMax()) for n in range(1, IN_CALL.attempts))
    # Half the tightest budget, so a retry still leaves room for the attempt.
    assert worst_case < min(budgets) / 2


# --- Retry-After --------------------------------------------------------------


def test_retry_after_seconds_is_honoured_and_clamped() -> None:
    assert retry_after_ms(_response(429, {"retry-after": "0.2"}), cap_ms=1000) == 200
    # A rate limiter asking for a minute cannot be obeyed inside a live call.
    assert retry_after_ms(_response(429, {"retry-after": "60"}), cap_ms=1000) == 1000


def test_retry_after_http_date_is_understood() -> None:
    past = retry_after_ms(
        _response(503, {"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}), cap_ms=5000
    )
    assert past == 0.0


def test_missing_or_junk_retry_after_falls_back_to_jitter() -> None:
    assert retry_after_ms(_response(429), cap_ms=1000) is None
    assert retry_after_ms(_response(429, {"retry-after": "soon"}), cap_ms=1000) is None


async def test_retry_after_is_preferred_over_the_jitter_window() -> None:
    """A provider's own instruction wins, which is the point of honouring it."""
    send = Sequence(_response(429, {"retry-after": "0"}))

    response = await request_with_retry(send.asend, label="t", policy=ZERO)

    assert (response.status_code, send.calls) == (200, 2)


# --- Celery ------------------------------------------------------------------


def test_celery_countdown_is_jittered_bounded_and_never_zero() -> None:
    """A countdown of zero re-runs the task immediately — a failing task hot loop."""
    countdowns = {celery_countdown(2, base_seconds=10) for _ in range(200)}

    assert len(countdowns) > 1, "a fixed countdown synchronises every worker"
    assert min(countdowns) >= 1
    assert max(countdowns) <= 40


def test_celery_countdown_respects_its_cap() -> None:
    assert celery_countdown(20, base_seconds=30, cap_seconds=600) <= 600


def test_policies_are_distinguishable_in_logs() -> None:
    """The `policy` field is how a retry storm gets attributed to a regime."""
    names = {IN_CALL.name, BACKGROUND.name, UNSAFE_WRITE.name}
    assert len(names) == 3
