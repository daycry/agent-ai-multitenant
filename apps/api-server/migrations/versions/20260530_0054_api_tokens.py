"""api_tokens — tenant-owned public-API credential (X-API-Token) + RLS (Plan 13 task_13_02).

Creates the ``api_tokens`` table whose ORM shape (columns, indexes,
constraints) is defined by :class:`api_server.db.models.ApiToken`. One
row is a per-tenant credential for the public REST API ``/api/v1`` (Plan
13 Decisiones Clave: ``X-API-Token`` in the HEADER, never a query param;
the token grants access SCOPED to its own tenant only). The Tenant Admin
mints/lists/revokes these via ``/auth/api-tokens`` (task_13_02); the
middleware that resolves a presented token to its tenant lands in
task_13_03.

Tenancy decision (CLAUDE.md principle 1): **tenant-owned** — ``tenant_id``
NOT NULL + the canonical FOR ALL tenant-isolation RLS policy (the same
NULLIF + ``::uuid`` cast shape copied from migrations 0001 / 0036 / 0053),
so a tenant manages ONLY its own tokens. There is no platform / NULL-tenant
branch. A token issued for tenant A can never read or write tenant B's
data. Resolving a presented token to its tenant runs once on the BYPASSRLS
role (the request is unauthenticated until the hash is matched); every
subsequent ``/api/v1`` query then runs on the app role (NOBYPASSRLS) with
``app.tenant_id`` bound to the resolved tenant.

Secret handling (CLAUDE.md: no plaintext secrets in the DB). Only the
SHA-256 hex digest of the raw token is stored (``token_hash``), never the
token itself; ``prefix`` keeps the leading clear ``<marker>_<id>`` segment
for UI disambiguation. A UNIQUE index on the digest both enforces global
uniqueness (it identifies a tenant on an unauthenticated request) and makes
the by-hash lookup an index probe.

Single head before this migration is ``0053_guardrail_alert_rules``; this is
``0054_api_tokens``. Fully reversible: ``downgrade`` drops the policy,
disables RLS, then drops the table. Proven by an up / down to
``0040_sso_email_domains`` / up cycle.

Revision ID: 0054_api_tokens
Revises: 0053_guardrail_alert_rules
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054_api_tokens"
down_revision: str | Sequence[str] | None = "0053_guardrail_alert_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Tenant-isolation RLS — canonical FOR ALL policy (copied verbatim from the
# scim_tokens / guardrail_alert_rules pattern). The NULLIF(..., '') guard
# turns the empty string an unset GUC returns into NULL before the ::uuid
# cast, so an unset session deterministically matches zero rows (safe
# default). FORCE so the policy applies even to the table owner.
# ---------------------------------------------------------------------------
_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE api_tokens FORCE ROW LEVEL SECURITY",
    "CREATE POLICY tenant_isolation ON api_tokens FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS tenant_isolation ON api_tokens",
    "ALTER TABLE api_tokens DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("""'["read"]'::jsonb"""),
        ),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "rate_limit",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("100"),
        ),
        sa.Column(
            "ip_allowlist",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_used_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
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
            ["tenant_id"],
            ["organizations.id"],
            name="fk_api_tokens_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_api_tokens_created_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_api_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_api_token_hash"),
    )
    # tenant_id index (TenantScopedMixin declares index=True).
    op.create_index("ix_api_tokens_tenant_id", "api_tokens", ["tenant_id"])
    # Partial index over live (non-revoked) tokens per tenant — the hot path
    # for the Tenant-Admin listing and the by-tenant active-token lookup.
    op.create_index(
        "ix_api_tokens_tenant_active",
        "api_tokens",
        ["tenant_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _RLS_DOWN:
        op.execute(stmt)
    op.drop_index("ix_api_tokens_tenant_active", table_name="api_tokens")
    op.drop_index("ix_api_tokens_tenant_id", table_name="api_tokens")
    op.drop_table("api_tokens")
