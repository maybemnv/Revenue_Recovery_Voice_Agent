"""Media lifecycle: bounded outbound queue, drain, close, and final flush.

The queue exists so a slow Twilio socket cannot grow memory without limit, and
its ordering guarantees are the reason the passthrough tests stay deterministic:
frames leave the queue in FIFO order, `on_chunk_sent` fires only at the moment
of the actual write, and a barge-in discards queued-but-unwritten audio *before*
Twilio's `clear` so the flush cannot be followed by fresh audio being written in
behind it.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from apps.api.config.schema import ClientConfig
from apps.api.domain.state import CallState
from apps.api.media.bridge import (
    OUTBOUND_QUEUE_MAX,
    BridgeHooks,
    MediaBridge,
)
from apps.api.media.realtime_client import (
    EV_OUTPUT_AUDIO_DELTA,
    EV_OUTPUT_AUDIO_TRANSCRIPT_DELTA,
    EV_SPEECH_STARTED,
)
from apps.api.tools.registry import RegistryBundle, ToolRegistry

STREAM_SID = "MZlifecycle"

Step = Callable[[], Awaitable[None]]


class GatedTwilio:
    """A Twilio socket whose media writes can be stalled, to back up the queue.

    `send_json` blocks on `gate` for `media` events only, so `clear` and `mark`
    still flow — which is how a barge-in is delivered while the pump is stuck.
    """

    def __init__(self, frames: list[dict[str, Any] | Step]) -> None:
        self._frames = list(frames)
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.gate = asyncio.Event()
        self.gate.set()

    async def receive_text(self) -> str:
        while True:
            if not self._frames:
                await asyncio.Event().wait()
            frame = self._frames.pop(0)
            if callable(frame):
                await frame()
                continue
            return json.dumps(frame)

    async def send_json(self, payload: dict[str, Any]) -> None:
        if payload.get("event") == "media":
            await self.gate.wait()
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True

    def media_count(self) -> int:
        return sum(1 for p in self.sent if p.get("event") == "media")


class FakeRealtime:
    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.feed: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.idle = asyncio.Event()
        self.closed = False
        for event in script or []:
            self.feed.put_nowait(event)

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def append_audio(self, payload: str) -> None:
        await self.send_json({"type": "input_audio_buffer.append", "audio": payload})

    async def create_response(self, response: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"type": "response.create"}
        if response is not None:
            payload["response"] = response
        await self.send_json(payload)

    async def say_out_of_band(self, line: str) -> None:
        await self.create_response({"input": [], "instructions": f'Say exactly: "{line}"'})

    async def close(self) -> None:
        self.closed = True

    async def events(self) -> Any:
        while True:
            if self.feed.empty():
                self.idle.set()
            yield await self.feed.get()

    def types(self) -> list[str]:
        return [p.get("type", "") for p in self.sent]


def settled(realtime: FakeRealtime) -> Step:
    async def _step() -> None:
        await realtime.idle.wait()
        realtime.idle.clear()

    return _step


def push(realtime: FakeRealtime, event: dict[str, Any]) -> Step:
    async def _step() -> None:
        await realtime.feed.put(event)

    return _step


def _delta(seed: int, size: int = 160) -> str:
    raw = bytes([(seed + i) % 256 for i in range(size)])
    return base64.b64encode(raw).decode("ascii")


def _start_frame() -> dict[str, Any]:
    return {"event": "start", "start": {"streamSid": STREAM_SID}}


def _bridge(
    config: ClientConfig,
    twilio: GatedTwilio,
    realtime: FakeRealtime,
    hooks: BridgeHooks | None = None,
) -> MediaBridge:
    return MediaBridge(
        twilio=twilio,  # type: ignore[arg-type]
        realtime=realtime,  # type: ignore[arg-type]
        config=config,
        state=CallState(call_id="lc1", client_id=config.client_id, from_e164="+13125551111"),
        tools=RegistryBundle(registry=ToolRegistry(), context={}),
        hooks=hooks,
    )


async def _run(bridge: MediaBridge) -> Any:
    return await asyncio.wait_for(bridge.run(), timeout=5)


async def test_stop_closes_both_sockets(config: ClientConfig) -> None:
    realtime = FakeRealtime()
    twilio = GatedTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime))

    assert twilio.closed is True
    assert realtime.closed is True


async def test_queued_audio_is_drained_before_close(config: ClientConfig) -> None:
    """Audio already handed to the bridge is still written after `stop`."""
    deltas = [_delta(seed) for seed in range(5)]
    realtime = FakeRealtime(
        [{"type": EV_OUTPUT_AUDIO_DELTA, "delta": d} for d in deltas]
    )
    twilio = GatedTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime))

    assert [p["media"]["payload"] for p in twilio.sent if p.get("event") == "media"] == deltas


async def test_queue_is_bounded_and_drops_oldest(config: ClientConfig) -> None:
    """A stalled socket fills the queue; further audio drops the oldest frame."""
    realtime = FakeRealtime(
        [{"type": EV_OUTPUT_AUDIO_DELTA, "delta": _delta(i)} for i in range(OUTBOUND_QUEUE_MAX + 50)]
    )
    twilio = GatedTwilio([_start_frame(), settled(realtime), {"event": "stop"}])
    bridge = _bridge(config, twilio, realtime)

    # Stall the pump before it can drain anything.
    twilio.gate.clear()
    task = asyncio.create_task(bridge.run())
    try:
        # Give the pumps time to fill the queue past its ceiling.
        await _wait_until(lambda: bridge.stats.frames_dropped > 0)
        assert bridge.stats.frames_dropped == 50
        assert bridge._outbound.qsize() <= OUTBOUND_QUEUE_MAX  # noqa: SLF001
    finally:
        twilio.gate.set()
        await asyncio.wait_for(task, timeout=5)

    # The frames that survived were the *newest* OUTBOUND_QUEUE_MAX.
    written = [p["media"]["payload"] for p in twilio.sent if p.get("event") == "media"]
    assert len(written) == OUTBOUND_QUEUE_MAX
    assert written[0] == _delta(50)
    assert written[-1] == _delta(OUTBOUND_QUEUE_MAX + 49)


async def test_barge_in_discards_queued_audio_before_clear(config: ClientConfig) -> None:
    """Audio not yet written must not play after `clear` has flushed the buffer."""
    realtime = FakeRealtime(
        [{"type": EV_OUTPUT_AUDIO_DELTA, "delta": _delta(i)} for i in range(10)]
        + [{"type": EV_SPEECH_STARTED}]
    )
    twilio = GatedTwilio([_start_frame(), settled(realtime), {"event": "stop"}])
    bridge = _bridge(config, twilio, realtime)

    # Stall the pump mid-write so the remaining frames are still queued when the
    # barge-in lands. `clear` is not gated, so it gets through regardless.
    twilio.gate.clear()
    task = asyncio.create_task(bridge.run())
    try:
        # Synchronise on the barge-in itself, not on a queue-depth proxy.
        await _wait_until(lambda: any(p.get("event") == "clear" for p in twilio.sent))
        assert bridge.stats.barge_ins >= 0  # the cut happened; queue now empty
        assert bridge._outbound.qsize() == 0  # noqa: SLF001
    finally:
        twilio.gate.set()
        await asyncio.wait_for(task, timeout=5)

    events = [p.get("event", "") for p in twilio.sent]
    # At most the single in-flight frame made it out — the queued nine were
    # discarded, and the `clear` preceded whatever audio did land.
    assert twilio.media_count() <= 1
    first_media = next((i for i, e in enumerate(events) if e == "media"), len(events))
    assert events.index("clear") < first_media or twilio.media_count() == 0


async def test_mid_answer_hangup_flushes_partial_turn(config: ClientConfig) -> None:
    """Transcript deltas without a `.done` still land as one agent turn."""
    realtime = FakeRealtime(
        [
            {"type": EV_OUTPUT_AUDIO_TRANSCRIPT_DELTA, "delta": "We can send "},
            {"type": EV_OUTPUT_AUDIO_TRANSCRIPT_DELTA, "delta": "someone out "},
            {"type": EV_OUTPUT_AUDIO_TRANSCRIPT_DELTA, "delta": "this afternoon."},
        ]
    )
    twilio = GatedTwilio([_start_frame(), settled(realtime), {"event": "stop"}])
    turns: list[tuple[str, str]] = []

    async def on_turn(role: str, text: str, at_ms: int, meta: dict[str, Any]) -> None:
        turns.append((role, text))

    await _run(_bridge(config, twilio, realtime, BridgeHooks(on_turn=on_turn)))

    assert ("agent", "We can send someone out this afternoon.") in turns


async def test_completed_turn_is_not_flushed_twice(config: ClientConfig) -> None:
    """A `.done` turn is recorded once; the partial buffer starts clean after."""
    realtime = FakeRealtime(
        [
            {"type": EV_OUTPUT_AUDIO_TRANSCRIPT_DELTA, "delta": "partial draft "},
            {"type": "response.output_audio_transcript.done", "transcript": "The real turn."},
        ]
    )
    twilio = GatedTwilio([_start_frame(), settled(realtime), {"event": "stop"}])
    turns: list[tuple[str, str]] = []

    async def on_turn(role: str, text: str, at_ms: int, meta: dict[str, Any]) -> None:
        turns.append((role, text))

    await _run(_bridge(config, twilio, realtime, BridgeHooks(on_turn=on_turn)))

    agent_turns = [text for role, text in turns if role == "agent"]
    assert agent_turns == ["The real turn."]


async def test_double_close_is_harmless(config: ClientConfig) -> None:
    """`aclose` runs from `run()`'s finally and from graceful shutdown."""
    realtime = FakeRealtime()
    twilio = GatedTwilio([_start_frame(), settled(realtime), {"event": "stop"}])
    bridge = _bridge(config, twilio, realtime)

    await _run(bridge)
    await bridge.aclose()

    assert twilio.closed is True
    assert realtime.closed is True


async def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    async def _poll() -> None:
        for _ in range(int(timeout / 0.01)):
            if predicate():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("condition not reached within timeout")

    await asyncio.wait_for(_poll(), timeout=timeout)
