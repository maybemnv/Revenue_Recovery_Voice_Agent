from __future__ import annotations

import asyncio

from apps.api.observability.live import get_hub
from apps.api.routers.stream import _event_stream


class ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


async def test_live_stream_uses_default_message_channel() -> None:
    hub = get_hub()
    stream = _event_stream(ConnectedRequest(), call_id=None)  # type: ignore[arg-type]
    assert await anext(stream) == ": connected\n\n"

    await hub.publish("call-1", "turn", {"role": "agent", "text": "hello"})
    chunk = await asyncio.wait_for(anext(stream), timeout=1.0)

    assert chunk.startswith("data: ")
    assert '"kind": "turn"' in chunk
    assert "event:" not in chunk
    await stream.aclose()
