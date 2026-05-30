"""notification_channels / preferences / logs + RLS (Plan 10 task_10_02).

Creates the three-table notification substrate whose ORM lives in
``api_server.db.notification`` (task_10_01) and wires the Row-Level
Security policies. The tenancy decisions are encoded both in the model
docstrings and here:

  - **``notification_channels``** — HYBRID, keyed on a ``scope``
    discriminator. ``tenant_id`` NULLABLE: ``scope='platform'`` rows are
    tenant-agnostic (NULL tenant — a System Admin ops channel, the same
    for everyone, like ``platform_settings``); ``scope='tenant'`` /
    ``scope='user'`` rows are owned by a tenant (NOT NULL). RLS mirrors the
    marketplace_listings hybrid pattern (migration 0041): a FOR ALL
    tenant-isolation policy for tenant/user rows + a SELECT-only policy
    exposing the NULL-tenant platform rows to every session. Writes to
    platform rows are reserved for BYPASSRLS roles (System Admin).

  - **``notification_preferences``** — HYBRID, same model as channels.
    Platform rows (NULL tenant) are the defaults; tenant/user rows
    override them (resolved most-specific-wins in task_10_04).

  - **``notification_logs``** — tenant-owned, append-only. ``tenant_id``
    NULLABLE (a platform-scoped send is still recorded — exactly like
    ``audit_log.tenant_id``). FOR ALL tenant-isolation policy + a
    SELECT-only policy exposing the NULL-tenant platform sends to every
    session (the operator inbox view).

Secret-at-rest invariant (CLAUDE.md: NO plaintext secrets): a channel's
secret lives in EXACTLY ONE never-plaintext form — a CHECK constraint
enforces "at most one of (secret_ref, secret_encrypted)". A second CHECK
enforces the scope↔tenant consistency: a platform channel/preference has
NULL tenant; a tenant/user one has NOT NULL tenant.

The RLS DDL copies the canonical NULLIF + cast shape from migration 0001 /
0041 so unset sessions see zero tenant rows (safe default). The BYPASSRLS
dispatcher (task_10_02) additionally validates ``row.tenant_id ==
request.tenant_id`` at the task boundary on top of RLS. Fully reversible:
``downgrade`` drops the policies then the tables in dependency order
(logs → preferences → channels).

Revision ID: 0045_notifications
Revises: 0044_marketplace_shares
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_notifications"
down_revision: str | Sequence[str] | None = "0044_marketplace_shares"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# RLS DDL — sent one statement at a time (asyncpg refuses multi-statement
# strings). The NULLIF(..., '') guard turns the empty string returned by
# current_setting(..., true) on an unset GUC into NULL before the ::uuid
# cast, so an unset session deterministically matches zero tenant rows
# (copied verbatim from 0041 so the tenant-isolation semantics match).
# ---------------------------------------------------------------------------
_RLS_UP: tuple[str, ...] = (
    # notification_channels — hybrid. tenant/user rows isolated by the FOR
    # ALL policy; platform (tenant_id IS NULL) rows exposed read-only to
    # every session. Platform-row writes are reserved for BYPASSRLS roles.
    "ALTER TABLE notification_channels ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE notification_channels FORCE ROW LEVEL SECURITY",
    "CREATE POLICY notification_channels_tenant_isolation ON notification_channels FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    "CREATE POLICY notification_channels_platform_read ON notification_channels FOR SELECT"
    " USING (tenant_id IS NULL)",
    # notification_preferences — hybrid, same shape.
    "ALTER TABLE notification_preferences ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE notification_preferences FORCE ROW LEVEL SECURITY",
    "CREATE POLICY notification_preferences_tenant_isolation ON notification_preferences FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    "CREATE POLICY notification_preferences_platform_read ON notification_preferences FOR SELECT"
    " USING (tenant_id IS NULL)",
    # notification_logs — tenant-owned, append-only. tenant rows isolated;
    # platform (NULL tenant) sends exposed read-only (operator inbox view).
    "ALTER TABLE notification_logs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE notification_logs FORCE ROW LEVEL SECURITY",
    "CREATE POLICY notification_logs_tenant_isolation ON notification_logs FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    "CREATE POLICY notification_logs_platform_read ON notification_logs FOR SELECT"
    " USING (tenant_id IS NULL)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS notification_logs_platform_read ON notification_logs",
    "DROP POLICY IF EXISTS notification_logs_tenant_isolation ON notification_logs",
    "ALTER TABLE notification_logs DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS notification_preferences_platform_read ON notification_preferences",
    "DROP POLICY IF EXISTS notification_preferences_tenant_isolation ON notification_preferences",
    "ALTER TABLE notification_preferences DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS notification_channels_platform_read ON notification_channels",
    "DROP POLICY IF EXISTS notification_channels_tenant_isolation ON notification_channels",
    "ALTER TABLE notification_channels DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # notification_channels — hybrid (platform | tenant | user scope).
    # -----------------------------------------------------------------------
    op.create_table(
        "notification_channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("channel_type", sa.String(length=16), nullable=False),
        # NULL => platform-scoped (tenant-agnostic); NOT NULL => tenant/user.
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # Never-plaintext secret: EXACTLY ONE of these two is set.
        sa.Column("secret_ref", sa.String(length=512), nullable=True),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_notification_channels_owner_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_channels"),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_user_id",
            "channel_type",
            "name",
            name="uq_notification_channels_scope_type_name",
        ),
        # At most one never-plaintext secret source (never both).
        sa.CheckConstraint(
            "NOT (secret_ref IS NOT NULL AND secret_encrypted IS NOT NULL)",
            name="ck_notification_channels_single_secret",
        ),
        # scope ↔ tenant consistency: platform => NULL tenant; tenant/user
        # => NOT NULL tenant. (A user channel additionally has an owner, but
        # we keep that softer — the service layer sets owner_user_id.)
        sa.CheckConstraint(
            "(scope = 'platform' AND tenant_id IS NULL)"
            " OR (scope IN ('tenant', 'user') AND tenant_id IS NOT NULL)",
            name="ck_notification_channels_scope_tenant",
        ),
    )
    op.create_index(
        "ix_notification_channels_tenant_id",
        "notification_channels",
        ["tenant_id"],
    )
    op.create_index(
        "ix_notification_channels_tenant_enabled",
        "notification_channels",
        ["tenant_id", "enabled"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_notification_channels_platform_type",
        "notification_channels",
        ["channel_type"],
        postgresql_where=sa.text("tenant_id IS NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_notification_channels_owner",
        "notification_channels",
        ["owner_user_id"],
        postgresql_where=sa.text("owner_user_id IS NOT NULL"),
    )

    # -----------------------------------------------------------------------
    # notification_preferences — hybrid (platform | tenant | user scope).
    # -----------------------------------------------------------------------
    op.create_table(
        "notification_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("channel_type", sa.String(length=16), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("quiet_hours_start", sa.Integer(), nullable=True),
        sa.Column("quiet_hours_end", sa.Integer(), nullable=True),
        sa.Column("quiet_hours_tz", sa.String(length=64), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_notification_preferences_owner_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_preferences"),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_user_id",
            "event_type",
            "channel_type",
            name="uq_notification_preferences_scope_event_channel",
        ),
        sa.CheckConstraint(
            "(scope = 'platform' AND tenant_id IS NULL)"
            " OR (scope IN ('tenant', 'user') AND tenant_id IS NOT NULL)",
            name="ck_notification_preferences_scope_tenant",
        ),
    )
    op.create_index(
        "ix_notification_preferences_tenant_id",
        "notification_preferences",
        ["tenant_id"],
    )
    op.create_index(
        "ix_notification_preferences_tenant_event",
        "notification_preferences",
        ["tenant_id", "event_type"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_notification_preferences_owner",
        "notification_preferences",
        ["owner_user_id"],
        postgresql_where=sa.text("owner_user_id IS NOT NULL"),
    )

    # -----------------------------------------------------------------------
    # notification_logs — tenant-owned, append-only.
    # -----------------------------------------------------------------------
    op.create_table(
        "notification_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # SET NULL on channel delete so the historical log survives.
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=True),
        # NULL => a platform-scoped send (mirrors audit_log.tenant_id).
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("channel_type", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("target", sa.String(length=512), nullable=True),
        sa.Column(
            "attempt",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["notification_channels.id"],
            name="fk_notification_logs_channel",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_logs"),
    )
    op.create_index(
        "ix_notification_logs_tenant_id",
        "notification_logs",
        ["tenant_id"],
    )
    op.create_index(
        "ix_notification_logs_tenant_created",
        "notification_logs",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_notification_logs_channel_created",
        "notification_logs",
        ["channel_id", "created_at"],
    )
    op.create_index(
        "ix_notification_logs_event_created",
        "notification_logs",
        ["event_type", "created_at"],
    )
    op.create_index(
        "ix_notification_logs_status",
        "notification_logs",
        ["status", "created_at"],
        postgresql_where=sa.text("status IN ('retrying', 'dead_letter', 'failed')"),
    )

    # -----------------------------------------------------------------------
    # RLS — applied last so the tables exist.
    # -----------------------------------------------------------------------
    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    # RLS first (policies depend on the tables).
    for stmt in _RLS_DOWN:
        op.execute(stmt)

    # Drop in dependency order: logs → preferences → channels.
    op.drop_table("notification_logs")
    op.drop_table("notification_preferences")
    op.drop_table("notification_channels")
