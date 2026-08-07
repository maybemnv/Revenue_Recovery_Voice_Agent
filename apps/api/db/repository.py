"""Persistence helpers shared by the media plane, the workers, and the routers.

Two rules encoded here rather than at each call site:

* every caller-authored string passes through `redact_pan` before it is written,
  so PAN data cannot reach the transcript even if the model repeats it back —
  including model-authored tool arguments, which carry caller text verbatim and
  are served back out by the dashboard;
* `upsert_contact` leans on the `contacts` UNIQUE constraint via `ON CONFLICT`
  rather than SELECT-then-INSERT, because the concurrent-insert race is real.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models import (
    Call,
    CallEvent,
    Contact,
    KBChunk,
    SmsSend,
    ToolInvocation,
    Turn,
)
from apps.api.security.redaction import redact_pan, redact_structure


async def create_call(
    session: AsyncSession,
    *,
    call_id: uuid.UUID,
    client_id: str,
    twilio_call_sid: str,
    from_e164: str,
    direction: str = "inbound",
    consent_captured: bool = False,
    started_at: datetime | None = None,
) -> Call:
    call = Call(
        id=call_id,
        client_id=client_id,
        twilio_call_sid=twilio_call_sid,
        from_e164=from_e164,
        direction=direction,
        consent_captured=consent_captured,
        started_at=started_at or datetime.now(UTC),
    )
    session.add(call)
    await session.flush()
    return call


async def get_call(session: AsyncSession, call_id: uuid.UUID) -> Call | None:
    return await session.get(Call, call_id)


async def get_call_by_sid(session: AsyncSession, sid: str) -> Call | None:
    result = await session.execute(select(Call).where(Call.twilio_call_sid == sid))
    return result.scalar_one_or_none()


async def finish_call(
    session: AsyncSession,
    call_id: uuid.UUID,
    *,
    outcome: str | None = None,
    cost_cents: int | None = None,
    ended_at: datetime | None = None,
) -> None:
    values: dict[str, Any] = {"ended_at": ended_at or datetime.now(UTC)}
    if outcome is not None:
        values["outcome"] = outcome
    if cost_cents is not None:
        values["cost_cents"] = cost_cents
    await session.execute(update(Call).where(Call.id == call_id).values(**values))


async def set_recording_url(session: AsyncSession, call_id: uuid.UUID, url: str) -> bool:
    """Recording storage is gated on consent; without it the URL is dropped.

    Returns whether the write happened, so the caller can log the refusal.
    """
    call = await session.get(Call, call_id)
    if call is None or not call.consent_captured:
        return False
    call.recording_url = url
    return True


async def insert_turn(
    session: AsyncSession,
    *,
    call_id: uuid.UUID,
    role: str,
    text: str,
    started_at_ms: int,
    latency_ms: int | None = None,
    truncated_at_ms: int | None = None,
    realtime_item_id: str | None = None,
) -> Turn:
    turn = Turn(
        call_id=call_id,
        role=role,
        text_=redact_pan(text),
        started_at_ms=started_at_ms,
        latency_ms=latency_ms,
        truncated_at_ms=truncated_at_ms,
        realtime_item_id=realtime_item_id,
    )
    session.add(turn)
    await session.flush()
    return turn


async def set_turn_truncation(
    session: AsyncSession, *, call_id: uuid.UUID, realtime_item_id: str, truncated_at_ms: int
) -> None:
    await session.execute(
        update(Turn)
        .where(Turn.call_id == call_id, Turn.realtime_item_id == realtime_item_id)
        .values(truncated_at_ms=truncated_at_ms)
    )


async def insert_tool_invocation(
    session: AsyncSession,
    *,
    call_id: uuid.UUID,
    name: str,
    arguments: dict[str, Any],
    result_status: str,
    latency_ms: int,
    attempt: int = 1,
) -> None:
    session.add(
        ToolInvocation(
            call_id=call_id,
            name=name,
            arguments=redact_structure(arguments),
            result_status=result_status,
            latency_ms=latency_ms,
            attempt=attempt,
        )
    )
    await session.flush()


async def insert_call_event(
    session: AsyncSession,
    *,
    call_id: uuid.UUID,
    at_ms: int,
    kind: str,
    payload: dict[str, Any],
) -> None:
    session.add(CallEvent(call_id=call_id, at_ms=at_ms, kind=kind, payload=payload))
    await session.flush()


async def upsert_contact(
    session: AsyncSession,
    *,
    client_id: str,
    phone_e164: str,
    full_name: str | None = None,
    crm_id: str | None = None,
) -> Contact:
    """Insert-or-update on the UNIQUE constraint.

    `ON CONFLICT DO UPDATE` is what makes two workers racing on the same number
    converge on one row. COALESCE keeps a previously-known name or CRM id when
    this call did not learn one.
    """
    insert_stmt = pg_insert(Contact).values(
        id=uuid.uuid4(),
        client_id=client_id,
        phone_e164=phone_e164,
        full_name=full_name,
        crm_id=crm_id,
    )
    # COALESCE(new, existing): this call's value wins when it has one, otherwise
    # a previously-learned name or CRM id survives.
    stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_contacts_client_phone",
        set_={
            "full_name": func.coalesce(insert_stmt.excluded.full_name, Contact.full_name),
            "crm_id": func.coalesce(insert_stmt.excluded.crm_id, Contact.crm_id),
        },
    ).returning(Contact)
    result = await session.execute(stmt)
    return result.scalar_one()


async def mark_opted_out(
    session: AsyncSession, *, client_id: str, phone_e164: str, at: datetime | None = None
) -> None:
    stmt = (
        pg_insert(Contact)
        .values(
            id=uuid.uuid4(),
            client_id=client_id,
            phone_e164=phone_e164,
            opted_out_at=at or datetime.now(UTC),
        )
        .on_conflict_do_update(
            constraint="uq_contacts_client_phone",
            set_={"opted_out_at": at or datetime.now(UTC)},
        )
    )
    await session.execute(stmt)


async def claim_sms_send(
    session: AsyncSession, *, client_id: str, to_e164: str, dedupe_key: str
) -> int | None:
    """Reserve the right to send one message. `None` means someone already has it.

    `ON CONFLICT DO NOTHING ... RETURNING id` is the whole mechanism: the winner
    gets an id, every replay gets `None`, and the race between two workers is
    settled by the UNIQUE constraint rather than by a SELECT that can be stale by
    the time it returns.
    """
    stmt = (
        pg_insert(SmsSend)
        .values(client_id=client_id, to_e164=to_e164, dedupe_key=dedupe_key)
        .on_conflict_do_nothing(constraint="uq_sms_sends_client_key")
        .returning(SmsSend.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def mark_sms_delivered(
    session: AsyncSession, *, send_id: int, provider_sid: str | None
) -> None:
    """Record that the provider accepted it. The claim stands either way.

    A failed send deliberately leaves the row behind rather than releasing it: a
    Twilio retry storm resending a failed message is the more expensive mistake,
    and the row is the audit trail for why a caller never got a text.
    """
    await session.execute(
        update(SmsSend)
        .where(SmsSend.id == send_id)
        .values(delivered=True, provider_sid=provider_sid)
    )


async def is_suppressed(session: AsyncSession, *, client_id: str, phone_e164: str) -> bool:
    """Checked before every outbound SMS. An unknown number is not suppressed."""
    result = await session.execute(
        select(Contact.opted_out_at).where(
            Contact.client_id == client_id, Contact.phone_e164 == phone_e164
        )
    )
    row = result.scalar_one_or_none()
    return row is not None


async def insert_kb_chunks(
    session: AsyncSession,
    *,
    client_id: str,
    chunks: list[tuple[str, str, list[float]]],
) -> int:
    """chunks is a list of (source, content, embedding)."""
    session.add_all(
        [
            KBChunk(client_id=client_id, source=source, content=content, embedding=embedding)
            for source, content, embedding in chunks
        ]
    )
    await session.flush()
    return len(chunks)
