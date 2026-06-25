"""ingestion enqueue lease: documents.enqueued_at

prod-06 task_prod06_beat_02 (workers-11): the beat sweep re-enqueued every
``pending`` document older than the age cutoff, so a >5-min backlog on the
``ingestion`` queue caused the sweep to re-enqueue documents that were still
legitimately queued (duplicate work). ``enqueued_at`` is the lease marker the
sweep now claims atomically: it re-enqueues only documents whose lease is NULL
(the enqueue never landed) or has expired. Nullable + additive — existing rows
keep working with NULL (treated as "never enqueued"). A partial index keeps the
sweep's claim query cheap as the pending set grows. Reversible.

Revision ID: 0097_document_enqueued_at
Revises: 0096_marketplace_dedup_nulls
Create Date: 2026-06-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0097_document_enqueued_at"
down_revision: str | Sequence[str] | None = "0096_marketplace_dedup_nulls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("enqueued_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # The sweep's claim query filters status='pending'; a partial index keeps it
    # cheap regardless of how many indexed/failed rows accumulate.
    op.create_index(
        "ix_documents_pending_enqueued_at",
        "documents",
        ["enqueued_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_documents_pending_enqueued_at", table_name="documents")
    op.drop_column("documents", "enqueued_at")
