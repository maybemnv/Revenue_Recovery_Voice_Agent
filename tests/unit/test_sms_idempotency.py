"""Confirmation SMS must survive a replayed webhook without texting twice.

Twilio retries a status callback on any non-2xx *and* on its own timeouts, so
`/telephony/status` has an at-least-once trigger sitting in front of a
non-idempotent provider write. Two of those retries and the caller gets "we
missed your call" three times.

The guard is a claim row, not a code path: `claim_sms_send` does
`ON CONFLICT DO NOTHING ... RETURNING id`, so the winner gets an id and every
replay gets `None`. These tests drive `send_sms` against a fake session that
models exactly the one property the real constraint provides — a second insert
with the same `(client_id, dedupe_key)` returns nothing — plus a fake Twilio that
counts how many sends actually reached it.

The failure path deliberately keeps the claim. A retry storm that resends a
failed message later is the more expensive mistake, and the row is the audit
trail for why a caller never got a text.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from apps.api.telephony import sms


class FakeConstraint:
    """The `(client_id, dedupe_key)` UNIQUE index, and nothing else.

    Shared across sessions on purpose: the claim has to hold between two separate
    `session_scope()` blocks, which is what a replayed webhook actually looks
    like.
    """

    def __init__(self) -> None:
        self.claimed: set[tuple[str, str]] = set()
        self.delivered: dict[int, str | None] = {}
        self.suppressed: set[tuple[str, str]] = set()
        self._next_id = 0

    def claim(self, client_id: str, dedupe_key: str) -> int | None:
        key = (client_id, dedupe_key)
        if key in self.claimed:
            return None
        self.claimed.add(key)
        self._next_id += 1
        return self._next_id


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeConstraint:
    """Patch the three repository calls `send_sms` makes, keeping the constraint."""
    state = FakeConstraint()

    @asynccontextmanager
    async def fake_scope() -> Any:
        yield object()

    async def fake_is_suppressed(_session: Any, *, client_id: str, phone_e164: str) -> bool:
        return (client_id, phone_e164) in state.suppressed

    async def fake_claim(
        _session: Any, *, client_id: str, to_e164: str, dedupe_key: str
    ) -> int | None:
        return state.claim(client_id, dedupe_key)

    async def fake_mark(_session: Any, *, send_id: int, provider_sid: str | None) -> None:
        state.delivered[send_id] = provider_sid

    monkeypatch.setattr(sms, "session_scope", fake_scope)
    monkeypatch.setattr(sms, "is_suppressed", fake_is_suppressed)
    monkeypatch.setattr(sms, "claim_sms_send", fake_claim)
    monkeypatch.setattr(sms, "mark_sms_delivered", fake_mark)
    return state


class FakeTwilio:
    """Counts sends. `status` drives the provider's answer for every attempt."""

    def __init__(self, status: int = 201, sid: str = "SM123") -> None:
        self.status = status
        self.sid = sid
        self.sends = 0

    async def post(self, url: str, **_: Any) -> httpx.Response:
        self.sends += 1
        return httpx.Response(
            self.status,
            json={"sid": self.sid},
            request=httpx.Request("POST", url),
        )


@pytest.fixture(autouse=True)
def twilio_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """`send_sms` bails before the provider call when messaging is unconfigured."""
    settings = sms.get_settings()
    monkeypatch.setattr(settings, "twilio_account_sid", "ACtest", raising=False)
    monkeypatch.setattr(settings, "twilio_auth_token", "token", raising=False)
    monkeypatch.setattr(settings, "twilio_messaging_from", "+13125550000", raising=False)


MISSED = "missed_call:CA0000000000000000000000000000001"


async def test_a_replayed_webhook_sends_exactly_one_text(db: FakeConstraint) -> None:
    twilio = FakeTwilio()

    results = [
        await sms.send_sms(
            to="+13125551111",
            body="We missed your call.",
            client_id="acme",
            dedupe_key=MISSED,
            client=twilio,
        )
        for _ in range(3)
    ]

    assert twilio.sends == 1
    # Only the winner reports True: a replay did not send, so it did not deliver.
    assert results == [True, False, False]


async def test_the_winner_records_the_provider_sid(db: FakeConstraint) -> None:
    twilio = FakeTwilio(sid="SMabc")

    await sms.send_sms(
        to="+13125551111",
        body="hi",
        client_id="acme",
        dedupe_key=MISSED,
        client=twilio,
    )

    assert db.delivered == {1: "SMabc"}


async def test_a_different_call_is_a_different_key(db: FakeConstraint) -> None:
    """Dedupe is per-message, not a global mute on the number."""
    twilio = FakeTwilio()

    for sid in ("CA111", "CA222"):
        await sms.send_sms(
            to="+13125551111",
            body="We missed your call.",
            client_id="acme",
            dedupe_key=f"missed_call:{sid}",
            client=twilio,
        )

    assert twilio.sends == 2


async def test_two_clients_can_share_a_dedupe_key(db: FakeConstraint) -> None:
    """The constraint is scoped by client, so one tenant cannot mute another."""
    twilio = FakeTwilio()

    for client_id in ("acme", "globex"):
        await sms.send_sms(
            to="+13125551111",
            body="We missed your call.",
            client_id=client_id,
            dedupe_key=MISSED,
            client=twilio,
        )

    assert twilio.sends == 2


async def test_a_failed_send_keeps_its_claim(db: FakeConstraint) -> None:
    """Resending later is worse than not sending: a 400 is a verdict, not a blip."""
    twilio = FakeTwilio(status=400)

    first = await sms.send_sms(
        to="+13125551111", body="hi", client_id="acme", dedupe_key=MISSED, client=twilio
    )
    second = await sms.send_sms(
        to="+13125551111", body="hi", client_id="acme", dedupe_key=MISSED, client=twilio
    )

    assert (first, second) == (False, False)
    assert twilio.sends == 1
    assert db.delivered == {}


async def test_suppression_is_checked_before_the_claim(db: FakeConstraint) -> None:
    """An opted-out number must not burn its dedupe key, or a later opt-in is muted."""
    db.suppressed.add(("acme", "+13125551111"))
    twilio = FakeTwilio()

    sent = await sms.send_sms(
        to="+13125551111", body="hi", client_id="acme", dedupe_key=MISSED, client=twilio
    )

    assert sent is False
    assert twilio.sends == 0
    assert db.claimed == set()


async def test_no_key_means_no_claim_and_no_dedupe(db: FakeConstraint) -> None:
    """Unkeyed sends keep the old behaviour rather than silently deduping."""
    twilio = FakeTwilio()

    for _ in range(2):
        await sms.send_sms(to="+13125551111", body="hi", client_id="acme", client=twilio)

    assert twilio.sends == 2
    assert db.claimed == set()


async def test_an_ambiguous_failure_is_not_retried_into_a_duplicate(
    db: FakeConstraint,
) -> None:
    """`UNSAFE_WRITE` guards the transport; the claim guards the trigger."""

    class Timeout:
        def __init__(self) -> None:
            self.sends = 0

        async def post(self, url: str, **_: Any) -> httpx.Response:
            self.sends += 1
            raise httpx.ReadTimeout("lost", request=httpx.Request("POST", url))

    twilio = Timeout()

    sent = await sms.send_sms(
        to="+13125551111", body="hi", client_id="acme", dedupe_key=MISSED, client=twilio
    )

    assert sent is False
    # One attempt only: a read timeout may mean the text went out.
    assert twilio.sends == 1
