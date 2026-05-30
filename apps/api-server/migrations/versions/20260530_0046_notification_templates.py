"""notification_templates (tenant override) + RLS (Plan 10 task_10_03).

Adds the tenant-override layer of the notification template system whose
ORM lives in ``api_server.db.notification`` (:class:`NotificationTemplate`).

Tenancy decision (encoded both here and in the model docstring):

  - **``notification_templates``** — tenant-owned. ``tenant_id NOT NULL``
    + a FOR ALL tenant-isolation RLS policy, exactly like
    ``marketplace_installations`` (migration 0041). This is the deliberate
    counterpart to the channel/preference HYBRID model: the *platform*
    layer of the three-layer model is NOT a NULL-tenant row here — it is
    the set of builtin templates shipped in code
    (``notification_dispatcher.templates``). A row in this table is always
    a tenant override of a builtin, so there is no NULL-tenant / platform
    branch and no ``scope`` discriminator. A tenant can never see or
    override another tenant's template; the BYPASSRLS dispatcher additionally
    resolves the override under the request's ``app.tenant_id`` and the
    builtin is the fallback when no live override exists.

The template sources are plain Jinja2 text (NOT secrets), so they are
stored in the clear — unlike channel secrets, which are Vault-or-Fernet at
rest. The Jinja2 environment that renders them is SANDBOXED
(``jinja2.sandbox``) so a tenant override can never execute arbitrary code.

The RLS DDL copies the canonical NULLIF + cast shape from migration 0001 /
0041 / 0045 so unset sessions see zero rows (safe default). Fully
reversible: ``downgrade`` drops the policy then the table.

Revision ID: 0046_notification_templates
Revises: 0045_notifications
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046_notification_templates"
down_revision: str | Sequence[str] | None = "0045_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# RLS DDL — one statement at a time (asyncpg refuses multi-statement
# strings). The NULLIF(..., '') guard turns the empty string returned by
# current_setting(..., true) on an unset GUC into NULL before the ::uuid
# cast, so an unset session deterministically matches zero rows (copied
# verbatim from 0041 / 0045 so the tenant-isolation semantics match).
# ---------------------------------------------------------------------------
_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE notification_templates ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE notification_templates FORCE ROW LEVEL SECURITY",
    "CREATE POLICY notification_templates_tenant_isolation ON notification_templates FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS notification_templates_tenant_isolation ON notification_templates",
    "ALTER TABLE notification_templates DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "notification_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("channel_type", sa.String(length=16), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column("subject_template", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_notification_templates"),
        sa.UniqueConstraint(
            "tenant_id",
            "event_type",
            "channel_type",
            "locale",
            name="uq_notification_templates_key",
        ),
        # Locale is closed to ES + EN (CLAUDE.md §12).
        sa.CheckConstraint(
            "locale IN ('es', 'en')",
            name="ck_notification_templates_locale",
        ),
    )
    op.create_index(
        "ix_notification_templates_tenant_id",
        "notification_templates",
        ["tenant_id"],
    )
    op.create_index(
        "ix_notification_templates_tenant_lookup",
        "notification_templates",
        ["tenant_id", "event_type", "channel_type", "locale"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _RLS_DOWN:
        op.execute(stmt)
    op.drop_table("notification_templates")
