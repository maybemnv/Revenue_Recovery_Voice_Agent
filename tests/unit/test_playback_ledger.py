"""The five mark-ack orderings the ledger has to survive.

These are the tests that justify the ledger existing at all. Each one is a real
thing Twilio does, and the naive implementation (a counter incremented per ack)
gets three of the five wrong.
"""

from __future__ import annotations

from apps.api.media.playback_ledger import ULAW_BYTES_PER_SECOND, PlaybackLedger

CHUNK = 160  # 20 ms of μ-law at 8 kHz


def _queue(ledger: PlaybackLedger, count: int, *, size: int = CHUNK) -> list[str]:
    names = []
    for i in range(count):
        name = f"m{i}"
        ledger.on_chunk_sent(name, size)
        names.append(name)
    return names


def test_bytes_to_ms_matches_the_ulaw_rate() -> None:
    ledger = PlaybackLedger()
    ledger.on_item_started("item-1")
    ledger.on_chunk_sent("m0", ULAW_BYTES_PER_SECOND)
    ledger.on_mark_ack("m0")
    assert ledger.played_ms_for_current_item() == 1000


def test_in_order_acks_advance_one_chunk_at_a_time() -> None:
    ledger = PlaybackLedger()
    ledger.on_item_started("item-1")
    names = _queue(ledger, 5)

    for i, name in enumerate(names, start=1):
        assert ledger.on_mark_ack(name) is True
        assert ledger.played_bytes == i * CHUNK

    assert ledger.played_ms_for_current_item() == 100  # 5 x 20 ms
    assert ledger.outstanding_marks == 0
    assert ledger.unplayed_ms() == 0


def test_out_of_order_acks_never_walk_the_watermark_backwards() -> None:
    """A late ack for an earlier mark must not un-play audio already counted."""
    ledger = PlaybackLedger()
    ledger.on_item_started("item-1")
    names = _queue(ledger, 4)

    ledger.on_mark_ack(names[3])
    assert ledger.played_bytes == 4 * CHUNK

    # The stragglers arrive after the mark that superseded them.
    for name in (names[0], names[1], names[2]):
        ledger.on_mark_ack(name)

    assert ledger.played_bytes == 4 * CHUNK
    assert ledger.played_ms_for_current_item() == 80


def test_dropped_acks_self_heal_on_the_next_one() -> None:
    """Cumulative totals mean a missing ack costs nothing once a later one lands."""
    ledger = PlaybackLedger()
    ledger.on_item_started("item-1")
    names = _queue(ledger, 6)

    # m0, m2, m4 never come back.
    ledger.on_mark_ack(names[1])
    assert ledger.played_bytes == 2 * CHUNK
    ledger.on_mark_ack(names[5])

    assert ledger.played_bytes == 6 * CHUNK
    assert ledger.played_ms_for_current_item() == 120
    assert ledger.outstanding_marks == 0


def test_duplicate_acks_are_idempotent() -> None:
    ledger = PlaybackLedger()
    ledger.on_item_started("item-1")
    names = _queue(ledger, 3)

    assert ledger.on_mark_ack(names[2]) is True
    assert ledger.played_bytes == 3 * CHUNK

    # Second and third delivery of the same mark.
    assert ledger.on_mark_ack(names[2]) is False
    assert ledger.on_mark_ack(names[2]) is False
    assert ledger.played_bytes == 3 * CHUNK


def test_ack_arriving_after_a_clear_cannot_corrupt_the_next_item() -> None:
    """The case that makes `begin_new_item` a separate method from `on_item_started`."""
    ledger = PlaybackLedger()
    ledger.on_item_started("item-1")
    names = _queue(ledger, 10)
    ledger.on_mark_ack(names[2])  # caller heard 3 chunks
    assert ledger.played_ms_for_current_item() == 60

    # Barge-in: Twilio drops the other 7 chunks, and we start a fresh item.
    ledger.begin_new_item("item-2")
    assert ledger.unplayed_ms() == 0
    assert ledger.played_ms_for_current_item() == 0

    # An ack for discarded audio shows up late. It must be ignored.
    assert ledger.on_mark_ack(names[7]) is False
    assert ledger.played_ms_for_current_item() == 0

    # The new item then measures only its own audio.
    ledger.on_chunk_sent("n0", ULAW_BYTES_PER_SECOND // 2)
    ledger.on_mark_ack("n0")
    assert ledger.played_ms_for_current_item() == 500


def test_unplayed_ms_is_what_a_clear_would_discard() -> None:
    ledger = PlaybackLedger()
    ledger.on_item_started("item-1")
    names = _queue(ledger, 10)
    ledger.on_mark_ack(names[1])

    assert ledger.queued_ms_for_current_item() == 200
    assert ledger.played_ms_for_current_item() == 40
    assert ledger.unplayed_ms() == 160


def test_second_item_offsets_from_the_queued_watermark() -> None:
    """Without a clear, a new item queues *behind* whatever is still buffered."""
    ledger = PlaybackLedger()
    ledger.on_item_started("item-1")
    _queue(ledger, 5)

    ledger.on_item_started("item-2")
    assert ledger.item_start_offset == 5 * CHUNK
    assert ledger.played_ms_for_current_item() == 0

    ledger.on_chunk_sent("n0", CHUNK)
    ledger.on_mark_ack("n0")
    # Six chunks played in total, but only one belongs to item-2.
    assert ledger.played_bytes == 6 * CHUNK
    assert ledger.played_ms_for_current_item() == 20


def test_snapshot_agrees_with_the_live_reads() -> None:
    ledger = PlaybackLedger()
    ledger.on_item_started("item-1")
    names = _queue(ledger, 4)
    ledger.on_mark_ack(names[1])

    snap = ledger.snapshot()
    assert snap.played_bytes == ledger.played_bytes
    assert snap.queued_bytes == ledger.queued_bytes
    assert snap.outstanding_marks == ledger.outstanding_marks
    assert snap.current_item_id == "item-1"
    assert snap.played_ms_for_current_item == ledger.played_ms_for_current_item()


def test_unknown_mark_is_ignored_rather_than_raising() -> None:
    ledger = PlaybackLedger()
    assert ledger.on_mark_ack("never-sent") is False
    assert ledger.played_bytes == 0
