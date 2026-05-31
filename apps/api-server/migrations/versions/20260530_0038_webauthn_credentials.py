"""webauthn_credentials table — opt-in WebAuthn second factor (Plan 08 task_08_10).

Per-user, per-tenant WebAuthn / FIDO2 (passkey, security key) enrollment.
A SECOND alternative to TOTP in the SAME opt-in MFA challenge flow: a user
with no row here logs in EXACTLY as before; a user with a registered
authenticator is challenged after the password/SSO step and completes login
by signing a WebAuthn assertion instead of typing a TOTP code.

Tenant-scoped with the same RLS isolation guarantee as every other tenant
table: the app role (NOBYPASSRLS) only ever sees rows whose ``tenant_id``
matches ``current_setting('app.tenant_id')``. Registration and
authentication all run under ``app.tenant_id`` bound to the active tenant,
so one tenant's credentials are invisible to another. A user may register
several authenticators in a tenant, so the row is NOT unique per
``(tenant_id, user_id)`` — only the credential id is globally unique.

Secret handling (CLAUDE.md: no plaintext secrets in the DB). Nothing here is
a secret: a WebAuthn credential stores only the PUBLIC key (the private key
never leaves the authenticator), the public credential id and the signature
counter. The counter is the anti-cloning control — each accepted assertion
must present a strictly greater counter, so a replayed (stale-counter)
assertion is rejected.

Reversible: ``downgrade`` drops the RLS policy then the table.

Revision ID: 0038_webauthn_credentials
Revises: 0037_user_mfa_totp
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038_webauthn_credentials"
down_revision: str | Sequence[str] | None = "0037_user_mfa_totp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webauthn_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column(
            "transports",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("confirmed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
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
            name="fk_webauthn_credentials_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_webauthn_credentials_user",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("credential_id", name="uq_webauthn_credential_id"),
        sa.PrimaryKeyConstraint("id", name="pk_webauthn_credentials"),
    )
    op.create_index(
        "ix_webauthn_credentials_user_id",
        "webauthn_credentials",
        ["user_id"],
    )
    op.create_index(
        "ix_webauthn_tenant_user",
        "webauthn_credentials",
        ["tenant_id", "user_id"],
    )

    # RLS — same isolation guarantee as the rest of the tenant tables.
    # FORCE so even the table owner is subject to the policy. NULLIF + cast
    # mirrors migration 0001/0037's pattern: unset session -> zero rows.
    op.execute("ALTER TABLE webauthn_credentials ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE webauthn_credentials FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON webauthn_credentials FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON webauthn_credentials")
    op.drop_index("ix_webauthn_tenant_user", table_name="webauthn_credentials")
    op.drop_index("ix_webauthn_credentials_user_id", table_name="webauthn_credentials")
    op.drop_table("webauthn_credentials")
