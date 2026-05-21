"""approval_policy_templates table (task_01_14 substrate).

Per spec §7.7-7.8: human approval is configurable per project across
13 categories of sensitive actions. This table holds named templates
that tenants pick from when creating a project. The chosen template's
JSON gets copied into `projects.human_approval_policy` (we don't keep
a foreign key -- the project owns its policy from that point on).

Same RLS pattern as the other catalogs: tenant_scoped + a SELECT-only
`approval_policy_templates_builtin_read` policy for is_builtin rows.

Revision ID: 0008_approval_policy_templates
Revises: 0007_project_templates
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_approval_policy_templates"
down_revision: str | Sequence[str] | None = "0007_project_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_policy_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # JSONB shape: {"categories": {"<category>": "auto" | "human_required"}, ...}
        sa.Column(
            "categories",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
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
        sa.Column(
            "deleted_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_approval_policy_templates_tenant_id",
        "approval_policy_templates",
        ["tenant_id"],
    )
    op.create_index(
        "ix_approval_policy_templates_is_builtin",
        "approval_policy_templates",
        ["is_builtin"],
        postgresql_where=sa.text("is_builtin = true"),
    )

    # RLS: same shape as the other catalog tables.
    op.execute("ALTER TABLE approval_policy_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE approval_policy_templates FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY approval_policy_templates_tenant_isolation"
        " ON approval_policy_templates FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    op.execute(
        "CREATE POLICY approval_policy_templates_builtin_read"
        " ON approval_policy_templates FOR SELECT"
        " USING (is_builtin = true)"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS approval_policy_templates_builtin_read"
        " ON approval_policy_templates"
    )
    op.execute(
        "DROP POLICY IF EXISTS approval_policy_templates_tenant_isolation"
        " ON approval_policy_templates"
    )
    op.execute("ALTER TABLE approval_policy_templates DISABLE ROW LEVEL SECURITY")
    op.drop_table("approval_policy_templates")
