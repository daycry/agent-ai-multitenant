"""incoming_webhooks — per-project inbound-webhook config + received events + RLS.

Plan 13 Fase C task_13_08. Two tenant + PROJECT scoped tables back the INBOUND
webhook flow (the inverse of Plan 10's OUTGOING signing — here an external tool
POSTs an event we VERIFY the HMAC of):

  * ``incoming_webhook_configs`` — one row per (project, origin) endpoint. The
    ``{config_id}`` in the public URL ``/webhooks/incoming/{origin}/{config_id}``
    resolves to a row, and THROUGH it to the row's ``tenant_id`` + ``project_id``
    — so an event for project A can never act on tenant B. The HMAC signing
    secret is stored ONLY as Fernet ciphertext (``signing_secret_encrypted``),
    never in clear (CLAUDE.md: no plaintext secrets in the DB).
  * ``incoming_webhook_events`` — one row per RECEIVED (verified) event, stored
    for replay/debugging (task_13_12). Records the raw body + the headers
    needed to re-derive the signature; a partial UNIQUE on
    ``(config_id, delivery_id)`` makes a sender's redelivery idempotent. The
    signing secret is NEVER stored here.

Both ORM shapes are :class:`api_server.db.models.IncomingWebhookConfig` /
``IncomingWebhookEvent``.

Tenancy decision (CLAUDE.md principle 1): both tables are tenant-owned —
``tenant_id`` NOT NULL + the canonical FOR ALL tenant-isolation RLS policy
(the NULLIF + ``::uuid`` cast shape copied verbatim from 0054_api_tokens), AND
project-scoped via a ``project_id`` FK (``ON DELETE CASCADE``). Resolving a
config on the PUBLIC endpoint runs once on the BYPASSRLS role (the request is
unauthenticated until the HMAC is verified); every tenant-facing query then
runs on the app role with ``app.tenant_id`` bound.

Single head before this migration is ``0054_api_tokens``; this is
``0055_incoming_webhooks``. Fully reversible: ``downgrade`` drops each policy,
disables RLS, then drops the tables (events first — it FKs the config). Proven
by an up / down to ``0040_sso_email_domains`` / up cycle.

Revision ID: 0055_incoming_webhooks
Revises: 0054_api_tokens
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0055_incoming_webhooks"
down_revision: str | Sequence[str] | None = "0054_api_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Tenant-isolation RLS — canonical FOR ALL policy (copied verbatim from the
# 0054_api_tokens pattern). NULLIF(..., '') turns the empty string an unset
# GUC returns into NULL before the ::uuid cast, so an unset session matches
# zero rows (safe default). FORCE so the policy applies even to the owner.
# ---------------------------------------------------------------------------
def _rls_up(table: str) -> tuple[str, ...]:
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {table} FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    )


def _rls_down(table: str) -> tuple[str, ...]:
    return (
        f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    )


def upgrade() -> None:
    op.create_table(
        "incoming_webhook_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("signing_secret_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("last_event_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
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
            ["tenant_id"],
            ["organizations.id"],
            name="fk_incoming_webhook_configs_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_incoming_webhook_configs_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_incoming_webhook_configs"),
        sa.CheckConstraint(
            "origin IN ('github', 'gitlab', 'jira', 'sentry', 'linear', 'generic')",
            name="ck_incoming_webhook_configs_origin",
        ),
    )
    # tenant_id index (TenantScopedMixin declares index=True).
    op.create_index(
        "ix_incoming_webhook_configs_tenant_id",
        "incoming_webhook_configs",
        ["tenant_id"],
    )
    # Live configs per (tenant, project) — the project-page listing hot path.
    op.create_index(
        "ix_incoming_webhook_configs_tenant_project",
        "incoming_webhook_configs",
        ["tenant_id", "project_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "incoming_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("delivery_id", sa.String(length=255), nullable=True),
        sa.Column("event_type", sa.String(length=255), nullable=True),
        sa.Column("signature", sa.String(length=512), nullable=True),
        sa.Column("raw_body", sa.Text(), nullable=False),
        sa.Column(
            "verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "received_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_incoming_webhook_events_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["config_id"],
            ["incoming_webhook_configs.id"],
            name="fk_incoming_webhook_events_config",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_incoming_webhook_events_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_incoming_webhook_events"),
    )
    op.create_index(
        "ix_incoming_webhook_events_tenant_id",
        "incoming_webhook_events",
        ["tenant_id"],
    )
    # Idempotent redelivery: a sender that retries reuses its delivery id, so a
    # partial UNIQUE on (config_id, delivery_id) collides on the second insert.
    op.create_index(
        "uq_incoming_webhook_events_delivery",
        "incoming_webhook_events",
        ["config_id", "delivery_id"],
        unique=True,
        postgresql_where=sa.text("delivery_id IS NOT NULL"),
    )
    # Replay listing: a config's events newest-first.
    op.create_index(
        "ix_incoming_webhook_events_config",
        "incoming_webhook_events",
        ["config_id", "received_at"],
    )

    for stmt in (*_rls_up("incoming_webhook_configs"), *_rls_up("incoming_webhook_events")):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in (*_rls_down("incoming_webhook_events"), *_rls_down("incoming_webhook_configs")):
        op.execute(stmt)
    # Events first — it FKs the config table.
    op.drop_index("ix_incoming_webhook_events_config", table_name="incoming_webhook_events")
    op.drop_index("uq_incoming_webhook_events_delivery", table_name="incoming_webhook_events")
    op.drop_index("ix_incoming_webhook_events_tenant_id", table_name="incoming_webhook_events")
    op.drop_table("incoming_webhook_events")
    op.drop_index(
        "ix_incoming_webhook_configs_tenant_project", table_name="incoming_webhook_configs"
    )
    op.drop_index("ix_incoming_webhook_configs_tenant_id", table_name="incoming_webhook_configs")
    op.drop_table("incoming_webhook_configs")
