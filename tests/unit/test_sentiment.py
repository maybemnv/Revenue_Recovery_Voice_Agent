"""Live sentiment: the out-of-band classifier that makes the frustration rule real.

`should_escalate` has always read `negative_sentiment_turns`, but until the
classifier was wired in nothing ever moved that counter — the "two negative turns
→ get a human" rule was unreachable. These tests pin the wiring end to end.

Three properties carry the design.

*The classifier must not be audible.* It rides `conversation: "none"` with
`output_modalities: ["text"]`, so it emits no audio and never enters the
conversation the next spoken answer reasons over. A regression here is a caller
hearing the word "frustrated" read back at them.

*A verdict must be correlated, not assumed.* The classifier's `response.done`
arrives interleaved with the spoken turn's. They are told apart by
`response.metadata.topic`, and the wrong branch would reset the voice-to-voice
latency clock mid-measurement — silently corrupting the headline metric rather
than failing.

*An unparseable answer must be inert.* `record_sentiment` treats anything outside
the negative set as a streak reset, so a garbled label can only ever lose an
escalation, never invent one.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from apps.api.config.schema import ClientConfig
from apps.api.domain.state import CallState
from apps.api.media.bridge import SENTIMENT_MAX_CHARS, BridgeHooks, MediaBridge
from apps.api.media.realtime_client import (
    EV_INPUT_TRANSCRIPT_COMPLETED,
    EV_OUTPUT_AUDIO_DELTA,
    EV_OUTPUT_AUDIO_TRANSCRIPT_DONE,
    EV_RESPONSE_DONE,
    EV_SPEECH_STOPPED,
    OOB_TOPIC_SENTIMENT,
)
from apps.api.tools.registry import RegistryBundle, ToolRegistry

STREAM_SID = "MZsentiment"

Step = Callable[[], Awaitable[None]]


class FakeRealtime:
    """Mirrors `RealtimeClient`'s senders; announces idle so tests need no sleeps."""

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

    def classifications(self) -> list[dict[str, Any]]:
        """Every out-of-band classifier request, in order."""
        return [
            p["response"]
            for p in self.sent
            if p.get("type") == "response.create"
            and (p.get("response") or {}).get("metadata", {}).get("topic")
            == OOB_TOPIC_SENTIMENT
        ]


class FakeTwilio:
    def __init__(self, frames: list[dict[str, Any] | Step]) -> None:
        self._frames: list[dict[str, Any] | Step] = list(frames)
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._shut = asyncio.Event()

    async def receive_text(self) -> str:
        while True:
            if not self._frames:
                await self._shut.wait()
                raise RuntimeError("socket closed")
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


def settled(realtime: FakeRealtime) -> Step:
    async def _step() -> None:
        await realtime.idle.wait()
        realtime.idle.clear()

    return _step


def push(realtime: FakeRealtime, event: dict[str, Any]) -> Step:
    async def _step() -> None:
        await realtime.feed.put(event)

    return _step


def _start_frame() -> dict[str, Any]:
    return {
        "event": "start",
        "start": {"streamSid": STREAM_SID, "callSid": "CA123", "tracks": ["inbound"]},
    }


def _said(text: str) -> dict[str, Any]:
    return {"type": EV_INPUT_TRANSCRIPT_COMPLETED, "transcript": text}


def _verdict(label: str) -> dict[str, Any]:
    """A finished classifier response, carrying its topic back on the metadata."""
    return {
        "type": EV_RESPONSE_DONE,
        "response": {
            "metadata": {"topic": OOB_TOPIC_SENTIMENT},
            "output": [{"content": [{"type": "text", "text": label}]}],
        },
    }


def _bridge(
    config: ClientConfig,
    twilio: FakeTwilio,
    realtime: FakeRealtime,
    hooks: BridgeHooks | None = None,
    state: CallState | None = None,
) -> MediaBridge:
    return MediaBridge(
        twilio=twilio,  # type: ignore[arg-type]
        realtime=realtime,  # type: ignore[arg-type]
        config=config,
        state=state
        or CallState(call_id="c1", client_id=config.client_id, from_e164="+13125551111"),
        tools=RegistryBundle(registry=ToolRegistry(), context={}),
        hooks=hooks,
    )


