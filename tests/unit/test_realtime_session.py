"""The GA session handshake, over a real socket.

`build_session_update` is already unit-tested as a pure shape. What was missing
is proof that the shape survives an actual `websockets` round trip: that the
client connects without the beta header, sends the nested GA audio config, and
reads `session.updated` back off the wire.

The server here is a stand-in for OpenAI that asserts GA-ness on arrival. It
rejects the two mistakes that every beta-era tutorial still makes — the
`OpenAI-Beta: realtime=v1` header and flat `input_audio_format` — so a
regression toward the beta shapes fails here rather than on a live call.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
import websockets
from websockets.asyncio.server import Server, ServerConnection

from apps.api.config.schema import ClientConfig
from apps.api.media.realtime_client import (
    EV_SESSION_UPDATED,
    PCMU,
    RealtimeClient,
    build_session_update,
)


class FakeRealtimeServer:
    """A GA-shaped Realtime endpoint. Records what it was sent, replies in kind."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {}
        self.path: str = ""
        self._server: Server | None = None
        self.port: int = 0

    async def __aenter__(self) -> FakeRealtimeServer:
        self._server = await websockets.serve(self._handle, "127.0.0.1", 0)
        self.port = next(iter(self._server.sockets)).getsockname()[1]
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    async def _handle(self, ws: ServerConnection) -> None:
        self.headers = dict(ws.request.headers) if ws.request else {}
        self.path = ws.request.path if ws.request else ""
        async for raw in ws:
            payload = json.loads(raw)
            self.received.append(payload)
            if payload.get("type") == "session.update":
                # Echo the accepted session back, exactly as GA does.
                await ws.send(
                    json.dumps(
                        {
                            "type": EV_SESSION_UPDATED,
                            "session": payload["session"],
                        }
                    )
                )


@pytest.fixture
async def server() -> AsyncIterator[FakeRealtimeServer]:
    async with FakeRealtimeServer() as srv:
        yield srv


async def test_session_update_round_trips_with_pcmu_both_directions(
    server: FakeRealtimeServer, config: ClientConfig
) -> None:
    """The [D0] gate: send GA `session.update`, read `session.updated` back."""
    client = RealtimeClient(model=config.realtime.model, api_key="test-key", url=server.url)

    async with client:
        await client.send_json(build_session_update(config, tools=[]))
        event = await asyncio.wait_for(anext(client.events()), timeout=5.0)

    assert event["type"] == EV_SESSION_UPDATED

    audio = event["session"]["audio"]
    # The claim this whole design rests on: μ-law in, μ-law out, no transcode.
    assert audio["input"]["format"] == PCMU
    assert audio["output"]["format"] == PCMU
    assert audio["input"]["format"]["type"] == "audio/pcmu"
    assert audio["output"]["format"]["type"] == "audio/pcmu"


async def test_connect_sends_no_beta_header(
    server: FakeRealtimeServer, config: ClientConfig
) -> None:
    """GA rejects the beta payload shapes, and this header opts you into them."""
    client = RealtimeClient(model=config.realtime.model, api_key="test-key", url=server.url)

    async with client:
        await client.send_json(build_session_update(config, tools=[]))
        await asyncio.wait_for(anext(client.events()), timeout=5.0)

    lowered = {k.lower(): v for k, v in server.headers.items()}
    assert "openai-beta" not in lowered
    assert lowered["authorization"] == "Bearer test-key"


async def test_session_uses_nested_audio_not_flat_beta_keys(
    server: FakeRealtimeServer, config: ClientConfig
) -> None:
    """`session.audio.input.format`, never `input_audio_format`."""
    client = RealtimeClient(model=config.realtime.model, api_key="test-key", url=server.url)

    async with client:
        await client.send_json(build_session_update(config, tools=[]))
        await asyncio.wait_for(anext(client.events()), timeout=5.0)

    sent = server.received[0]
    assert sent["type"] == "session.update"
    session = sent["session"]

    assert session["type"] == "realtime"
    assert "input_audio_format" not in session
    assert "output_audio_format" not in session
    assert session["audio"]["input"]["format"] == PCMU
    assert session["audio"]["output"]["format"] == PCMU
    # semantic_vad with barge-in enabled is what makes beat 3 possible at all.
    turn_detection = session["audio"]["input"]["turn_detection"]
    assert turn_detection["type"] == "semantic_vad"
    assert turn_detection["interrupt_response"] is True


async def test_model_is_pinned_on_the_query_string(
    server: FakeRealtimeServer, config: ClientConfig
) -> None:
    client = RealtimeClient(model=config.realtime.model, api_key="test-key", url=server.url)

    async with client:
        await client.send_json(build_session_update(config, tools=[]))
        await asyncio.wait_for(anext(client.events()), timeout=5.0)

    assert f"model={config.realtime.model}" in server.path
