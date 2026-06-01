"""memory_entries.source_human_work_session_id (Plan 16 Fase E, task_16_15).

The Memorizer (Plan 04 task_04_03) distilled finished ``Execution`` rows into
``MemoryEntry`` rows, back-linked by ``source_execution_id``. Plan 16 adds human
tasks: a ``agent_type='human'`` task records its work in
``human_work_sessions`` (task_16_03), NOT in ``executions``. So the Memorizer
now also distils HumanWorkSessions into MemoryEntries (e.g. "user X made
decision D in context C and it led to outcome O").

For the citation to point at the RIGHT source we add a SECOND, sibling
back-link column:

  * ``source_human_work_session_id`` UUID NULL — FK to
    ``human_work_sessions.id`` ``ON DELETE SET NULL`` (mirrors
    ``source_execution_id``'s ``ON DELETE SET NULL``: dropping the source must
    not erase the distilled memory, only its citation). NULL for every existing
    row and for AI-distilled / human-curated memories; set only when the
    Memorizer distilled a human work session.

A memory has at most one source: an Execution OR a HumanWorkSession (or
neither, for human-curated ``POST /memories`` entries). A CHECK constraint
forbids setting both at once so the citation is always unambiguous.

The column inherits the existing ``memory_entries`` tenant RLS (no policy
change). Additive + backward-compatible: existing rows get NULL.

Single head before this migration is ``0074_project_budget_human_cost``; this
is ``0075_memory_source_human_ws`` (kept <= 32 chars to fit
``alembic_version.version_num``). Fully reversible: ``downgrade`` drops the
CHECK + the FK column, restoring 0074 exactly.

Revision ID: 0075_memory_source_human_ws
Revises: 0074_project_budget_human_cost
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0075_memory_source_human_ws"
down_revision: str | Sequence[str] | None = "0074_project_budget_human_cost"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Sibling of source_execution_id. SET NULL so dropping the work session
    # keeps the distilled memory (only the citation is lost). Nullable + no
    # server_default so every existing row stays NULL (AI / human-curated).
    op.add_column(
        "memory_entries",
        sa.Column(
            "source_human_work_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("human_work_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # A memory cites at most ONE source — an Execution XOR a HumanWorkSession
    # (or neither). Forbid both being set so the citation is unambiguous.
    op.create_check_constraint(
        "ck_memory_entries_single_source",
        "memory_entries",
        "source_execution_id IS NULL OR source_human_work_session_id IS NULL",
    )
    # Read path: "the memories distilled from this work session" (audit / detail
    # view). Partial so it stays tiny — only set on human-distilled rows.
    op.create_index(
        "ix_memory_entries_source_hws",
        "memory_entries",
        ["source_human_work_session_id"],
        postgresql_where=sa.text("source_human_work_session_id IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_memory_entries_source_hws", table_name="memory_entries")
    op.drop_constraint("ck_memory_entries_single_source", "memory_entries", type_="check")
    op.drop_column("memory_entries", "source_human_work_session_id")
