"""enable pgvector extension

Runs on its own, ahead of every table migration, because `kb_chunks.embedding`
cannot be created until the type exists.

Revision ID: 0001_pgvector
Revises:
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_pgvector"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Deliberately not dropped: another schema in the same database may use it.
    pass
