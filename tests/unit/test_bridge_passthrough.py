"""The relay's central claim: audio crosses the bridge byte-identical.

If a single transcode, resample, or re-encode ever creeps into the hot path,
these tests fail. They also pin the mark discipline — one mark per outbound
delta, sent after the audio, counted in decoded bytes — because the truncation
maths downstream is only as honest as that accounting.

Interleaving is made deterministic rather than timing-dependent: the fake
OpenAI feed announces when it has gone idle, and the fake Twilio socket can wait
on that announcement before delivering its next frame. No sleeps, no flake.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import WebSocketDisconnect

from apps.api.config.schema import ClientConfig
from apps.api.domain.state import CallState
from apps.api.media.bridge import (
    OUTBOUND_QUEUE_MAX,
    BridgeHooks,
    MediaBridge,
    _b64_decoded_size,
    decode_size_exact,
)
from apps.api.media.realtime_client import (
    EV_OUTPUT_AUDIO_DELTA,
    EV_OUTPUT_ITEM_ADDED,
    EV_SPEECH_STARTED,
)
from apps.api.tools.registry import RegistryBundle, ToolRegistry

STREAM_SID = "MZdeadbeef"

Step = Callable[[], Awaitable[None]]


class FakeRealtime:
    """Stands in for `RealtimeClient`. Records sends, replays a scripted feed.

    `idle` is set whenever the feed has nothing left *and* everything already
    delivered has been fully processed by the bridge, which is the barrier the
    tests synchronise on.
    """

    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.appended: list[str] = []
        self.feed: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.idle = asyncio.Event()
        self.closed = False
        for event in script or []:
            self.feed.put_nowait(event)

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def append_audio(self, base64_payload: str) -> None:
        # Records the raw string, not a decode: the test asserts on the exact
        # characters that came off the Twilio socket.
        self.appended.append(base64_payload)
        await self.send_json({"type": "input_audio_buffer.append", "audio": base64_payload})

    async def create_response(self, response: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"type": "response.create"}
        if response is not None:
            payload["response"] = response
        await self.send_json(payload)

    async def say_out_of_band(self, line: str) -> None:
        await self.create_response({"input": [], "instructions": f'Say exactly: "{line}"'})

    async def classify_out_of_band(self, instructions: str, topic: str) -> None:
        await self.create_response(
            {
                "conversation": "none",
                "input": [],
                "output_modalities": ["text"],
                "instructions": instructions,
                "metadata": {"topic": topic},
            }
        )

    async def close(self) -> None:
        self.closed = True

    async def events(self) -> Any:
        while True:
            if self.feed.empty():
                self.idle.set()
            yield await self.feed.get()

    def types(self) -> list[str]:
        return [p.get("type", "") for p in self.sent]

    def payload(self, kind: str) -> dict[str, Any]:
        for sent in self.sent:
            if sent.get("type") == kind:
                return sent
        raise AssertionError(f"{kind} was never sent; got {self.types()}")


class FakeTwilio:
    """Replays inbound frames, records outbound ones.

    A frame may be a coroutine function instead of a dict, in which case it is
    awaited for its side effect and the next frame is taken. That is how a test
    says "wait until the model side has caught up" without a sleep.
    """

    def __init__(self, frames: list[dict[str, Any] | Step]) -> None:
        self._frames: list[dict[str, Any] | Step] = list(frames)
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._shut = asyncio.Event()

    async def receive_text(self) -> str:
        while True:
            if not self._frames:
                # The scripted "stop" frame is what ends a call, not exhaustion.
                # A close from the other side still has to unblock this read, the
                # way a real Starlette socket does.
                await self._shut.wait()
                raise WebSocketDisconnect(code=1000)
            frame = self._frames.pop(0)
            if callable(frame):
                await frame()
                continue
            return json.dumps(frame)

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self._shut.set()

    def media_payloads(self) -> list[str]:
        return [p["media"]["payload"] for p in self.sent if p.get("event") == "media"]

    def marks(self) -> list[str]:
        return [p["mark"]["name"] for p in self.sent if p.get("event") == "mark"]

    def events(self) -> list[str]:
        return [p.get("event", "") for p in self.sent]


def settled(realtime: FakeRealtime) -> Step:
    """Barrier: continue once the bridge has drained everything OpenAI sent."""

    async def _step() -> None:
        await realtime.idle.wait()
        realtime.idle.clear()

    return _step


def push(realtime: FakeRealtime, event: dict[str, Any]) -> Step:
    async def _step() -> None:
        await realtime.feed.put(event)

    return _step


def _ulaw_frame(seed: int, size: int = 160) -> str:
    """A base64 μ-law frame containing the byte patterns that break naive handling.

    Spans 0x00 and 0xFF, which a UTF-8 round-trip or a str/bytes confusion
    mangles, at lengths whose base64 form carries padding.
    """
    raw = bytes([(seed + i) % 256 for i in range(size)])
    return base64.b64encode(raw).decode("ascii")


def _start_frame() -> dict[str, Any]:
    return {
        "event": "start",
        "start": {"streamSid": STREAM_SID, "callSid": "CA123", "tracks": ["inbound"]},
    }


def _media_frame(payload: str) -> dict[str, Any]:
    return {"event": "media", "streamSid": STREAM_SID, "media": {"payload": payload}}


def _mark_frame(name: str) -> dict[str, Any]:
    return {"event": "mark", "streamSid": STREAM_SID, "mark": {"name": name}}


def _bridge(
    config: ClientConfig,
    twilio: FakeTwilio,
    realtime: FakeRealtime,
    hooks: BridgeHooks | None = None,
) -> MediaBridge:
    return MediaBridge(
        twilio=twilio,  # type: ignore[arg-type]
        realtime=realtime,  # type: ignore[arg-type]
        config=config,
        state=CallState(call_id="c1", client_id=config.client_id, from_e164="+13125551111"),
        tools=RegistryBundle(registry=ToolRegistry(), context={}),
        hooks=hooks,
    )


async def _run(bridge: MediaBridge) -> Any:
    return await asyncio.wait_for(bridge.run(), timeout=5)


# -- inbound: caller -> model ------------------------------------------------


async def test_inbound_audio_reaches_openai_byte_identical(config: ClientConfig) -> None:
    payloads = [_ulaw_frame(seed) for seed in (0, 61, 200)]
    twilio = FakeTwilio([_start_frame(), *[_media_frame(p) for p in payloads], {"event": "stop"}])
    realtime = FakeRealtime()

    stats = await _run(_bridge(config, twilio, realtime))

    # The exact strings, in order, with nothing added or normalised.
    assert realtime.appended == payloads
    assert stats.frames_from_caller == 3
    # And the bytes those strings stand for survived the trip.
    assert [base64.b64decode(p) for p in realtime.appended] == [
        base64.b64decode(p) for p in payloads
    ]


async def test_malformed_frame_is_skipped_without_killing_the_call(
    config: ClientConfig,
) -> None:
    twilio = FakeTwilio(
        [_start_frame(), {}, _media_frame(_ulaw_frame(1)), {"event": "stop"}]
    )
    realtime = FakeRealtime()

    stats = await _run(_bridge(config, twilio, realtime))

    assert stats.frames_from_caller == 1
    assert stats.errors == []


# -- outbound: model -> caller -----------------------------------------------


async def test_outbound_audio_reaches_twilio_byte_identical(config: ClientConfig) -> None:
    deltas = [_ulaw_frame(seed) for seed in (7, 128, 255)]
    realtime = FakeRealtime([{"type": EV_OUTPUT_AUDIO_DELTA, "delta": d} for d in deltas])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime))

    assert twilio.media_payloads() == deltas


async def test_every_delta_is_followed_by_exactly_one_mark(config: ClientConfig) -> None:
    realtime = FakeRealtime(
        [{"type": EV_OUTPUT_AUDIO_DELTA, "delta": _ulaw_frame(i)} for i in range(4)]
    )
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    stats = await _run(_bridge(config, twilio, realtime))

    # media, mark, media, mark, ... — the mark must trail the audio it measures.
    assert twilio.events() == ["media", "mark"] * 4
    assert twilio.marks() == ["m1", "m2", "m3", "m4"]
    assert stats.frames_to_caller == 4
    assert stats.marks_sent == 4


async def test_audio_before_the_start_frame_is_dropped(config: ClientConfig) -> None:
    """Without a streamSid there is nowhere to send it, and Twilio would reject it."""
    realtime = FakeRealtime([{"type": EV_OUTPUT_AUDIO_DELTA, "delta": _ulaw_frame(3)}])
    twilio = FakeTwilio([settled(realtime), {"event": "stop"}])

    stats = await _run(_bridge(config, twilio, realtime))

    assert twilio.media_payloads() == []
    assert stats.frames_to_caller == 0


async def test_empty_delta_is_not_forwarded(config: ClientConfig) -> None:
    realtime = FakeRealtime([{"type": EV_OUTPUT_AUDIO_DELTA, "delta": ""}])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime))

    assert twilio.media_payloads() == []


# -- the ledger sees real byte counts ---------------------------------------


async def test_mark_ack_advances_the_ledger_by_the_decoded_size(
    config: ClientConfig,
) -> None:
    delta = _ulaw_frame(9, size=800)  # 100 ms of μ-law
    realtime = FakeRealtime([{"type": EV_OUTPUT_AUDIO_DELTA, "delta": delta}])
    twilio = FakeTwilio(
        [_start_frame(), settled(realtime), _mark_frame("m1"), {"event": "stop"}]
    )
    bridge = _bridge(config, twilio, realtime)

    stats = await _run(bridge)

    assert stats.marks_acked == 1
    assert bridge.ledger.played_bytes == 800
    assert bridge.ledger.unplayed_ms() == 0


async def test_unknown_mark_ack_is_not_counted(config: ClientConfig) -> None:
    twilio = FakeTwilio([_start_frame(), _mark_frame("ghost"), {"event": "stop"}])

    stats = await _run(_bridge(config, twilio, FakeRealtime()))

    assert stats.marks_acked == 0


@pytest.mark.parametrize("size", [*range(1, 40), 160, 320, 800, 8000])
def test_decoded_size_arithmetic_matches_an_actual_decode(size: int) -> None:
    """`_b64_decoded_size` exists to avoid allocating; it must not drift."""
    payload = base64.b64encode(os.urandom(size)).decode("ascii")
    assert _b64_decoded_size(payload) == decode_size_exact(payload) == size


def test_decoded_size_of_empty_payload_is_zero() -> None:
    assert _b64_decoded_size("") == 0


# -- session setup and barge-in wiring ---------------------------------------


async def test_start_frame_sends_session_update_then_greets(config: ClientConfig) -> None:
    twilio = FakeTwilio([_start_frame(), {"event": "stop"}])
    realtime = FakeRealtime()

    bridge = _bridge(config, twilio, realtime)
    await _run(bridge)

    assert realtime.types()[:2] == ["session.update", "response.create"]
    session = realtime.sent[0]["session"]
    # PCMU both directions. This is the config that makes passthrough legal.
    assert session["audio"]["input"]["format"] == {"type": "audio/pcmu"}
    assert session["audio"]["output"]["format"] == {"type": "audio/pcmu"}
    # The greeting is spoken out-of-band so it never enters conversation state.
    assert realtime.sent[1]["response"]["input"] == []
    assert config.realtime.greeting in realtime.sent[1]["response"]["instructions"]
    assert bridge.stream_sid == STREAM_SID


async def test_speech_started_truncates_at_what_the_caller_actually_heard(
    config: ClientConfig,
) -> None:
    delta = _ulaw_frame(11, size=1600)  # 200 ms
    realtime = FakeRealtime(
        [
            {"type": EV_OUTPUT_ITEM_ADDED, "item": {"type": "message", "id": "item-x"}},
            {"type": EV_OUTPUT_AUDIO_DELTA, "delta": delta},
        ]
    )
    twilio = FakeTwilio(
        [
            _start_frame(),
            settled(realtime),
            _mark_frame("m1"),  # caller heard all 200 ms
            push(realtime, {"type": EV_SPEECH_STARTED}),
            settled(realtime),
            {"event": "stop"},
        ]
    )
    events: list[tuple[str, dict[str, Any]]] = []

    async def on_event(kind: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))

    bridge = _bridge(config, twilio, realtime, BridgeHooks(on_event=on_event))
    stats = await _run(bridge)

    assert "clear" in twilio.events()
    truncate = realtime.payload("conversation.item.truncate")
    assert truncate["item_id"] == "item-x"
    assert truncate["audio_end_ms"] == 200
    # cancel must precede truncate on the model socket.
    types = realtime.types()
    assert types.index("response.cancel") < types.index("conversation.item.truncate")
    assert stats.barge_ins == 1
    assert [k for k, _ in events].count("barge_in") == 1


async def test_speech_started_while_agent_is_silent_is_harmless(
    config: ClientConfig,
) -> None:
    realtime = FakeRealtime([{"type": EV_SPEECH_STARTED}])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    stats = await _run(_bridge(config, twilio, realtime))

    assert "clear" in twilio.events()
    assert "conversation.item.truncate" not in realtime.types()
    assert stats.barge_ins == 0
    assert stats.errors == []


async def test_realtime_error_is_recorded_without_ending_the_call(
    config: ClientConfig,
) -> None:
    realtime = FakeRealtime(
        [{"type": "error", "error": {"code": "rate_limit_exceeded", "message": "slow down"}}]
    )
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    stats = await _run(_bridge(config, twilio, realtime))

    assert stats.errors == ["rate_limit_exceeded"]


async def test_transcripts_are_handed_to_the_turn_sink(config: ClientConfig) -> None:
    realtime = FakeRealtime(
        [
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "My furnace is out.",
            },
            {"type": "response.output_audio_transcript.done", "transcript": "I can help."},
            {"type": "response.output_audio_transcript.done", "transcript": "   "},
        ]
    )
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])
    turns: list[tuple[str, str, int, dict[str, Any]]] = []

    async def on_turn(role: str, text: str, at_ms: int, meta: dict[str, Any]) -> None:
        turns.append((role, text, at_ms, meta))

    bridge = _bridge(config, twilio, realtime, BridgeHooks(on_turn=on_turn))
    await _run(bridge)

    # Whitespace-only transcripts are dropped rather than stored as empty turns.
    assert [(role, text) for role, text, _, _ in turns] == [
        ("caller", "My furnace is out."),
        ("agent", "I can help."),
    ]
    assert bridge.state.caller_turns == 1
    assert bridge.state.agent_turns == 1
    # The agent turn carries the time-to-first-response it was measured against.
    assert "latency_ms" in turns[1][3]


async def test_stop_frame_ends_the_call(config: ClientConfig) -> None:
    twilio = FakeTwilio([_start_frame(), {"event": "stop"}])
    bridge = _bridge(config, twilio, FakeRealtime())

    stats = await _run(bridge)

    assert stats.errors == []


# -- lifecycle: close, flush, bounded queue ----------------------------------


async def test_stop_closes_both_sockets(config: ClientConfig) -> None:
    """A `stop` that leaves a socket open leaks a connection per call."""
    twilio = FakeTwilio([_start_frame(), {"event": "stop"}])
    realtime = FakeRealtime()

    await _run(_bridge(config, twilio, realtime))

    assert twilio.closed is True
    assert realtime.closed is True


async def test_queued_audio_is_flushed_before_the_sockets_close(
    config: ClientConfig,
) -> None:
    """Audio handed to the bridge before `stop` still reaches the caller."""
    deltas = [_ulaw_frame(seed) for seed in (5, 15, 25)]
    realtime = FakeRealtime([{"type": EV_OUTPUT_AUDIO_DELTA, "delta": d} for d in deltas])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    stats = await _run(_bridge(config, twilio, realtime))

    assert twilio.media_payloads() == deltas
    assert stats.frames_to_caller == 3
    assert stats.frames_dropped == 0


async def test_partial_agent_turn_is_flushed_when_the_caller_hangs_up(
    config: ClientConfig,
) -> None:
    """A caller who hangs up mid-answer must not lose the transcript so far."""
    realtime = FakeRealtime(
        [
            {"type": "response.output_audio_transcript.delta", "delta": "Your technician "},
            {"type": "response.output_audio_transcript.delta", "delta": "can be there at two"},
        ]
    )
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])
    turns: list[tuple[str, str]] = []

    async def on_turn(role: str, text: str, at_ms: int, meta: dict[str, Any]) -> None:
        turns.append((role, text))

    bridge = _bridge(config, twilio, realtime, BridgeHooks(on_turn=on_turn))
    await _run(bridge)

    assert turns == [("agent", "Your technician can be there at two")]
    assert bridge.state.agent_turns == 1


async def test_completed_turn_is_not_flushed_twice(config: ClientConfig) -> None:
    """`.done` clears the partial buffer, so teardown must not re-persist it."""
    realtime = FakeRealtime(
        [
            {"type": "response.output_audio_transcript.delta", "delta": "I can help."},
            {"type": "response.output_audio_transcript.done", "transcript": "I can help."},
        ]
    )
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])
    turns: list[tuple[str, str]] = []

    async def on_turn(role: str, text: str, at_ms: int, meta: dict[str, Any]) -> None:
        turns.append((role, text))

    bridge = _bridge(config, twilio, realtime, BridgeHooks(on_turn=on_turn))
    await _run(bridge)

    assert turns == [("agent", "I can help.")]
    assert bridge.state.agent_turns == 1


async def test_outbound_queue_is_bounded_and_drops_oldest(config: ClientConfig) -> None:
    """A stalled socket must cost frames, not unbounded memory."""
    overflow = OUTBOUND_QUEUE_MAX + 25
    bridge = _bridge(config, FakeTwilio([]), FakeRealtime())
    bridge.stream_sid = STREAM_SID

    # Enqueue without ever running the pump: this is a socket that has stalled.
    for i in range(overflow):
        await bridge._send_audio(_ulaw_frame(i % 256))

    assert bridge._outbound.qsize() == OUTBOUND_QUEUE_MAX
    assert bridge.stats.frames_dropped == overflow - OUTBOUND_QUEUE_MAX
    # Oldest dropped, newest kept: the surviving head is not mark m1.
    assert bridge._outbound.get_nowait().mark_name != "m1"


async def test_barge_in_discards_queued_audio_before_clear(config: ClientConfig) -> None:
    """Our own queue must be dropped first, or the pump writes past the `clear`."""
    bridge = _bridge(config, FakeTwilio([]), FakeRealtime())
    bridge.stream_sid = STREAM_SID
    for i in range(5):
        await bridge._send_audio(_ulaw_frame(i))
    assert bridge._outbound.qsize() == 5

    dropped = bridge._discard_queued_audio()

    assert dropped == 5
    assert bridge._outbound.qsize() == 0


async def test_shutdown_closes_a_live_call_gracefully(config: ClientConfig) -> None:
    """The graceful-shutdown path: drain, flush, close — without a `stop` frame."""
    realtime = FakeRealtime()
    twilio = FakeTwilio([_start_frame()])  # no stop: the process is going down
    bridge = _bridge(config, twilio, realtime)

    task = asyncio.create_task(bridge.run())
    await asyncio.sleep(0)  # let the pumps start
    await bridge.shutdown()
    stats = await asyncio.wait_for(task, timeout=5)

    assert twilio.closed is True
    assert realtime.closed is True
    assert stats.errors == []


async def test_aclose_is_idempotent(config: ClientConfig) -> None:
    """`run()`'s finally and the shutdown hook both call it."""
    realtime = FakeRealtime()
    twilio = FakeTwilio([_start_frame(), {"event": "stop"}])
    bridge = _bridge(config, twilio, realtime)
    turns: list[tuple[str, str]] = []

    async def on_turn(role: str, text: str, at_ms: int, meta: dict[str, Any]) -> None:
        turns.append((role, text))

    bridge.hooks = BridgeHooks(on_turn=on_turn)
    bridge._partial_agent_text = "half a sentence"

    await _run(bridge)
    await bridge.aclose()
    await bridge.aclose()

    # Flushed exactly once despite three teardowns.
    assert turns == [("agent", "half a sentence")]

