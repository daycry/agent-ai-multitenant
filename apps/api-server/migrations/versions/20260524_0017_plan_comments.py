"""plan_comments table — inline plan comments (Plan 03 task_03_21).

Per-plan comments scoped to the whole plan, a phase, or a task.
RLS-isolated like the rest of the domain; soft-deletable so retracting
a comment leaves an audit trail.

Revision ID: 0017_plan_comments
Revises: 0016_plan_spec_status_widen
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_plan_comments"
down_revision: str | Sequence[str] | None = "0016_plan_spec_status_widen"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plan_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_kind", sa.String(length=16), nullable=False),
        sa.Column("target_ref", sa.String(length=120), nullable=True),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("content", sa.Text(), nullable=False),
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
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(target_kind = 'plan' AND (target_ref IS NULL OR target_ref = ''))"
            " OR (target_kind <> 'plan' AND target_ref IS NOT NULL AND length(target_ref) > 0)",
            name="ck_plan_comments_target_ref_consistency",
        ),
    )
    op.create_index("ix_plan_comments_tenant_id", "plan_comments", ["tenant_id"])
    op.create_index(
        "ix_plan_comments_plan_id",
        "plan_comments",
        ["plan_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.execute("ALTER TABLE plan_comments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE plan_comments FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY plan_comments_tenant_isolation"
        " ON plan_comments FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS plan_comments_tenant_isolation ON plan_comments")
    op.execute("ALTER TABLE plan_comments DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_plan_comments_plan_id", table_name="plan_comments")
    op.drop_index("ix_plan_comments_tenant_id", table_name="plan_comments")
    op.drop_table("plan_comments")
