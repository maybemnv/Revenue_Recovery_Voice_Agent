"""Dashboard query contracts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

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