async def _run(bridge: MediaBridge) -> Any:
    return await asyncio.wait_for(bridge.run(), timeout=5)


def _events(captured: list[tuple[str, dict[str, Any]]], kind: str) -> list[dict[str, Any]]:
    return [payload for name, payload in captured if name == kind]


def _capture(into: list[tuple[str, dict[str, Any]]]) -> BridgeHooks:
    async def on_event(name: str, payload: dict[str, Any]) -> None:
        into.append((name, payload))

    return BridgeHooks(on_event=on_event)


# -- the request ---------------------------------------------------------------


async def test_caller_turn_asks_for_a_classification(config: ClientConfig) -> None:
    realtime = FakeRealtime([_said("this is the third time I have called")])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime))

    assert len(realtime.classifications()) == 1


async def test_the_classifier_is_silent_and_outside_the_conversation(
    config: ClientConfig,
) -> None:
    """Text-only and `conversation: "none"` — otherwise the caller hears the label."""
    realtime = FakeRealtime([_said("I am not happy about this")])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime))

    request = realtime.classifications()[0]
    assert request["conversation"] == "none"
    assert request["output_modalities"] == ["text"]
    assert request["input"] == []


async def test_the_utterance_travels_in_the_instructions(config: ClientConfig) -> None:
    """`conversation: "none"` means the model cannot see the turn it is judging."""
    realtime = FakeRealtime([_said("my heating has been broken for a week")])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime))

    assert "my heating has been broken for a week" in realtime.classifications()[0][
        "instructions"
    ]


async def test_a_monologue_is_truncated(config: ClientConfig) -> None:
    realtime = FakeRealtime([_said("so " * 4000)])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime))

    instructions = realtime.classifications()[0]["instructions"]
    assert len(instructions) < SENTIMENT_MAX_CHARS + 400


async def test_card_digits_never_reach_the_classifier(config: ClientConfig) -> None:
    """The classifier is one more sink, and it gets the same redaction as the rest."""
    realtime = FakeRealtime([_said("my card is 4111 1111 1111 1111")])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime))

    assert "4111" not in realtime.classifications()[0]["instructions"]


async def test_an_empty_transcript_asks_nothing(config: ClientConfig) -> None:
    realtime = FakeRealtime([_said("   ")])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime))

    assert realtime.classifications() == []


async def test_classification_is_skippable_per_client(
    client_config_dict: dict[str, Any],
) -> None:
    """One text-only response per caller turn is a real cost; it can be turned off."""
    client_config_dict["escalation"]["live_sentiment"] = False
    config = ClientConfig.model_validate(client_config_dict)
    realtime = FakeRealtime([_said("I am furious")])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime))

    assert realtime.classifications() == []


# -- the verdict ---------------------------------------------------------------


async def test_a_negative_verdict_moves_the_counter(config: ClientConfig) -> None:
    state = CallState(call_id="c1", client_id=config.client_id, from_e164="+13125551111")
    realtime = FakeRealtime([_verdict("frustrated")])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime, state=state))

    assert state.negative_sentiment_turns == 1


async def test_a_positive_verdict_resets_the_streak(config: ClientConfig) -> None:
    """One good turn clears it: the rule is *consecutive* negative turns."""
    state = CallState(call_id="c1", client_id=config.client_id, from_e164="+13125551111")
    state.negative_sentiment_turns = 1
    realtime = FakeRealtime([_verdict("positive")])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime, state=state))

    assert state.negative_sentiment_turns == 0


async def test_a_verdict_is_reported_as_an_event(config: ClientConfig) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    realtime = FakeRealtime([_verdict("angry")])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime, hooks=_capture(captured)))

    assert _events(captured, "sentiment") == [{"label": "angry", "negative_turns": 1}]


