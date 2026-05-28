"""tenant_settings table (Plan 06.7 task_06_7_01).

Generic per-tenant key/value config table with category dimension.
Replaces the pattern of "one column on organizations per feature"
(which doesn't scale — every plan adds new settings).

Layout::

    PRIMARY KEY (tenant_id, category, key)

The registry of known (category, key) pairs lives in code
(``api_server.settings_registry``); the DB stores only values
configured by users. Reads fall back to the registry's default
when the row is missing — so the tenant never has to touch a setting
for the system to work.

Revision ID: 0023_tenant_settings
Revises: 0022_knowledge_bases
Create Date: 2026-05-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_tenant_settings"
down_revision: str | Sequence[str] | None = "0022_knowledge_bases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_settings",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_tenant_settings_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name="fk_tenant_settings_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "category", "key", name="pk_tenant_settings"),
    )
    # RLS: same isolation guarantee as the rest of the tenant-scoped
    # tables — the app role can only read/write rows whose tenant_id
    # matches `current_setting('app.tenant_id')`.
    op.execute("ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY tenant_isolation ON tenant_settings "
        "USING (tenant_id::text = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON tenant_settings;")
    op.drop_table("tenant_settings")
