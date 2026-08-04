"""Bytes queued to Twilio, and bytes Twilio confirms it has played.

Over a WebSocket the client owns playback, so the client owns truncation. Twilio
echoes a `mark` only *after* that audio has left the speaker, which makes the
mark stream the only honest signal of what the caller actually heard. Everything
the barge-in controller sends to OpenAI is derived from this file.

Two operations look similar and are not:

* `on_item_started` is the ordinary transition between response items. The new
  item's audio plays after everything already queued, so its offset is the
  queued watermark and outstanding marks stay live.
* `begin_new_item` is the post-`clear` reset. Twilio has thrown the queue away,
  so outstanding marks will never be acked and the queued watermark collapses
  back onto what was really played.

Confusing those two is how a truncation offset silently drifts for the rest of
a call.
"""

from __future__ import annotations

from dataclasses import dataclass

ULAW_BYTES_PER_SECOND = 8000  # 8 kHz, 1 byte per sample


def _bytes_to_ms(num_bytes: int) -> int:
    return max(0, int(num_bytes / ULAW_BYTES_PER_SECOND * 1000))


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    """Immutable view, safe to hand to the live dashboard or mirror to Redis."""

    played_bytes: int
    queued_bytes: int
    outstanding_marks: int
    current_item_id: str | None
    item_start_offset: int

    @property
    def played_ms_for_current_item(self) -> int:
        return _bytes_to_ms(self.played_bytes - self.item_start_offset)


class PlaybackLedger:
    def __init__(self) -> None:
        # mark_name -> cumulative bytes queued at and including that mark
        self._marks: dict[str, int] = {}
        self._played_bytes: int = 0
        self._queued_bytes: int = 0
        self.current_item_id: str | None = None
        self.item_start_offset: int = 0

    # -- writes -----------------------------------------------------------
    def on_chunk_sent(self, mark_name: str, payload_bytes: int) -> None:
        """Record audio handed to Twilio, tagged with the mark that follows it."""
        self._queued_bytes += payload_bytes
        self._marks[mark_name] = self._queued_bytes

    def on_mark_ack(self, mark_name: str) -> bool:
        """Twilio played the audio up to this mark. Returns whether it counted.

        Unknown marks are a no-op, which makes duplicate acks idempotent and
        makes an ack that outlived a `clear` harmless. `max` guards the
        out-of-order case: a late ack must never walk the played count
        backwards. A dropped ack self-heals, because any later mark's cumulative
        total already covers every mark before it.
        """
        cumulative = self._marks.pop(mark_name, None)
        if cumulative is None:
            return False
        self._played_bytes = max(self._played_bytes, cumulative)
        self._prune()
        return True

    def _prune(self) -> None:
        """Forget marks the played watermark has already passed."""
        played = self._played_bytes
        for name in [n for n, cum in self._marks.items() if cum <= played]:
            del self._marks[name]

    # -- item boundaries --------------------------------------------------
    def on_item_started(self, item_id: str) -> None:
        """Ordinary transition. The new item queues behind existing audio."""
        self.current_item_id = item_id
        self.item_start_offset = self._queued_bytes

    def begin_new_item(self, item_id: str | None = None) -> None:
        """Reset after a Twilio `clear`. Everything still queued is gone.

        Outstanding marks are dropped rather than kept, so an ack still in
        flight when the buffer was flushed cannot advance the next item's played
        count. Collapsing the queued watermark onto the played one keeps both
        baselines consistent, which is what makes the next item's `played_ms`
        correct even though a few bytes of absolute history are discarded.
        """
        self._marks.clear()
        self._queued_bytes = self._played_bytes
        self.item_start_offset = self._played_bytes
        self.current_item_id = item_id

    # -- reads ------------------------------------------------------------
    def played_ms_for_current_item(self) -> int:
        return _bytes_to_ms(self._played_bytes - self.item_start_offset)

    def queued_ms_for_current_item(self) -> int:
        return _bytes_to_ms(self._queued_bytes - self.item_start_offset)

    def unplayed_ms(self) -> int:
        """Audio handed to Twilio that the caller has not heard yet."""
        return _bytes_to_ms(self._queued_bytes - self._played_bytes)

    @property
    def played_bytes(self) -> int:
        return self._played_bytes

    @property
    def queued_bytes(self) -> int:
        return self._queued_bytes

    @property
    def outstanding_marks(self) -> int:
        return len(self._marks)

    def snapshot(self) -> LedgerSnapshot:
        return LedgerSnapshot(
            played_bytes=self._played_bytes,
            queued_bytes=self._queued_bytes,
            outstanding_marks=len(self._marks),
            current_item_id=self.current_item_id,
            item_start_offset=self.item_start_offset,
        )
