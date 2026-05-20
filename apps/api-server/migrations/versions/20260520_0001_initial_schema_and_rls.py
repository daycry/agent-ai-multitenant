"""Initial schema + Row-Level Security.

Creates the five phase-0 tables (organizations, users,
user_org_memberships, sessions, audit_log) and turns on
PostgreSQL RLS for every tenant-scoped table.

Policy model:

  - organizations: a non-bypass role sees the single row whose id
    matches `current_setting('app.tenant_id')`.
  - user_org_memberships, audit_log: filtered by
    `tenant_id = current_setting('app.tenant_id')`.
  - sessions: filtered by `user_id = current_setting('app.user_id')`
    (sessions exist before the user picks an active tenant, so
    tenant_id is nullable and we key off user_id instead).
  - users: NO RLS — global, scoped by code via memberships.

NULLIF + cast: `current_setting(..., true)` returns the empty string
when unset (with missing_ok=true). NULLIF turns it into NULL before
the ::uuid cast so the policy never raises and unset sessions see
zero rows (safe default).

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# RLS DDL emitted as raw SQL — Alembic ops don't model row-level security.
# Statements are kept as a list so asyncpg sends them one prepared
# statement at a time (asyncpg refuses multi-statement strings).
# ---------------------------------------------------------------------------

_RLS_POLICIES_UP: tuple[str, ...] = (
    # organizations — only the current tenant's own row is visible.
    "ALTER TABLE organizations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE organizations FORCE ROW LEVEL SECURITY",
    "CREATE POLICY org_self_only ON organizations FOR ALL"
    " USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    # user_org_memberships — scoped by tenant_id.
    "ALTER TABLE user_org_memberships ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE user_org_memberships FORCE ROW LEVEL SECURITY",
    "CREATE POLICY membership_tenant_isolation ON user_org_memberships FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    # sessions — scoped by user_id (tenant_id is nullable during login).
    "ALTER TABLE sessions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE sessions FORCE ROW LEVEL SECURITY",
    "CREATE POLICY session_owner_only ON sessions FOR ALL"
    " USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)",
    # audit_log — scoped by tenant_id. System Admin cross-tenant rows
    # (tenant_id IS NULL) are invisible to tenant roles; only BYPASSRLS
    # roles read those.
    "ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE audit_log FORCE ROW LEVEL SECURITY",
    "CREATE POLICY audit_log_tenant_isolation ON audit_log FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_POLICIES_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS audit_log_tenant_isolation ON audit_log",
    "ALTER TABLE audit_log DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS session_owner_only ON sessions",
    "ALTER TABLE sessions DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS membership_tenant_isolation ON user_org_memberships",
    "ALTER TABLE user_org_memberships DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS org_self_only ON organizations",
    "ALTER TABLE organizations DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # organizations
    # -----------------------------------------------------------------------
    op.create_table(
        "organizations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
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
    )

    # -----------------------------------------------------------------------
    # users (global — NOT tenant-scoped)
    # -----------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column(
            "is_system_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "last_login_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
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
    )

    # -----------------------------------------------------------------------
    # user_org_memberships (tenant-scoped, M:N user<->org + role)
    # -----------------------------------------------------------------------
    op.create_table(
        "user_org_memberships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
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
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),
    )
    op.create_index("ix_user_org_memberships_tenant_id", "user_org_memberships", ["tenant_id"])
    op.create_index("ix_user_org_memberships_user_id", "user_org_memberships", ["user_id"])
    op.create_index(
        "ix_membership_tenant_user",
        "user_org_memberships",
        ["tenant_id", "user_id"],
    )

    # -----------------------------------------------------------------------
    # sessions
    # -----------------------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "expires_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_active_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "revoked_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
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
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_tenant_id", "sessions", ["tenant_id"])
    op.create_index("ix_sessions_user_active", "sessions", ["user_id", "revoked_at"])

    # -----------------------------------------------------------------------
    # audit_log — append-only
    # -----------------------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("changes", postgresql.JSONB(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_tenant_created", "audit_log", ["tenant_id", "created_at"])
    op.create_index("ix_audit_log_action_created", "audit_log", ["action", "created_at"])

    # -----------------------------------------------------------------------
    # RLS policies — applied last so the tables exist.
    # -----------------------------------------------------------------------
    for stmt in _RLS_POLICIES_UP:
        op.execute(stmt)


def downgrade() -> None:
    # RLS first (policies depend on the tables).
    for stmt in _RLS_POLICIES_DOWN:
        op.execute(stmt)

    op.drop_table("audit_log")
    op.drop_table("sessions")
    op.drop_table("user_org_memberships")
    op.drop_table("users")
    op.drop_table("organizations")
