"""In-process pub/sub for the live call feed.

The dashboard's SSE endpoint subscribes here; the media bridge publishes. One
process, one hub — which is a real constraint: with more than one API replica a
viewer only sees the calls handled by the replica they are connected to. That is
a Redis pub/sub swap behind the same two functions, and deliberately not built
yet, because the demo runs one API process and a distributed event bus that is
never exercised is a liability rather than an asset.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from apps.api.observability.logging import get_logger

log = get_logger(__name__)

# Bounded so a subscriber that stops reading cannot grow the heap without limit.
QUEUE_MAXSIZE = 256


@dataclass(frozen=True, slots=True)
class LiveEvent:
    call_id: str
    kind: str
    payload: dict[str, Any]
    at: str

    def to_json(self) -> dict[str, Any]:
        return {"call_id": self.call_id, "kind": self.kind, "at": self.at, **self.payload}


@dataclass(slots=True)
class EventHub:
    _subscribers: set[asyncio.Queue[LiveEvent]] = field(default_factory=set)

    async def publish(self, call_id: str, kind: str, payload: dict[str, Any]) -> None:
        if not self._subscribers:
            return
        event = LiveEvent(
            call_id=call_id,
            kind=kind,
            payload=payload,
            at=datetime.now(UTC).isoformat(),
        )
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A slow dashboard tab must never apply backpressure to a live
                # phone call. Drop the event for that subscriber only.
                log.warning("live_event_dropped", kind=kind)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[LiveEvent]]:
        queue: asyncio.Queue[LiveEvent] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


_hub = EventHub()


def get_hub() -> EventHub:
    return _hub
