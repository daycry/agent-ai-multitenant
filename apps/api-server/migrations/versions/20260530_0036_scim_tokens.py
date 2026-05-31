"""scim_tokens table + memberships.external_id (Plan 08 task_08_08).

Per-tenant SCIM 2.0 bearer credential. An IdP uses this token (not the
interactive JWT/session auth) to provision users into a tenant via the
``/scim/v2/Users`` endpoints, so the token IS the tenant context.

Also adds ``user_org_memberships.external_id`` — the IdP's stable
identifier for a user within a tenant (SCIM ``externalId``). It lives on
the tenant-scoped membership (not the global ``users`` row) because SCIM
externalId is scoped to the provisioning domain; NULL for non-SCIM users.

Tenant-scoped with the same RLS isolation guarantee as every other
tenant table: the app role (NOBYPASSRLS) only ever sees rows whose
``tenant_id`` matches ``current_setting('app.tenant_id')``. Resolving a
presented token to its tenant runs once on the BYPASSRLS role (the
request is unauthenticated until the token is matched); every subsequent
SCIM query runs under ``app.tenant_id`` bound to the resolved tenant, so
a token issued for tenant A can never touch tenant B's users.

Secret handling (CLAUDE.md: no plaintext secrets in the DB). Only the
SHA-256 hex digest of the token is stored (``token_hash``), never the
token itself; ``token_prefix`` keeps the first few clear characters for
UI disambiguation. A UNIQUE index on the digest both enforces global
uniqueness (it identifies a tenant on an unauthenticated request) and
makes the by-hash lookup an index probe.

Reversible: ``downgrade`` drops the RLS policy then the table.

Revision ID: 0036_scim_tokens
Revises: 0035_users_is_sso_provisioned
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036_scim_tokens"
down_revision: str | Sequence[str] | None = "0035_users_is_sso_provisioned"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scim_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("revoked_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_used_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
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
            name="fk_scim_tokens_tenant",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("token_hash", name="uq_scim_token_hash"),
        sa.PrimaryKeyConstraint("id", name="pk_scim_tokens"),
    )
    op.create_index(
        "ix_scim_tokens_tenant_id",
        "scim_tokens",
        ["tenant_id"],
    )
    op.create_index(
        "ix_scim_tokens_tenant_active",
        "scim_tokens",
        ["tenant_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # RLS — same isolation guarantee as the rest of the tenant tables.
    # FORCE so even the table owner is subject to the policy. NULLIF + cast
    # mirrors migration 0001/0032's pattern: unset session -> zero rows.
    op.execute("ALTER TABLE scim_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE scim_tokens FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON scim_tokens FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )

    # SCIM externalId on the tenant-scoped membership (NULL for non-SCIM
    # users). No RLS DDL needed: user_org_memberships already carries the
    # tenant_isolation policy from migration 0001.
    op.add_column(
        "user_org_memberships",
        sa.Column("external_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_org_memberships", "external_id")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON scim_tokens")
    op.drop_index("ix_scim_tokens_tenant_active", table_name="scim_tokens")
    op.drop_index("ix_scim_tokens_tenant_id", table_name="scim_tokens")
    op.drop_table("scim_tokens")
