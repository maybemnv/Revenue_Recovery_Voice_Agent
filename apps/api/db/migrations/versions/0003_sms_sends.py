"""sms_sends: one row per message, UNIQUE on the dedupe key

Twilio retries a status callback on any non-2xx and on its own timeouts, so the
missed-call text has an at-least-once trigger in front of it. The UNIQUE
constraint is what makes the send at-most-once; `ON CONFLICT DO NOTHING
RETURNING id` settles the two-worker race that a SELECT-then-INSERT loses.

Revision ID: 0003_sms_sends
Revises: 0002_initial_schema
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_sms_sends"
down_revision: str | None = "0002_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sms_sends",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("to_e164", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("provider_sid", sa.Text(), nullable=True),
        sa.Column("delivered", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        # The claim. A replay loses it and sends nothing.
        sa.UniqueConstraint("client_id", "dedupe_key", name="uq_sms_sends_client_key"),
    )


def downgrade() -> None:
    op.drop_table("sms_sends")
