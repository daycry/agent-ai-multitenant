"""task_audit_events table (Plan 06.5 task_06_5_02).

Append-only audit log per task. Source of truth for the task history
shown in the admin-panel (Plan 06 task_06_38) and consumed by the
upcoming endpoint `GET /api/v1/tasks/{task_id}/history`.

The Python model already exists as
``api_server.task_lifecycle.AuditEvent``::

    @dataclass(frozen=True)
    class AuditEvent:
        task_id: str
        at: float                  # unix timestamp
        kind: str                  # "transition" / "review_comment" / ...
        actor: str
        payload: Mapping[str, Any]

This migration just persists it. Append-only is enforced by code (no
UPDATE / DELETE in the repository — tests pin that). The DB itself
has no trigger blocking modifications; that would conflict with the
soft-delete pattern used elsewhere. If we need stronger guarantees
later we'll either add a trigger or move to a write-once log table.

Revision ID: 0025_task_audit_events
Revises: 0024_review_sessions
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_task_audit_events"
down_revision: str | Sequence[str] | None = "0024_review_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_audit_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Free-form kind tag — extended by adding members; never rename
        # existing values (historical rows reference them). Known values
        # today: 'transition', 'review_comment', 'human_action',
        # 'creation', 'free_task_created'.
        sa.Column("kind", sa.String(length=32), nullable=False),
        # The actor is a free-form string instead of an FK to users
        # because some events are written by agents / system services
        # (e.g. "agent:reviewer", "system:plan_runner") that don't
        # exist as user rows. Audit traceability is by *string*, not
        # by joinable identity.
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_task_audit_events_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name="fk_task_audit_events_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_audit_events"),
    )

    # Hot path: chronological history of one task. The endpoint
    # `GET /api/v1/tasks/{id}/history` runs `WHERE task_id = ? ORDER BY at`
    # — a single covering index over (task_id, at) is exactly what we
    # need.
    op.create_index(
        "ix_task_audit_events_task_at",
        "task_audit_events",
        ["task_id", "at"],
    )
    # Tenant queries (rare, but used by the cross-task audit views).
    op.create_index(
        "ix_task_audit_events_tenant_at",
        "task_audit_events",
        ["tenant_id", "at"],
    )

    # RLS — same pattern as the rest of the tenant-scoped tables.
    op.execute("ALTER TABLE task_audit_events ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY tenant_isolation ON task_audit_events "
        "USING (tenant_id::text = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON task_audit_events;")
    op.drop_index("ix_task_audit_events_tenant_at", table_name="task_audit_events")
    op.drop_index("ix_task_audit_events_task_at", table_name="task_audit_events")
    op.drop_table("task_audit_events")
