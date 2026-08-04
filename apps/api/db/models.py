"""SQLAlchemy models for the seven tables in `docs/PRD.md:478-549`.

Invariants that protect correctness live here as *constraints*, not as
application logic. `contacts UNIQUE (client_id, phone_e164)` is the load-bearing
one: two calls from the same number landing on two workers concurrently is a
real race that a SELECT-then-INSERT loses.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIMENSIONS = 1536  # text-embedding-3-small

# `failed` is a first-class outcome, not an absence of one. A call the media
# plane crashed on must still be visible in the dashboard and countable in the
# eval report; folding it into `abandoned` would hide our own errors inside a
# caller-behaviour bucket.
CALL_OUTCOMES = ("booked", "qualified", "escalated", "abandoned", "out_of_area", "failed")
EVENT_KINDS = (
    "barge_in",
    "tool_call",
    "escalation",
    "budget_warning",
    "transfer",
    "consent",
    "session",
)


class Base(DeclarativeBase):
    pass


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    twilio_call_sid: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    from_e164: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False, default="inbound")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(Text)
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    recording_url: Mapped[str | None] = mapped_column(Text)
    consent_captured: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    turns: Mapped[list[Turn]] = relationship(
        back_populates="call", cascade="all, delete-orphan", order_by="Turn.started_at_ms"
    )
    events: Mapped[list[CallEvent]] = relationship(
        back_populates="call", cascade="all, delete-orphan", order_by="CallEvent.at_ms"
    )
    tool_invocations: Mapped[list[ToolInvocation]] = relationship(
        back_populates="call", cascade="all, delete-orphan"
    )
    analysis: Mapped[CallAnalysis | None] = relationship(
        back_populates="call", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        CheckConstraint("direction IN ('inbound','outbound')", name="ck_calls_direction"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN "
            "('booked','qualified','escalated','abandoned','out_of_area','failed')",
            name="ck_calls_outcome",
        ),
        # The dashboard's call list is exactly this query.
        Index("ix_calls_client_started", "client_id", text("started_at DESC")),
    )


class Turn(Base):
    __tablename__ = "turns"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    text_: Mapped[str] = mapped_column("text", Text, nullable=False)
    started_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    truncated_at_ms: Mapped[int | None] = mapped_column(Integer)
    realtime_item_id: Mapped[str | None] = mapped_column(Text)

    call: Mapped[Call] = relationship(back_populates="turns")

    __table_args__ = (
        CheckConstraint("role IN ('caller','agent')", name="ck_turns_role"),
        Index("ix_turns_call_started", "call_id", "started_at_ms"),
    )


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_status: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))

    call: Mapped[Call] = relationship(back_populates="tool_invocations")

    __table_args__ = (Index("ix_tool_invocations_call", "call_id"),)


class CallEvent(Base):
    """Append-only. Drives the escalation timeline and the barge-in markers."""

    __tablename__ = "call_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    call: Mapped[Call] = relationship(back_populates="events")

    __table_args__ = (Index("ix_call_events_call_at", "call_id", "at_ms"),)


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    phone_e164: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    crm_id: Mapped[str | None] = mapped_column(Text)
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Dedupe is a constraint, not a code path.
    __table_args__ = (UniqueConstraint("client_id", "phone_e164", name="uq_contacts_client_phone"),)


class CallAnalysis(Base):
    __tablename__ = "call_analyses"

    call_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), primary_key=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str] = mapped_column(Text, nullable=False)
    sentiment: Mapped[str] = mapped_column(Text, nullable=False)
    qa_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    action_items: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)

    call: Mapped[Call] = relationship(back_populates="analysis")

    __table_args__ = (
        CheckConstraint("qa_score BETWEEN 0 AND 100", name="ck_call_analyses_qa_score"),
    )


class KBChunk(Base):
    __tablename__ = "kb_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String(512))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)

    __table_args__ = (Index("ix_kb_chunks_client", "client_id"),)
