"""initial schema: the seven tables

Revision ID: 0002_initial_schema
Revises: 0001_pgvector
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0002_initial_schema"
down_revision: str | None = "0001_pgvector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("twilio_call_sid", sa.Text(), nullable=False),
        sa.Column("from_e164", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("cost_cents", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("recording_url", sa.Text(), nullable=True),
        sa.Column(
            "consent_captured", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.CheckConstraint("direction IN ('inbound','outbound')", name="ck_calls_direction"),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN "
            "('booked','qualified','escalated','abandoned','out_of_area','failed')",
            name="ck_calls_outcome",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("twilio_call_sid"),
    )
    op.create_index(
        "ix_calls_client_started", "calls", ["client_id", sa.text("started_at DESC")], unique=False
    )

    op.create_table(
        "turns",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("started_at_ms", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("truncated_at_ms", sa.Integer(), nullable=True),
        sa.Column("realtime_item_id", sa.Text(), nullable=True),
        sa.CheckConstraint("role IN ('caller','agent')", name="ck_turns_role"),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_turns_call_started", "turns", ["call_id", "started_at_ms"], unique=False)

    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_status", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_invocations_call", "tool_invocations", ["call_id"], unique=False)

    op.create_table(
        "call_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("at_ms", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_call_events_call_at", "call_events", ["call_id", "at_ms"], unique=False)

    op.create_table(
        "contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("phone_e164", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("crm_id", sa.Text(), nullable=True),
        sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # The dedupe race is lost by SELECT-then-INSERT; this is the fix.
        sa.UniqueConstraint("client_id", "phone_e164", name="uq_contacts_client_phone"),
    )

    op.create_table(
        "call_analyses",
        sa.Column("call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("sentiment", sa.Text(), nullable=False),
        sa.Column("qa_score", sa.SmallInteger(), nullable=False),
        sa.Column(
            "action_items",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("model", sa.Text(), nullable=False),
        sa.CheckConstraint("qa_score BETWEEN 0 AND 100", name="ck_call_analyses_qa_score"),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("call_id"),
    )

    op.create_table(
        "kb_chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=512), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kb_chunks_client", "kb_chunks", ["client_id"], unique=False)
    # HNSW over cosine distance: `lookup_knowledge` orders by `embedding <=> query`.
    op.execute(
        "CREATE INDEX ix_kb_chunks_embedding_hnsw ON kb_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_kb_chunks_embedding_hnsw", table_name="kb_chunks")
    op.drop_index("ix_kb_chunks_client", table_name="kb_chunks")
    op.drop_table("kb_chunks")
    op.drop_table("call_analyses")
    op.drop_table("contacts")
    op.drop_index("ix_call_events_call_at", table_name="call_events")
    op.drop_table("call_events")
    op.drop_index("ix_tool_invocations_call", table_name="tool_invocations")
    op.drop_table("tool_invocations")
    op.drop_index("ix_turns_call_started", table_name="turns")
    op.drop_table("turns")
    op.drop_index("ix_calls_client_started", table_name="calls")
    op.drop_table("calls")
