"""Contracts for the provider-free, persisted showcase replay."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.demo.replay import (
    DemoReplayService,
    FixtureReplayRepository,
    SqlAlchemyFixtureReplayRepository,
)
from apps.api.media import realtime_client
from apps.api.observability.live import EventHub
from apps.api.routers import demo as demo_router
from apps.api.routers import health


class RecordingFixtureRepository(FixtureReplayRepository):
    """A repository seam that records the persisted dashboard-shaped fixture."""

    def __init__(self) -> None:
        self.reset_count = 0
        self.reset_client_id: str | None = None
        self.persisted: list[object] = []

    async def reset_fixture(self, *, client_id: str) -> None:
        self.reset_count += 1
        self.reset_client_id = client_id
        self.persisted.clear()

    async def persist(self, replay: object) -> None:
        self.persisted.append(replay)

    async def fixture_data_ready(self, *, client_id: str) -> bool:
        return bool(self.persisted) and client_id == self.reset_client_id


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
        "confirm_appointment",
        "update_crm",
    }
    assert any(tool["status"] == "degraded" for tool in result["calls"][0]["tool_invocations"])
    assert any(event["kind"] == "escalation" for event in result["calls"][0]["events"])
    assert any(
        event["payload"].get("tool") == "confirm_appointment"
        for event in result["calls"][0]["events"]
    )
    assert any(
        event["payload"].get("tool") == "update_crm"
        for event in result["calls"][0]["events"]
    )
    assert result["calls"][0]["analysis"]["summary"]


@pytest.mark.asyncio
async def test_replay_is_idempotent_and_marks_every_published_event_as_simulated(
    repository: RecordingFixtureRepository,
) -> None:
    """Dropping the fixture-only reset or labels must fail this deterministic replay contract."""
    hub = EventHub()
    service = DemoReplayService(
        repository=repository,
        hub=hub,
        clock=lambda: datetime(2040, 2, 3, 12, 0, tzinfo=UTC),
    )

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
    """A provider adapter in this local seam would violate the offline contract."""
    service = DemoReplayService(repository=repository, hub=EventHub())

    assert await service.fixture_data_ready() is False
    await service.reset_and_replay()

    assert await service.fixture_data_ready() is True
    assert service.provider_clients_constructed == 0


@pytest.mark.asyncio
async def test_replay_completes_when_provider_constructors_and_network_clients_are_forbidden(
    repository: RecordingFixtureRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Routing replay through a provider client would make this successful fixture run explode."""
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("fixture replay must not construct providers or network clients")

    monkeypatch.setattr(realtime_client, "RealtimeClient", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)

    result = await DemoReplayService(repository=repository, hub=EventHub()).reset_and_replay()

    assert result["ready"] is True
    assert result["calls"][0]["cost_cents"] == 47


class RecordingSqlSession:
    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, statement: object) -> None:
        self.statements.append(statement)


@pytest.mark.asyncio
async def test_fixture_reset_deletes_only_the_named_client_and_fixture_sid() -> None:
    """Removing either predicate could delete a live customer's matching call data."""
    session = RecordingSqlSession()
    repository = SqlAlchemyFixtureReplayRepository(session)  # type: ignore[arg-type]

    await repository.reset_fixture(client_id="fixture-east")

    compiled = str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))  # type: ignore[union-attr]
    assert "calls.client_id = 'fixture-east'" in compiled
    assert "calls.twilio_call_sid = 'DEMO_FIXTURE_NORTHSIDE_HVAC_001'" in compiled


@pytest.mark.asyncio
async def test_replay_uses_an_injected_current_clock_and_configured_fixture_client(
    repository: RecordingFixtureRepository,
) -> None:
    """A stale fixed timestamp or hard-coded client makes the seven-day API metrics empty."""
    now = datetime(2040, 2, 3, 12, 0, tzinfo=UTC)
    service = DemoReplayService(
        repository=repository,
        hub=EventHub(),
        client_id="fixture-east",
        clock=lambda: now,
    )

    result = await service.reset_and_replay()

    assert result["client_id"] == "fixture-east"
    assert result["calls"][0]["started_at"] == now.isoformat()
    assert result["calls"][0]["ended_at"].startswith("2040-02-03T12:03:12")


def test_health_is_process_liveness_while_readiness_owns_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database outage must not turn the process-liveness route into readiness."""
    async def dependency_down() -> tuple[bool, str | None]:
        return False, "ConnectionError"

    monkeypatch.setattr(health, "_check_postgres", dependency_down)
    monkeypatch.setattr(health, "_check_redis", dependency_down)
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: SimpleNamespace(environment="fixture", fixture_mode=False),
    )
    app = FastAPI()
    app.include_router(health.router)

    with TestClient(app) as client:
        live = client.get("/health")
        ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert "checks" not in live.json()
    assert ready.status_code == 503
    assert ready.json()["checks"]["postgres"]["ok"] is False


def test_readiness_reports_fixture_labels_and_the_configured_fixture_client_without_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing the fixture readiness seam must not require a PostgreSQL server in route tests."""
    observed_client_ids: list[str] = []

    async def dependency_up() -> tuple[bool, str | None]:
        return True, None

    async def fixture_not_loaded(*, client_id: str) -> bool:
        observed_client_ids.append(client_id)
        return False

    monkeypatch.setattr(health, "_check_postgres", dependency_up)
    monkeypatch.setattr(health, "_check_redis", dependency_up)
    monkeypatch.setattr(health, "_fixture_data_ready", fixture_not_loaded, raising=False)
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: SimpleNamespace(
            environment="fixture", fixture_mode=True, fixture_client_id="fixture-east"
        ),
    )
    app = FastAPI()
    app.include_router(health.router)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["fixture"] is True
    assert response.json()["simulated"] is True
    assert response.json()["checks"]["fixture_data"] == {"ok": False}
    assert observed_client_ids == ["fixture-east"]


def test_reset_route_uses_the_configured_fixture_client_and_returns_simulation_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping the route's client setting would replay the wrong fixture through POST."""
    seen_client_ids: list[str] = []

    class FakeService:
        async def reset_and_replay(self) -> dict[str, object]:
            return {"fixture": True, "simulated": True, "client_id": "fixture-east", "ready": True}

    def fake_service(_session: object, *, client_id: str) -> FakeService:
        seen_client_ids.append(client_id)
        return FakeService()

    async def fake_session() -> object:
        yield object()

    monkeypatch.setattr(demo_router, "get_demo_service", fake_service)
    monkeypatch.setattr(
        demo_router,
        "get_settings",
        lambda: SimpleNamespace(fixture_mode=True, fixture_client_id="fixture-east"),
    )
    app = FastAPI()
    app.include_router(demo_router.router)
    app.dependency_overrides[demo_router.get_session] = fake_session

    with TestClient(app) as client:
        response = client.post("/api/demo/reset-and-replay")

    assert response.status_code == 200
    assert response.json()["fixture"] is True
    assert response.json()["simulated"] is True
    assert seen_client_ids == ["fixture-east"]
