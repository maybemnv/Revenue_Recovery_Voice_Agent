"""Server-sent events for the live call view.

SSE rather than a WebSocket because the feed is strictly one-way and SSE
reconnects on its own. A heartbeat comment goes out every 15 seconds so proxies
that reap idle connections at 30 or 60 do not silently kill a demo that is
sitting on the live view waiting for a call.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from apps.api.observability.live import get_hub
from apps.api.routers.auth import require_viewer

router = APIRouter(prefix="/api", tags=["dashboard"], dependencies=[Depends(require_viewer)])

HEARTBEAT_SECONDS = 15.0


async def _event_stream(request: Request, call_id: str | None) -> AsyncIterator[str]:
    hub = get_hub()
    async with hub.subscribe() as queue:
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if call_id and event.call_id != call_id:
                continue
            yield f"event: {event.kind}\ndata: {json.dumps(event.to_json())}\n\n"


@router.get("/stream")
async def stream(request: Request, call_id: str | None = None) -> StreamingResponse:
    return StreamingResponse(
        _event_stream(request, call_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx buffers SSE into uselessness without this.
            "X-Accel-Buffering": "no",
        },
    )
