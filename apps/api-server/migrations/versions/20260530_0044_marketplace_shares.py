"""marketplace_shares — cross-tenant sharing (opt-in, audited) (Plan 09 task_09_17).

Cross-tenant sharing is the one place tenant boundaries are deliberately
crossed in the marketplace — so it is implemented as an EXPLICIT, audited
GRANT, never an implicit RLS bypass. This migration adds the
``marketplace_shares`` table and the RLS that wires the grant semantics:

  - **``marketplace_shares``** — one row per opt-in grant: a private
    ``listing_id``, the ``owner_tenant_id`` that shared it, and the single
    ``target_tenant_id`` it is shared WITH. RLS is dual-scoped:

      * a ``FOR ALL`` policy keyed on ``owner_tenant_id`` = the current tenant
        lets the OWNER tenant create / list / revoke its own grants (the
        WITH CHECK rejects a forged ``owner_tenant_id``), and
      * a ``FOR SELECT`` policy keyed on ``target_tenant_id`` = the current
        tenant lets the TARGET tenant *read* the grants naming it as recipient
        ("shared by tenant X") without being able to manage them.

  - a new ``FOR SELECT`` policy ``marketplace_listings_shared_read`` on
    ``marketplace_listings`` exposes a (private) listing to the current tenant
    iff a LIVE share row (``deleted_at IS NULL AND revoked_at IS NULL``) grants
    it to that tenant. The target therefore sees/installs the shared listing
    ONLY through the explicit grant; revoking the share removes the visibility
    immediately. This is purely additive to the Phase-A SELECT policies
    (``marketplace_listings_tenant_isolation`` private read/write +
    ``marketplace_listings_global_read``); it grants no write path.

The System Admin (BYPASSRLS ``migrations_user`` session) enumerates ALL shares
for audit, untouched by these policies.

Default = nothing shared: with no live share row the target tenant sees
nothing. Fully reversible — ``downgrade`` drops the listings shared-read
policy, then the shares table (its own policies, indexes, and FKs go with it).

Revision ID: 0044_marketplace_shares
Revises: 0043_mkt_audit_append_only
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044_marketplace_shares"
down_revision: str | Sequence[str] | None = "0043_mkt_audit_append_only"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# RLS DDL — sent one statement at a time (asyncpg refuses multi-statement
# strings). The NULLIF(..., '') guard turns the empty string returned by
# current_setting(..., true) on an unset GUC into NULL before the ::uuid
# cast, so an unset session deterministically matches zero rows (copied
# verbatim from 0041 so the tenant-isolation semantics are unchanged).
# ---------------------------------------------------------------------------
_RLS_UP: tuple[str, ...] = (
    # marketplace_shares — dual-scoped grant table.
    "ALTER TABLE marketplace_shares ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE marketplace_shares FORCE ROW LEVEL SECURITY",
    # The OWNER tenant manages (create / list / revoke) its own grants.
    "CREATE POLICY marketplace_shares_owner_manage ON marketplace_shares FOR ALL"
    " USING (owner_tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (owner_tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    # The TARGET tenant may READ the grants naming it as recipient.
    "CREATE POLICY marketplace_shares_target_read ON marketplace_shares FOR SELECT"
    " USING (target_tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    # marketplace_listings — expose a shared listing to its target tenant.
    # Additive SELECT-only policy: a listing is visible iff a LIVE share grants
    # it to the current tenant. No write path is granted (sharing never lets
    # the target mutate the owner's listing).
    "CREATE POLICY marketplace_listings_shared_read ON marketplace_listings FOR SELECT"
    " USING (EXISTS ("
    "   SELECT 1 FROM marketplace_shares s"
    "    WHERE s.listing_id = marketplace_listings.id"
    "      AND s.target_tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    "      AND s.deleted_at IS NULL"
    "      AND s.revoked_at IS NULL))",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS marketplace_listings_shared_read ON marketplace_listings",
    "DROP POLICY IF EXISTS marketplace_shares_target_read ON marketplace_shares",
    "DROP POLICY IF EXISTS marketplace_shares_owner_manage ON marketplace_shares",
    "ALTER TABLE marketplace_shares DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "marketplace_shares",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), nullable=True),
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
            ["listing_id"],
            ["marketplace_listings.id"],
            name="fk_marketplace_shares_listing",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_tenant_id"],
            ["organizations.id"],
            name="fk_marketplace_shares_owner_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_tenant_id"],
            ["organizations.id"],
            name="fk_marketplace_shares_target_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name="fk_marketplace_shares_granted_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["users.id"],
            name="fk_marketplace_shares_revoked_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_marketplace_shares"),
    )
    # At most one LIVE share per (listing, target).
    op.create_index(
        "uq_marketplace_shares_live",
        "marketplace_shares",
        ["listing_id", "target_tenant_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND revoked_at IS NULL"),
    )
    op.create_index(
        "ix_marketplace_shares_owner",
        "marketplace_shares",
        ["owner_tenant_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_marketplace_shares_target",
        "marketplace_shares",
        ["target_tenant_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # FK index for the listing FK + the granted_by / revoked_by FKs.
    op.create_index(
        "ix_marketplace_shares_listing_id",
        "marketplace_shares",
        ["listing_id"],
    )
    op.create_index(
        "ix_marketplace_shares_granted_by",
        "marketplace_shares",
        ["granted_by"],
        postgresql_where=sa.text("granted_by IS NOT NULL"),
    )
    op.create_index(
        "ix_marketplace_shares_revoked_by",
        "marketplace_shares",
        ["revoked_by"],
        postgresql_where=sa.text("revoked_by IS NOT NULL"),
    )

    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _RLS_DOWN:
        op.execute(stmt)
    op.drop_table("marketplace_shares")
