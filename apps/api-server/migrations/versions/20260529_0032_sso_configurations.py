"""sso_configurations table (Plan 08 task_08_01).

Per-tenant enterprise SSO (OIDC) configuration. Tenant-scoped with the
same RLS isolation guarantee as every other tenant table: the app role
(NOBYPASSRLS) only ever sees rows whose ``tenant_id`` matches
``current_setting('app.tenant_id')``, so an OIDC login flow can never
resolve another tenant's IdP config.

Secret handling (CLAUDE.md: no plaintext secrets in the DB). The OIDC
``client_secret`` is stored in exactly one of two columns, never both,
never in clear text:

  * ``client_secret_ref``         — a Vault pointer (``vault:...``).
  * ``client_secret_encrypted``   — Fernet ciphertext (encrypted at
                                     rest with the SSO encryption key).

A CHECK constraint enforces the "at most one, never both" invariant.

Reversible: ``downgrade`` drops the RLS policy then the table.

Revision ID: 0032_sso_configurations
Revises: 0031_fk_indexes_cleanup
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_sso_configurations"
down_revision: str | Sequence[str] | None = "0031_fk_indexes_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sso_configurations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "provider",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'oidc'"),
        ),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("client_secret_ref", sa.String(length=512), nullable=True),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("""'["openid", "email", "profile"]'::jsonb"""),
        ),
        sa.Column(
            "claim_mappings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_sso_configurations_tenant",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_sso_config_tenant_provider"),
        # Secret invariant: never store both a Vault pointer and inline
        # ciphertext (ambiguous which wins) — at most one is set.
        sa.CheckConstraint(
            "NOT (client_secret_ref IS NOT NULL AND client_secret_encrypted IS NOT NULL)",
            name="ck_sso_config_single_secret_source",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sso_configurations"),
    )
    op.create_index(
        "ix_sso_configurations_tenant_id",
        "sso_configurations",
        ["tenant_id"],
    )
    op.create_index(
        "ix_sso_configurations_tenant_enabled",
        "sso_configurations",
        ["tenant_id", "enabled"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # RLS — same isolation guarantee as the rest of the tenant tables.
    # FORCE so even the table owner is subject to the policy. NULLIF +
    # cast mirrors migration 0001's pattern: unset session -> zero rows.
    op.execute("ALTER TABLE sso_configurations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sso_configurations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON sso_configurations FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON sso_configurations")
    op.drop_index("ix_sso_configurations_tenant_enabled", table_name="sso_configurations")
    op.drop_index("ix_sso_configurations_tenant_id", table_name="sso_configurations")
    op.drop_table("sso_configurations")
