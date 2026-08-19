"""Persist and publish the one deterministic showcase call without providers."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db import repository
from apps.api.db.models import Call, CallAnalysis
from apps.api.observability.live import EventHub, get_hub
from apps.api.security.redaction import mask_e164

FIXTURE_CLIENT_ID = "northside-hvac"
FIXTURE_CALL_ID = uuid.UUID("00000000-0000-0000-0000-000000000101")
FIXTURE_CALL_SID = "DEMO_FIXTURE_NORTHSIDE_HVAC_001"
FIXTURE_CLOCK = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FixtureReplay:
    call_id: uuid.UUID
    client_id: str
    twilio_call_sid: str
    from_e164: str
    started_at: datetime
    ended_at: datetime
    outcome: str
    cost_cents: int
    turns: tuple[dict[str, Any], ...]
    tool_invocations: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]
    analysis: dict[str, Any]

    def to_public(self) -> dict[str, Any]:
        return {
            "id": str(self.call_id),
            "client_id": self.client_id,
            "from_e164": mask_e164(self.from_e164),
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "outcome": self.outcome,
            "cost_cents": self.cost_cents,
            "fixture": True,
            "simulated": True,
            "turns": list(self.turns),
            "tool_invocations": list(self.tool_invocations),
            "events": list(self.events),
            "analysis": self.analysis,
        }


def fixture_replay() -> FixtureReplay:
    """The complete demo story, concentrated here so it cannot drift across UI surfaces."""
    return FixtureReplay(
        call_id=FIXTURE_CALL_ID,
        client_id=FIXTURE_CLIENT_ID,
        twilio_call_sid=FIXTURE_CALL_SID,
        from_e164="+15555550101",
        started_at=FIXTURE_CLOCK,
        ended_at=FIXTURE_CLOCK + timedelta(minutes=3, seconds=12),
        outcome="booked",
        cost_cents=47,
        turns=(
            {
                "role": "agent",
                "text": "Northside HVAC. How can I help?",
                "at_ms": 0,
                "latency_ms": 420,
            },
            {
                "role": "caller",
                "text": "There is a gas smell near our furnace.",
                "at_ms": 2200,
                "latency_ms": None,
            },
            {
                "role": "agent",
                "text": (
                    "Please leave the area. I am escalating this and booking "
                    "the earliest safe visit."
                ),
                "at_ms": 3900,
                "latency_ms": 510,
            },
        ),
        tool_invocations=(
            {
                "name": "check_service_area",
                "status": "ok",
                "latency_ms": 12,
                "attempt": 1,
                "arguments": {"postcode": "60614"},
            },
            {
                "name": "book_appointment",
                "status": "degraded",
                "latency_ms": 180,
                "attempt": 1,
                "arguments": {"requested_window": "today"},
            },
            {
                "name": "book_appointment",
                "status": "ok",
                "latency_ms": 95,
                "attempt": 2,
                "arguments": {"slot": "2026-08-19T16:00:00-05:00"},
            },
        ),
        events=(
            {
                "at_ms": 2500,
                "kind": "tool_call",
                "payload": {"tool": "check_service_area", "fixture": True, "simulated": True},
            },
            {
                "at_ms": 4100,
                "kind": "escalation",
                "payload": {"reason": "safety_keyword", "fixture": True, "simulated": True},
            },
            {
                "at_ms": 5200,
                "kind": "tool_call",
                "payload": {
                    "tool": "book_appointment",
                    "status": "degraded",
                    "fixture": True,
                    "simulated": True,
                },
            },
            {
                "at_ms": 7000,
                "kind": "tool_call",
                "payload": {
                    "tool": "book_appointment",
                    "status": "booked",
                    "fixture": True,
                    "simulated": True,
                },
            },
        ),
        analysis={
                "summary": (
                    "Simulated urgent Northside HVAC call: service area "
                    "validated, safety escalation "
                    "recorded, and fixture appointment booked after a degraded scheduling attempt."
                ),
            "intent": "urgent_hvac_repair",
            "sentiment": "concerned",
            "qa_score": 94,
            "action_items": ["Safety escalation logged", "Fixture booking confirmed"],
            "model": "fixture-replay",
        },
    )


class FixtureReplayRepository(ABC):
    @abstractmethod
    async def reset_fixture(self, *, client_id: str) -> None: ...

    @abstractmethod
    async def persist(self, replay: FixtureReplay) -> None: ...

    @abstractmethod
    async def fixture_data_ready(self, *, client_id: str) -> bool: ...


class SqlAlchemyFixtureReplayRepository(FixtureReplayRepository):
    """The narrow persistence adapter; deletion is restricted to known fixture SIDs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reset_fixture(self, *, client_id: str) -> None:
        await self._session.execute(
            delete(Call).where(
                Call.client_id == client_id,
                Call.twilio_call_sid == FIXTURE_CALL_SID,
            )
        )

    async def persist(self, replay: FixtureReplay) -> None:
        await repository.create_call(
            self._session,
            call_id=replay.call_id,
            client_id=replay.client_id,
            twilio_call_sid=replay.twilio_call_sid,
            from_e164=replay.from_e164,
            started_at=replay.started_at,
        )
        for turn in replay.turns:
            await repository.insert_turn(
                self._session,
                call_id=replay.call_id,
                role=turn["role"],
                text=turn["text"],
                started_at_ms=turn["at_ms"],
                latency_ms=turn["latency_ms"],
            )
        for invocation in replay.tool_invocations:
            await repository.insert_tool_invocation(
                self._session,
                call_id=replay.call_id,
                name=invocation["name"],
                arguments=invocation["arguments"],
                result_status=invocation["status"],
                latency_ms=invocation["latency_ms"],
                attempt=invocation["attempt"],
            )
        for event in replay.events:
            await repository.insert_call_event(
                self._session,
                call_id=replay.call_id,
                at_ms=event["at_ms"],
                kind=event["kind"],
                payload=event["payload"],
            )
        self._session.add(CallAnalysis(call_id=replay.call_id, **replay.analysis))
        await repository.finish_call(
            self._session,
            replay.call_id,
            outcome=replay.outcome,
            cost_cents=replay.cost_cents,
            ended_at=replay.ended_at,
        )

    async def fixture_data_ready(self, *, client_id: str) -> bool:
        return bool(
            await self._session.scalar(
                select(Call.id).where(
                    Call.client_id == client_id,
                    Call.twilio_call_sid == FIXTURE_CALL_SID,
                )
            )
        )


class DemoReplayService:
    """Provider-free service boundary used by the route and focused tests."""

    provider_clients_constructed = 0

    def __init__(self, *, repository: FixtureReplayRepository, hub: EventHub) -> None:
        self._repository = repository
        self._hub = hub

    async def reset_and_replay(self) -> dict[str, Any]:
        replay = fixture_replay()
        await self._repository.reset_fixture(client_id=replay.client_id)
        await self._repository.persist(replay)
        for event in replay.events:
            await self._hub.publish(
                str(replay.call_id),
                event["kind"],
                {**event["payload"], "fixture": True, "simulated": True},
                at=(replay.started_at + timedelta(milliseconds=event["at_ms"])).isoformat(),
            )
        return {
            "fixture": True,
            "simulated": True,
            "client_id": replay.client_id,
            "ready": await self.fixture_data_ready(),
            "event_count": len(replay.events),
            "calls": [replay.to_public()],
        }

    async def fixture_data_ready(self) -> bool:
        return await self._repository.fixture_data_ready(client_id=FIXTURE_CLIENT_ID)


def get_demo_service(session: AsyncSession) -> DemoReplayService:
    return DemoReplayService(repository=SqlAlchemyFixtureReplayRepository(session), hub=get_hub())
