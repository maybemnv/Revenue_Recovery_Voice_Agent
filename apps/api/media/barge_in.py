"""Caller interruption: flush, cancel, reconcile — in that order.

`clear` goes first because the buffered audio is the thing the caller is talking
over. Every millisecond it keeps playing is a millisecond of the agent talking
over the customer, and it is the only one of the three sends that is on the
caller's critical path. `response.cancel` and `conversation.item.truncate` are
housekeeping with the model; they cost the caller nothing.

`audio_end_ms` is read from the ledger *before* anything is sent, so the value
describes the moment of the cut rather than the moment the last await returned.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from apps.api.media.playback_ledger import PlaybackLedger
from apps.api.observability.logging import get_logger

log = get_logger(__name__)


class JsonSocket(Protocol):
    async def send_json(self, payload: dict[str, Any]) -> None: ...


EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class BargeInResult:
    """What the cut actually did. Persisted and asserted on in tests."""

    truncated: bool
    item_id: str | None
    audio_end_ms: int
    cutoff_ms: float
    discarded_ms: int


class BargeInController:
    def __init__(
        self,
        *,
        twilio: JsonSocket,
        realtime: JsonSocket,
        ledger: PlaybackLedger,
        stream_sid: str,
        emit_event: EventEmitter | None = None,
    ) -> None:
        self.twilio = twilio
        self.realtime = realtime
        self.ledger = ledger
        self.stream_sid = stream_sid
        self._emit_event = emit_event

    async def on_speech_started(self) -> BargeInResult:
        started = time.perf_counter()

        # Read before sending: this is the state at the instant of the cut.
        audio_end_ms = self.ledger.played_ms_for_current_item()
        item_id = self.ledger.current_item_id
        discarded_ms = self.ledger.unplayed_ms()

        # 1. Flush Twilio's outbound buffer FIRST. Anything still queued is audio
        #    the caller has not heard and must not hear now that they are talking.
        await self.twilio.send_json({"event": "clear", "streamSid": self.stream_sid})

        # 2. Cancel generation. Under semantic_vad with interrupt_response the
        #    server usually does this itself; sending it is idempotent and covers
        #    the race where it has not yet.
        await self.realtime.send_json({"type": "response.cancel"})

        # 3. Reconcile the model's belief with what the caller actually heard.
        #    With no in-flight item there is nothing to reconcile — a
        #    speech_started while the agent is silent is the common case, not an
        #    error, and must not raise.
        truncated = item_id is not None and audio_end_ms > 0
        if truncated:
            await self.realtime.send_json(
                {
                    "type": "conversation.item.truncate",
                    "item_id": item_id,
                    "content_index": 0,
                    "audio_end_ms": audio_end_ms,
                }
            )

        cutoff_ms = (time.perf_counter() - started) * 1000
        self.ledger.begin_new_item()

        result = BargeInResult(
            truncated=truncated,
            item_id=item_id,
            audio_end_ms=audio_end_ms,
            cutoff_ms=cutoff_ms,
            discarded_ms=discarded_ms,
        )

        if self._emit_event is not None:
            await self._emit_event(
                "barge_in",
                {
                    "truncated_at_ms": audio_end_ms,
                    "item_id": item_id,
                    "cutoff_ms": round(cutoff_ms, 2),
                    "discarded_ms": discarded_ms,
                    "truncated": truncated,
                },
            )

        log.info(
            "barge_in",
            item_id=item_id,
            audio_end_ms=audio_end_ms,
            cutoff_ms=round(cutoff_ms, 2),
            discarded_ms=discarded_ms,
        )
        return result
