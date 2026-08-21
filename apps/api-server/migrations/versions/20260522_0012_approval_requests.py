"""approval_requests table — human_approval_policy decisions (task_02_24).

Per spec §7.7: when an agent attempts an action whose category the
project's human_approval_policy marks `human_required`, the approval
engine parks the execution and persists a `pending` row here. A reviewer
approves or rejects it (task_02_26); an unanswered one times out
(task_02_27).

Same tenant-isolation RLS as the rest of the domain; the migration is
reversible.

Revision ID: 0012_approval_requests
Revises: 0011_platform_settings
Create Date: 2026-05-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_approval_requests"
down_revision: str | Sequence[str] | None = "0011_platform_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column(
            "action", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "requested_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "resolved_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
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
    )
    op.create_index("ix_approval_requests_tenant_id", "approval_requests", ["tenant_id"])
    op.create_index(
        "ix_approval_requests_tenant_status", "approval_requests", ["tenant_id", "status"]
    )
    op.create_index("ix_approval_requests_execution_id", "approval_requests", ["execution_id"])

    op.execute("ALTER TABLE approval_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE approval_requests FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY approval_requests_tenant_isolation"
        " ON approval_requests FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )

    # Fase F adds the execution status 'awaiting_human_approval' (23 chars);
    # widen executions.status (was VARCHAR(16) in migration 0010) to hold it.
    op.alter_column(
        "executions",
        "status",
        type_=sa.String(length=32),
        existing_type=sa.String(length=16),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Shrink back, truncating any value that no longer fits — keeps the
    # downgrade structurally reversible even with awaiting_human_approval rows.
    op.execute(
        "ALTER TABLE executions ALTER COLUMN status TYPE VARCHAR(16) USING substr(status, 1, 16)"
    )
    op.execute("DROP POLICY IF EXISTS approval_requests_tenant_isolation ON approval_requests")
    op.execute("ALTER TABLE approval_requests DISABLE ROW LEVEL SECURITY")
    op.drop_table("approval_requests")
