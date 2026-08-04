"""Barge-in ordering: clear, then cancel, then truncate — and never any other way.

The two sockets record into one shared list so the assertion reads as the actual
wire order, which is the property that matters. `clear` late means the agent
talks over the caller; `truncate` computed after the awaits means the model's
belief drifts from what was heard.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.api.media.barge_in import BargeInController
from apps.api.media.playback_ledger import PlaybackLedger

CHUNK = 160


class Recorder:
    """One ordered log, two socket views onto it."""

    def __init__(self) -> None:
        self.log: list[tuple[str, dict[str, Any]]] = []

    def socket(self, label: str) -> RecordingSocket:
        return RecordingSocket(self, label)

    def order(self) -> list[str]:
        return [
            f"{label}:{payload.get('event') or payload.get('type')}"
            for label, payload in self.log
        ]

    def payload(self, kind: str) -> dict[str, Any]:
        for _, payload in self.log:
            if payload.get("event") == kind or payload.get("type") == kind:
                return payload
        raise AssertionError(f"{kind} was never sent; got {self.order()}")


class RecordingSocket:
    def __init__(self, recorder: Recorder, label: str) -> None:
        self._recorder = recorder
        self._label = label

    async def send_json(self, payload: dict[str, Any]) -> None:
        self._recorder.log.append((self._label, payload))


def _controller(recorder: Recorder, ledger: PlaybackLedger, **kwargs: Any) -> BargeInController:
    return BargeInController(
        twilio=recorder.socket("twilio"),
        realtime=recorder.socket("realtime"),
        ledger=ledger,
        stream_sid="MZ123",
        **kwargs,
    )


def _speaking_ledger(*, chunks: int = 10, acked: int = 4) -> PlaybackLedger:
    ledger = PlaybackLedger()
    ledger.on_item_started("item-abc")
    for i in range(chunks):
        ledger.on_chunk_sent(f"m{i}", CHUNK)
    if acked:
        ledger.on_mark_ack(f"m{acked - 1}")
    return ledger


async def test_send_order_is_clear_then_cancel_then_truncate() -> None:
    recorder = Recorder()
    controller = _controller(recorder, _speaking_ledger())

    await controller.on_speech_started()

    assert recorder.order() == [
        "twilio:clear",
        "realtime:response.cancel",
        "realtime:conversation.item.truncate",
    ]


async def test_clear_targets_the_stream_sid() -> None:
    recorder = Recorder()
    await _controller(recorder, _speaking_ledger()).on_speech_started()

    assert recorder.payload("clear") == {"event": "clear", "streamSid": "MZ123"}


async def test_audio_end_ms_is_what_the_caller_heard_not_what_was_queued() -> None:
    recorder = Recorder()
    ledger = _speaking_ledger(chunks=10, acked=4)  # 200 ms queued, 80 ms played

    result = await _controller(recorder, ledger).on_speech_started()

    truncate = recorder.payload("conversation.item.truncate")
    assert truncate["audio_end_ms"] == 80
    assert truncate["item_id"] == "item-abc"
    assert truncate["content_index"] == 0
    assert result.discarded_ms == 120


async def test_a_mark_acked_mid_flight_cannot_move_the_truncation_point() -> None:
    """The offset is read before the first await, so later acks are irrelevant."""
    recorder = Recorder()
    ledger = _speaking_ledger(chunks=10, acked=4)

    class AckingSocket(RecordingSocket):
        async def send_json(self, payload: dict[str, Any]) -> None:
            # Simulate Twilio acking more audio while we are mid-sequence.
            ledger.on_mark_ack("m9")
            await super().send_json(payload)

    controller = BargeInController(
        twilio=AckingSocket(recorder, "twilio"),
        realtime=recorder.socket("realtime"),
        ledger=ledger,
        stream_sid="MZ123",
    )
    await controller.on_speech_started()

    assert recorder.payload("conversation.item.truncate")["audio_end_ms"] == 80


async def test_silent_agent_still_clears_and_cancels_but_does_not_truncate() -> None:
    """speech_started while the agent is silent is the common case, not an error."""
    recorder = Recorder()
    ledger = PlaybackLedger()

    result = await _controller(recorder, ledger).on_speech_started()

    assert recorder.order() == ["twilio:clear", "realtime:response.cancel"]
    assert result.truncated is False
    assert result.audio_end_ms == 0


async def test_nothing_played_yet_does_not_truncate_at_zero() -> None:
    """Truncating at 0 ms would tell the model it said nothing it did not say."""
    recorder = Recorder()
    ledger = _speaking_ledger(chunks=6, acked=0)  # queued, none acked

    result = await _controller(recorder, ledger).on_speech_started()

    assert "realtime:conversation.item.truncate" not in recorder.order()
    assert result.truncated is False
    assert result.discarded_ms == 120


async def test_ledger_is_reset_so_the_next_item_starts_from_zero() -> None:
    recorder = Recorder()
    ledger = _speaking_ledger()

    await _controller(recorder, ledger).on_speech_started()

    assert ledger.unplayed_ms() == 0
    assert ledger.outstanding_marks == 0
    assert ledger.played_ms_for_current_item() == 0


async def test_back_to_back_barge_ins_do_not_accumulate_offsets() -> None:
    recorder = Recorder()
    ledger = _speaking_ledger(chunks=10, acked=4)
    controller = _controller(recorder, ledger)

    first = await controller.on_speech_started()
    assert first.audio_end_ms == 80

    # New response item, 3 chunks queued, 2 played.
    ledger.on_item_started("item-def")
    for i in range(3):
        ledger.on_chunk_sent(f"n{i}", CHUNK)
    ledger.on_mark_ack("n1")

    second = await controller.on_speech_started()
    assert second.audio_end_ms == 40
    assert second.item_id == "item-def"


async def test_event_is_emitted_with_the_truncation_detail() -> None:
    recorder = Recorder()
    emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(name: str, payload: dict[str, Any]) -> None:
        emitted.append((name, payload))

    await _controller(recorder, _speaking_ledger(), emit_event=emit).on_speech_started()

    assert len(emitted) == 1
    name, payload = emitted[0]
    assert name == "barge_in"
    assert payload["truncated_at_ms"] == 80
    assert payload["discarded_ms"] == 120
    assert payload["truncated"] is True


async def test_a_failing_socket_surfaces_rather_than_silently_desyncing() -> None:
    """If `clear` fails we must not pretend the cut happened."""

    class BrokenSocket:
        async def send_json(self, payload: dict[str, Any]) -> None:
            raise ConnectionError("twilio socket closed")

    controller = BargeInController(
        twilio=BrokenSocket(),
        realtime=Recorder().socket("realtime"),
        ledger=_speaking_ledger(),
        stream_sid="MZ123",
    )
    with pytest.raises(ConnectionError):
        await controller.on_speech_started()
