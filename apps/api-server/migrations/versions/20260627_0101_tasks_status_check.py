"""tasks.status CHECK — pin the column to the canonical TaskStatus enum (P2.4)

`tasks.status` is a free `String(32)` with no DB-level guard, so a bug could
persist a value the enum / state-machine has never heard of (e.g. the orphan
`awaiting_human` removed in F43) and the task would be invisible/frozen. This
adds a CHECK derived from :class:`api_server.db.domain.TaskStatus` so the DB
rejects any out-of-enum status. F43 guarantees no such rows exist going
forward; this migration assumes the table is already clean. Reversible.

Revision ID: 0101_tasks_status_check
Revises: 0100_execution_finish_status
Create Date: 2026-06-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0101_tasks_status_check"
down_revision: str | Sequence[str] | None = "0100_execution_finish_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_tasks_status_valid"

# Mirror of api_server.db.domain.TaskStatus — kept inline so the migration is
# pinned to the values as of this revision (a later enum change ships its own
# migration rather than silently widening this CHECK).
_VALID_STATUSES: tuple[str, ...] = (
    "backlog",
    "ready",
    "assigned_to_human",
    "in_progress",
    "awaiting_human_approval",
    "in_review",
    "blocked",
    "done",
    "cancelled",
)


def upgrade() -> None:
    # Data-migration first (F43): any legacy row carrying the orphan
    # `awaiting_human` (the value the old task_lifecycle Literal wrote, which was
    # never a real domain.TaskStatus) is re-homed to the canonical escalation
    # state `blocked` — consistent with reviewer_bridge / CLAUDE.md ppio 7 — so the
    # CHECK below cannot fail on a pre-existing DB. Idempotent: a clean table
    # updates 0 rows.
    op.execute("UPDATE tasks SET status = 'blocked' WHERE status = 'awaiting_human'")
    values = ", ".join(f"'{s}'" for s in _VALID_STATUSES)
    op.create_check_constraint(
        _CONSTRAINT,
        "tasks",
        f"status IN ({values})",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "tasks", type_="check")
