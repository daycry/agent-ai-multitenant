"""user_mfa_totp table — opt-in TOTP second factor (Plan 08 task_08_09).

Per-user, per-tenant TOTP (RFC 6238) enrollment. MFA is ADDED ALONGSIDE
the existing auth (local login + OIDC + SAML): a user with no confirmed
row here logs in EXACTLY as before. Only a user with
``confirmed_at IS NOT NULL`` is challenged for a 6-digit code after the
password/SSO step succeeds.

Tenant-scoped with the same RLS isolation guarantee as every other
tenant table: the app role (NOBYPASSRLS) only ever sees rows whose
``tenant_id`` matches ``current_setting('app.tenant_id')``. Enrollment,
confirmation and recovery-code consumption all run under ``app.tenant_id``
bound to the active tenant, so one tenant's MFA state is invisible to
another. At most one enrollment per ``(tenant_id, user_id)`` (UNIQUE).

Secret handling (CLAUDE.md: no plaintext secrets in the DB):

  * ``secret_encrypted`` — the base32 TOTP seed, Fernet-encrypted at rest
    with the SAME ``API_SERVER_SSO_ENCRYPTION_KEY`` mechanism the OIDC
    client secret uses. Never the clear seed.
  * ``recovery_codes`` — a JSON array of one-time recovery codes, each
    stored ONLY as its SHA-256 hex digest. A code is consumed by removing
    its digest from the array, so each works exactly once.

Reversible: ``downgrade`` drops the RLS policy then the table.

Revision ID: 0037_user_mfa_totp
Revises: 0036_scim_tokens
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037_user_mfa_totp"
down_revision: str | Sequence[str] | None = "0036_scim_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_mfa_totp",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("confirmed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "recovery_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_user_mfa_totp_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_mfa_totp_user",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_mfa_totp_tenant_user"),
        sa.PrimaryKeyConstraint("id", name="pk_user_mfa_totp"),
    )
    op.create_index(
        "ix_user_mfa_totp_user_id",
        "user_mfa_totp",
        ["user_id"],
    )
    op.create_index(
        "ix_mfa_totp_tenant_user",
        "user_mfa_totp",
        ["tenant_id", "user_id"],
    )

    # RLS — same isolation guarantee as the rest of the tenant tables.
    # FORCE so even the table owner is subject to the policy. NULLIF + cast
    # mirrors migration 0001/0036's pattern: unset session -> zero rows.
    op.execute("ALTER TABLE user_mfa_totp ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_mfa_totp FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON user_mfa_totp FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON user_mfa_totp")
    op.drop_index("ix_mfa_totp_tenant_user", table_name="user_mfa_totp")
    op.drop_index("ix_user_mfa_totp_user_id", table_name="user_mfa_totp")
    op.drop_table("user_mfa_totp")
