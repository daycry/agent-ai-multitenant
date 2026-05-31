"""marketplace_sources / listings / installations / audit_entries (Plan 09 task_09_02).

Creates the four-table marketplace substrate defined by the ORM in
``api_server.db.marketplace`` (task_09_01) and wires the Row-Level
Security policies. The tenancy decisions are encoded both in the model
docstrings and here:

  - **``marketplace_sources``** — tenant-agnostic registry. NO RLS. A
    *private* tenant catalog is a row with ``owner_tenant_id`` set;
    visibility is resolved in the service layer.

  - **``marketplace_listings``** — hybrid. ``tenant_id`` NULLABLE: a NULL
    row is a global catalog listing visible to every tenant; a non-NULL
    row is private to that tenant. RLS mirrors the builtin skills/tools /
    agents pattern (migration 0004): a FOR ALL tenant-isolation policy
    (USING + WITH CHECK on the current tenant) for private rows, plus a
    SELECT-only policy that exposes global rows (``tenant_id IS NULL``)
    to any authenticated session. Writes to global rows are therefore
    reserved for BYPASSRLS roles (the System Admin / catalog publisher).

  - **``marketplace_installations``** — tenant-owned. ``tenant_id NOT
    NULL`` + a FOR ALL tenant-isolation RLS policy. Tenant A can never
    see, install over, or revoke tenant B's installation.

  - **``marketplace_audit_entries``** — tenant-owned, append-only.
    ``tenant_id NOT NULL`` + a FOR ALL tenant-isolation RLS policy.

The RLS DDL copies the canonical NULLIF + cast shape from migration 0001
/ 0020 so unset sessions see zero rows (safe default). All four tables
carry FK indexes for their FK columns. The migration is fully reversible:
``downgrade`` drops the policies, indexes, and tables in dependency order
(audit → installations → listings → sources).

Revision ID: 0041_marketplace
Revises: 0040_sso_email_domains
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041_marketplace"
down_revision: str | Sequence[str] | None = "0040_sso_email_domains"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# RLS DDL emitted as raw SQL — Alembic ops don't model row-level security.
# Statements are sent one at a time (asyncpg refuses multi-statement
# strings). The NULLIF(..., '') guard turns the empty string returned by
# current_setting(..., true) on an unset GUC into NULL before the ::uuid
# cast, so an unset session deterministically matches zero rows.
# ---------------------------------------------------------------------------
_RLS_UP: tuple[str, ...] = (
    # marketplace_listings — hybrid. Private rows are isolated by the
    # FOR ALL policy; global (tenant_id IS NULL) rows are exposed to
    # every session via the SELECT-only policy. Writes to global rows are
    # reserved for BYPASSRLS roles (the WITH CHECK below rejects a write
    # that doesn't carry the current tenant_id).
    "ALTER TABLE marketplace_listings ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE marketplace_listings FORCE ROW LEVEL SECURITY",
    "CREATE POLICY marketplace_listings_tenant_isolation ON marketplace_listings FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    "CREATE POLICY marketplace_listings_global_read ON marketplace_listings FOR SELECT"
    " USING (tenant_id IS NULL)",
    # marketplace_installations — tenant-owned.
    "ALTER TABLE marketplace_installations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE marketplace_installations FORCE ROW LEVEL SECURITY",
    "CREATE POLICY marketplace_installations_tenant_isolation ON marketplace_installations FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    # marketplace_audit_entries — tenant-owned, append-only.
    "ALTER TABLE marketplace_audit_entries ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE marketplace_audit_entries FORCE ROW LEVEL SECURITY",
    "CREATE POLICY marketplace_audit_entries_tenant_isolation ON marketplace_audit_entries"
    " FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS marketplace_audit_entries_tenant_isolation"
    " ON marketplace_audit_entries",
    "ALTER TABLE marketplace_audit_entries DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS marketplace_installations_tenant_isolation"
    " ON marketplace_installations",
    "ALTER TABLE marketplace_installations DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS marketplace_listings_global_read ON marketplace_listings",
    "DROP POLICY IF EXISTS marketplace_listings_tenant_isolation ON marketplace_listings",
    "ALTER TABLE marketplace_listings DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # marketplace_sources — tenant-agnostic registry (no RLS).
    # -----------------------------------------------------------------------
    op.create_table(
        "marketplace_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "source_type",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'official'"),
        ),
        sa.Column("uri", sa.String(length=1024), nullable=True),
        sa.Column(
            "is_trusted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "requires_signature",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "default_trust_level",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'experimental'"),
        ),
        sa.Column("owner_tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_marketplace_sources"),
        sa.UniqueConstraint("name", name="uq_marketplace_sources_name"),
    )
    # Partial index for private-catalog lookups by owner tenant.
    op.create_index(
        "ix_marketplace_sources_owner_tenant",
        "marketplace_sources",
        ["owner_tenant_id"],
        postgresql_where=sa.text("owner_tenant_id IS NOT NULL"),
    )

    # -----------------------------------------------------------------------
    # marketplace_listings — hybrid (global catalog OR private listing).
    # -----------------------------------------------------------------------
    op.create_table(
        "marketplace_listings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        # NULL => global catalog listing; non-NULL => private to a tenant.
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column(
            "trust_level",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'experimental'"),
        ),
        sa.Column(
            "manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "requested_permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("signature", sa.Text(), nullable=True),
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
            ["source_id"],
            ["marketplace_sources.id"],
            name="fk_marketplace_listings_source",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_marketplace_listings"),
        sa.UniqueConstraint(
            "source_id",
            "tenant_id",
            "name",
            "version",
            name="uq_marketplace_listings_source_tenant_name_version",
        ),
    )
    # FK index for the source FK + the plain tenant_id index the model
    # declares (index=True on the column).
    op.create_index(
        "ix_marketplace_listings_source_id",
        "marketplace_listings",
        ["source_id"],
    )
    op.create_index(
        "ix_marketplace_listings_tenant_id",
        "marketplace_listings",
        ["tenant_id"],
    )
    # Browse paths: private (tenant, kind) and global catalog by kind.
    op.create_index(
        "ix_marketplace_listings_tenant_kind",
        "marketplace_listings",
        ["tenant_id", "kind"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_marketplace_listings_global_kind",
        "marketplace_listings",
        ["kind"],
        postgresql_where=sa.text("tenant_id IS NULL AND deleted_at IS NULL"),
    )

    # -----------------------------------------------------------------------
    # marketplace_installations — tenant-owned.
    # -----------------------------------------------------------------------
    op.create_table(
        "marketplace_installations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'enabled'"),
        ),
        sa.Column(
            "granted_permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("installed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "installed_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_by", postgresql.UUID(as_uuid=True), nullable=True),
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
            name="fk_marketplace_installations_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["marketplace_listings.id"],
            name="fk_marketplace_installations_listing",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_marketplace_installations_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installed_by"],
            ["users.id"],
            name="fk_marketplace_installations_installed_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["users.id"],
            name="fk_marketplace_installations_revoked_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_marketplace_installations"),
    )
    # tenant_id index (TenantScopedMixin declares index=True).
    op.create_index(
        "ix_marketplace_installations_tenant_id",
        "marketplace_installations",
        ["tenant_id"],
    )
    # FK indexes for the joinable FK columns.
    op.create_index(
        "ix_marketplace_installations_listing_id",
        "marketplace_installations",
        ["listing_id"],
    )
    op.create_index(
        "ix_marketplace_installations_project_id",
        "marketplace_installations",
        ["project_id"],
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )
    op.create_index(
        "ix_marketplace_installations_installed_by",
        "marketplace_installations",
        ["installed_by"],
        postgresql_where=sa.text("installed_by IS NOT NULL"),
    )
    op.create_index(
        "ix_marketplace_installations_revoked_by",
        "marketplace_installations",
        ["revoked_by"],
        postgresql_where=sa.text("revoked_by IS NOT NULL"),
    )
    # At most one LIVE (non-revoked, non-deleted) install per
    # (tenant, listing, project).
    op.create_index(
        "uq_marketplace_installations_live",
        "marketplace_installations",
        ["tenant_id", "listing_id", "project_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND status != 'revoked'"),
    )
    op.create_index(
        "ix_marketplace_installations_tenant_status",
        "marketplace_installations",
        ["tenant_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # -----------------------------------------------------------------------
    # marketplace_audit_entries — tenant-owned, append-only.
    # -----------------------------------------------------------------------
    op.create_table(
        "marketplace_audit_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "detail",
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_marketplace_audit_entries_tenant",
            ondelete="CASCADE",
        ),
        # The target listing/installation may be soft-deleted while the
        # immutable audit row survives — SET NULL keeps the trail intact.
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["marketplace_listings.id"],
            name="fk_marketplace_audit_entries_listing",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["marketplace_installations.id"],
            name="fk_marketplace_audit_entries_installation",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_marketplace_audit_entries"),
    )
    # tenant_id index (declared index=True on the column) + chronological
    # tenant/action views, and an FK index for the listing FK.
    op.create_index(
        "ix_marketplace_audit_entries_tenant_id",
        "marketplace_audit_entries",
        ["tenant_id"],
    )
    op.create_index(
        "ix_marketplace_audit_tenant_created",
        "marketplace_audit_entries",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_marketplace_audit_action_created",
        "marketplace_audit_entries",
        ["action", "created_at"],
    )
    op.create_index(
        "ix_marketplace_audit_listing",
        "marketplace_audit_entries",
        ["listing_id"],
        postgresql_where=sa.text("listing_id IS NOT NULL"),
    )
    op.create_index(
        "ix_marketplace_audit_installation",
        "marketplace_audit_entries",
        ["installation_id"],
        postgresql_where=sa.text("installation_id IS NOT NULL"),
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

    # Drop in dependency order: audit → installations → listings → sources.
    op.drop_table("marketplace_audit_entries")
    op.drop_table("marketplace_installations")
    op.drop_table("marketplace_listings")
    op.drop_table("marketplace_sources")