async def test_a_verdict_is_normalised_before_matching(config: ClientConfig) -> None:
    """Models add punctuation and capitals; that is not a parse failure."""
    state = CallState(call_id="c1", client_id=config.client_id, from_e164="+13125551111")
    realtime = FakeRealtime([_verdict("  Frustrated.  ")])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime, state=state))

    assert state.negative_sentiment_turns == 1


async def test_an_unparseable_verdict_leaves_state_untouched(config: ClientConfig) -> None:
    """A garbled label must not reset a streak either — it is not evidence."""
    captured: list[tuple[str, dict[str, Any]]] = []
    state = CallState(call_id="c1", client_id=config.client_id, from_e164="+13125551111")
    state.negative_sentiment_turns = 1
    realtime = FakeRealtime([_verdict("I would say the caller seems a bit tense")])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime, state=state, hooks=_capture(captured)))

    assert state.negative_sentiment_turns == 1
    assert _events(captured, "sentiment") == []


async def test_an_empty_verdict_is_ignored(config: ClientConfig) -> None:
    state = CallState(call_id="c1", client_id=config.client_id, from_e164="+13125551111")
    realtime = FakeRealtime([{"type": EV_RESPONSE_DONE, "response": {
        "metadata": {"topic": OOB_TOPIC_SENTIMENT}, "output": []}}])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime, state=state))

    assert state.negative_sentiment_turns == 0


# -- escalation ----------------------------------------------------------------


async def test_two_consecutive_negative_turns_escalate(config: ClientConfig) -> None:
    """The rule `should_escalate` has always had, now reachable."""
    captured: list[tuple[str, dict[str, Any]]] = []
    state = CallState(call_id="c1", client_id=config.client_id, from_e164="+13125551111")
    realtime = FakeRealtime([_verdict("negative"), _verdict("angry")])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime, state=state, hooks=_capture(captured)))

    assert state.escalated
    reasons = [e["reason"] for e in _events(captured, "escalation")]
    assert any("frustration" in r.lower() for r in reasons)


async def test_one_negative_turn_does_not_escalate(config: ClientConfig) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    state = CallState(call_id="c1", client_id=config.client_id, from_e164="+13125551111")
    realtime = FakeRealtime([_verdict("negative")])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime, state=state, hooks=_capture(captured)))

    assert not state.escalated
    assert _events(captured, "escalation") == []


async def test_an_escalated_call_stops_classifying(config: ClientConfig) -> None:
    """Once a human is being fetched, the label cannot change the outcome."""
    state = CallState(call_id="c1", client_id=config.client_id, from_e164="+13125551111")
    state.escalated = True
    realtime = FakeRealtime([_said("I am still angry")])
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime, state=state))

    assert realtime.classifications() == []


# -- the latency clock ---------------------------------------------------------


async def test_a_verdict_does_not_reset_the_voice_to_voice_clock(
    config: ClientConfig,
) -> None:
    """The classifier's `done` races the spoken turn's; only the topic tells them apart.

    Before the metadata routing existed, the classifier's `response.done` cleared
    `_pending_response_start` mid-measurement and the agent turn was persisted
    without its latency — silently losing the headline metric on every negative
    call rather than failing loudly.
    """
    recorded: list[tuple[str, dict[str, Any]]] = []

    async def on_turn(role: str, text: str, at_ms: int, meta: dict[str, Any]) -> None:
        recorded.append((role, meta))

    hooks = BridgeHooks(on_turn=on_turn)
    realtime = FakeRealtime(
        [
            {"type": EV_SPEECH_STOPPED},
            _verdict("frustrated"),
            {"type": EV_OUTPUT_AUDIO_DELTA, "delta": "AAAA"},
            {"type": EV_OUTPUT_AUDIO_TRANSCRIPT_DONE, "transcript": "I hear you."},
        ]
    )
    twilio = FakeTwilio([_start_frame(), settled(realtime), {"event": "stop"}])

    await _run(_bridge(config, twilio, realtime, hooks=hooks))

    assert any(role == "agent" and "latency_ms" in meta for role, meta in recorded)



