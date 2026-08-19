"""Contracts for the provider-free, persisted showcase replay."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from apps.api.demo.replay import (
    FIXTURE_CLIENT_ID,
    DemoReplayService,
    FixtureReplayRepository,
)
from apps.api.observability.live import EventHub


class RecordingFixtureRepository(FixtureReplayRepository):
    """A repository seam that records the persisted dashboard-shaped fixture."""

    def __init__(self) -> None:
        self.reset_count = 0
        self.persisted: list[object] = []

    async def reset_fixture(self, *, client_id: str) -> None:
        assert client_id == FIXTURE_CLIENT_ID
        self.reset_count += 1
        self.persisted.clear()

    async def persist(self, replay: object) -> None:
        self.persisted.append(replay)

    async def fixture_data_ready(self, *, client_id: str) -> bool:
        return bool(self.persisted) and client_id == FIXTURE_CLIENT_ID


@pytest.fixture
def repository() -> RecordingFixtureRepository:
    return RecordingFixtureRepository()


@pytest.mark.asyncio
async def test_replay_persists_redacted_hvac_story_with_degraded_booking_and_escalation(
    repository: RecordingFixtureRepository,
) -> None:
    """Removing a persisted replay turn, tool, event, analysis, or cost must fail this test."""
    service = DemoReplayService(repository=repository, hub=EventHub())

    result = await service.reset_and_replay()

    assert result["fixture"] is True
    assert result["simulated"] is True
    assert result["client_id"] == "northside-hvac"
    assert result["ready"] is True
    assert result["calls"][0]["from_e164"] == "+1555555****"
    assert result["calls"][0]["outcome"] == "booked"
    assert result["calls"][0]["cost_cents"] > 0
    assert any(turn["role"] == "caller" for turn in result["calls"][0]["turns"])
    assert {tool["name"] for tool in result["calls"][0]["tool_invocations"]} == {
        "check_service_area",
        "book_appointment",
    }
    assert any(tool["status"] == "degraded" for tool in result["calls"][0]["tool_invocations"])
    assert any(event["kind"] == "escalation" for event in result["calls"][0]["events"])
    assert result["calls"][0]["analysis"]["summary"]


@pytest.mark.asyncio
async def test_replay_is_idempotent_and_marks_every_published_event_as_simulated(
    repository: RecordingFixtureRepository,
) -> None:
    """Dropping the fixture-only reset or labels must fail this deterministic replay contract."""
    hub = EventHub()
    service = DemoReplayService(repository=repository, hub=hub)

    async with hub.subscribe() as events:
        first = await service.reset_and_replay()
        published = [await events.get() for _ in range(first["event_count"])]
    second = await service.reset_and_replay()

    assert repository.reset_count == 2
    assert len(repository.persisted) == 1
    assert first["calls"] == second["calls"]
    assert all(event.to_json()["fixture"] is True for event in published)
    assert all(event.to_json()["simulated"] is True for event in published)


@pytest.mark.asyncio
async def test_replay_never_needs_a_provider_client_and_reports_fixture_readiness(
    repository: RecordingFixtureRepository,
) -> None:
    """Replacing the local repository seam with a provider adapter must fail this offline contract."""
    service = DemoReplayService(repository=repository, hub=EventHub())

    assert await service.fixture_data_ready() is False
    await service.reset_and_replay()

    assert await service.fixture_data_ready() is True
    assert service.provider_clients_constructed == 0
