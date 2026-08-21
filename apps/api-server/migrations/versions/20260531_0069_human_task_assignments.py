"""human_task_assignments table + RLS (Plan 16 Fase B, task_16_05).

Who a human task is currently with + where that person is in the accept cycle.
When the orchestrator routes a ``ready`` task whose assignee Agent is
``agent_type='human'`` it does NOT request a runtime container from the pool
(the AI path). Instead it creates one row here — the human Agent
(``human_agent_id``), the concrete User the work landed on
(``assigned_to_user_id``, resolved from ``human_agent_config.assigned_user_id``)
and when (``assigned_at``) — and transitions the Task to ``assigned_to_human``
via the §7.2 state machine (task_16_04). The acceptance-timeout job
(task_16_06) reads the ``pending_acceptance`` rows and, on expiry, creates a
fresh assignment for the ``escalation_target_user_id``.

Columns (from the task block):

  - ``task_id``             FK tasks (CASCADE) — the human task assigned.
  - ``human_agent_id``      FK agents (SET NULL) — the human Agent it is with.
  - ``assigned_to_user_id`` FK users (SET NULL) — the concrete User.
  - ``assigned_at``         TIMESTAMPTZ NOT NULL DEFAULT now() — the timeout job
                            ages off this column.
  - ``status``              TEXT NOT NULL DEFAULT 'pending_acceptance', CHECKed
                            against the HumanTaskAssignmentStatus value set
                            (pending_acceptance / accepted / reassigned /
                            declined / expired) — same CHECK shape as
                            ck_agents_agent_type.

Tenant-owned: ``tenant_id`` NOT NULL + RLS. The RLS DDL copies the EXACT shape
``human_work_sessions`` / ``agents`` use (migration 0068 / 0002): ``ENABLE`` +
``FORCE`` ROW LEVEL SECURITY, then a ``{table}_tenant_isolation`` FOR ALL policy
with ``tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`` —
the NULLIF guard turns the empty string an unset GUC returns into NULL before
the ``::uuid`` cast, so an unset session deterministically matches zero tenant
rows (safe default). An assignment names a tenant ``users`` row and a tenant
``agents`` row, so this table is never global.

Single head before this migration is ``0068_human_work_sessions``; this is
``0069_human_task_assignments``. Fully reversible: ``downgrade`` drops the
policy then the table, restoring 0068 exactly. The plan-wide reversibility
proof target is ``0040_sso_email_domains``.

Revision ID: 0069_human_task_assignments
Revises: 0068_human_work_sessions
Create Date: 2026-05-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0069_human_task_assignments"
down_revision: str | Sequence[str] | None = "0068_human_work_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# RLS DDL — sent one statement at a time (asyncpg refuses multi-statement
# strings). Mirrors the human_work_sessions tenant-isolation shape verbatim:
# ENABLE + FORCE + a FOR ALL USING policy with the NULLIF + ::uuid cast.
_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE human_task_assignments ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE human_task_assignments FORCE ROW LEVEL SECURITY",
    "CREATE POLICY human_task_assignments_tenant_isolation ON human_task_assignments FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS human_task_assignments_tenant_isolation ON human_task_assignments",
    "ALTER TABLE human_task_assignments DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "human_task_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("human_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_to_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "assigned_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending_acceptance'"),
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name="fk_human_task_assignments_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["human_agent_id"],
            ["agents.id"],
            name="fk_human_task_assignments_agent",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to_user_id"],
            ["users.id"],
            name="fk_human_task_assignments_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_human_task_assignments"),
        # The DB enforces the HumanTaskAssignmentStatus value set.
        sa.CheckConstraint(
            "status IN ('pending_acceptance', 'accepted', 'reassigned', 'declined', 'expired')",
            name="ck_human_task_assignments_status",
        ),
    )
    op.create_index(
        "ix_human_task_assignments_tenant_id",
        "human_task_assignments",
        ["tenant_id"],
    )
    op.create_index(
        "ix_human_task_assignments_task_id",
        "human_task_assignments",
        ["task_id"],
    )
    op.create_index(
        "ix_human_task_assignments_assigned_user",
        "human_task_assignments",
        ["assigned_to_user_id"],
    )
    # The acceptance-timeout sweep (task_16_06) scans the open
    # pending_acceptance rows by age — a partial index keeps it cheap.
    op.create_index(
        "ix_human_task_assignments_pending",
        "human_task_assignments",
        ["assigned_at"],
        postgresql_where=sa.text("status = 'pending_acceptance'"),
    )

    # RLS — applied last so the table exists.
    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    # RLS first (the policy depends on the table).
    for stmt in _RLS_DOWN:
        op.execute(stmt)

    op.drop_index("ix_human_task_assignments_pending", table_name="human_task_assignments")
    op.drop_index("ix_human_task_assignments_assigned_user", table_name="human_task_assignments")
    op.drop_index("ix_human_task_assignments_task_id", table_name="human_task_assignments")
    op.drop_index("ix_human_task_assignments_tenant_id", table_name="human_task_assignments")
    op.drop_table("human_task_assignments")
