"""user_invitations — el registro por invitación del ADR 0134 (opción C).

El operador cerró el registro público el 2026-07-31: se entra con un token que
emite un admin. Esta tabla es el soporte, y tiene tres rasgos que no son de
adorno:

1. **El token vive HASHEADO** (`token_hash`, SHA-256 hex de 64 chars) y nunca en
   claro, igual que `api_tokens` y `scim_tokens`. El `token_prefix` en claro
   existe solo para que el listado del admin distinga una invitación de otra sin
   revelarla.
2. **`UNIQUE` sobre el digest**, y no es higiene: el canje llega en una petición
   NO autenticada que solo trae el token, así que la búsqueda por hash tiene que
   ser un sondeo de índice y no un escaneo.
3. **RLS con `FORCE` y política por tenant**, como toda tabla con `tenant_id` en
   este repo — `tests/integration/test_rls_invariant.py` falla si aparece una
   tabla tenant-scoped sin proteger, así que crearla sin política habría roto la
   suite en el sitio correcto.

El índice de las pendientes es PARCIAL (`redeemed_at IS NULL AND revoked_at IS
NULL`): es la única consulta caliente —listado del admin y comprobación de
duplicados por email— y las canjeadas o revocadas se conservan para auditoría
pero no se consultan.

Revision ID: 0127_user_invitations
Revises: 0126_perf_indexes_uniqueness
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0127_user_invitations"
down_revision: str | Sequence[str] | None = "0126_perf_indexes_uniqueness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE user_invitations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE user_invitations FORCE ROW LEVEL SECURITY",
    "CREATE POLICY tenant_isolation ON user_invitations FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS tenant_isolation ON user_invitations",
    "ALTER TABLE user_invitations DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "user_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("redeemed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("redeemed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revoked_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_user_invitation_token_hash"),
        # SET NULL en los dos: la invitación sobrevive a su emisor y al usuario
        # que la canjeó — es un rastro de auditoría, no una relación de dominio.
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["redeemed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_user_invitations_tenant_pending",
        "user_invitations",
        ["tenant_id", "email"],
        unique=False,
        postgresql_where=sa.text("redeemed_at IS NULL AND revoked_at IS NULL"),
    )
    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    # Reversible de verdad: se retiran política, RLS, índice y tabla. Las
    # invitaciones se pierden con la tabla, que es el único dato que esta
    # migración introdujo.
    for stmt in _RLS_DOWN:
        op.execute(stmt)
    op.drop_index("ix_user_invitations_tenant_pending", table_name="user_invitations")
    op.drop_table("user_invitations")
