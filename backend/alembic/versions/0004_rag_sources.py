"""rag_sources: what has been indexed into Qdrant, for incremental re-indexing

Revision ID: 0004_rag_sources
Revises: 0003_lifecycle_history_backoff
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_rag_sources"
down_revision: str | None = "0003_lifecycle_history_backoff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rag_sources",
        sa.Column("source_key", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embed_model", sa.String(128), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("source_version", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("source_key", name=op.f("pk_rag_sources")),
    )


def downgrade() -> None:
    op.drop_table("rag_sources")
