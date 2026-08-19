"""Dashboard query contracts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from apps.api.demo.replay import fixture_replay
from apps.api.routers import dashboard
from apps.api.routers.dashboard import metrics


class _AggregateResult:
    def one(self) -> SimpleNamespace:
        return SimpleNamespace(total=1, booked=0, escalated=0, cost_cents=47, avg_seconds=30.0)


class TwoClientLatencySession:
    """A query seam with two disjoint latency populations."""

    def __init__(self, selected_client: str) -> None:
        self.selected_client = selected_client

    async def execute(self, _statement: Any) -> _AggregateResult:
        return _AggregateResult()

    async def scalar(self, statement: Any) -> int:
        compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))
        if "calls.client_id" not in compiled:
            return 900
        return 120 if self.selected_client == "northside-hvac" else 900


@pytest.mark.asyncio
async def test_metrics_p50_is_scoped_to_the_selected_client() -> None:
    """Without the predicate, Northside's 120ms p50 would mix with another client's 900ms."""
    northside = await metrics(
        session=TwoClientLatencySession("northside-hvac"),
        client_id="northside-hvac",
        days=7,  # type: ignore[arg-type]
    )
    other = await metrics(
        session=TwoClientLatencySession("other-hvac"),
        client_id="other-hvac",
        days=7,  # type: ignore[arg-type]
    )

    assert northside["p50_response_latency_ms"] == 120
    assert other["p50_response_latency_ms"] == 900


class FutureFixtureMetricsSession:
    """A query seam that applies the API's generated time window to a replay row."""

    def __init__(self, started_at: object) -> None:
        self.started_at = started_at

    @staticmethod
    def _window_start(statement: Any) -> object:
        values = statement.compile().params.values()
        return next(value for value in values if hasattr(value, "tzinfo"))

    async def execute(self, statement: Any) -> _AggregateResult:
        in_window = self.started_at >= self._window_start(statement)
        return type(
            "Result",
            (),
            {
                "one": lambda _self: SimpleNamespace(
                    total=1 if in_window else 0,
                    booked=1 if in_window else 0,
                    escalated=0,
                    cost_cents=47 if in_window else 0,
                    avg_seconds=192.0 if in_window else None,
                )
            },
        )()

    async def scalar(self, statement: Any) -> int | None:
        return 420 if self.started_at >= self._window_start(statement) else None


@pytest.mark.asyncio
async def test_metrics_include_the_current_fixture_replay_at_an_arbitrary_future_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replay fixed in 2026 silently ages out of the default seven-day API metrics window."""
    future = dashboard.datetime(2040, 2, 3, 12, 0, tzinfo=dashboard.UTC)
    replay = fixture_replay(now=future - dashboard.timedelta(days=1))
    monkeypatch.setattr(dashboard, "utc_now", lambda: future, raising=False)

    result = await metrics(
        session=FutureFixtureMetricsSession(replay.started_at),  # type: ignore[arg-type]
        client_id=replay.client_id,
        days=7,  # type: ignore[arg-type]
    )

    assert result["total_calls"] == 1
    assert result["booked"] == 1
    assert result["cost_usd"] == 0.47
    assert result["p50_response_latency_ms"] == 420
