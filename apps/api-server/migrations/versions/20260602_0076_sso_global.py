"""sso_configurations platform-global + button_label (Plan sso-global-user-admin task_sso_01).

Re-scopes ``sso_configurations`` from **per-tenant** (Plan 08 / ADR 0031)
to **platform-global** (ADR 0047, supersedes the per-tenant part of 0031,
re-aligns with ADR 0028). Auth providers (OIDC / SAML) are now configured
ONCE by ``system_admin`` and serve every tenant; access to a tenant is
granted by ``UserOrganizationMembership`` AFTER login, not by which tenant
owns the provider.

Schema change (upgrade):

  * Add ``button_label`` (nullable) — the login-button text shown on the
    public ``/login`` page (a kind-derived default is used when NULL).
  * **Consolidate** the existing per-tenant rows into global ones: keep at
    most ONE row per ``provider``/kind for the whole platform. When several
    tenants had the same provider, the **most-recently-updated** row wins;
    the losers are removed (a NOTICE logs how many, per provider, so the
    operator can reconcile — ADR 0047 accepts this, in practice dev has
    very few). Soft-deleted rows (``deleted_at IS NOT NULL``) are dropped
    too — only the single live winner per provider survives.
  * Drop the per-tenant ``uq_sso_config_tenant_provider`` unique constraint
    and add a global ``uq_sso_config_provider`` (unique on ``provider``).
  * Drop the tenant indexes and the FK to ``organizations``; drop RLS +
    the ``tenant_isolation`` policy (the table is platform-global, gated
    at the app layer on the BYPASSRLS admin engine — same posture as
    ``llm_providers`` in migration 0070).
  * Drop the ``tenant_id`` column.

Secret handling (CLAUDE.md: no plaintext secrets in the DB) is
**UNCHANGED**: the ``client_secret_ref`` / ``client_secret_encrypted`` and
SP-key columns + their CHECK constraints are untouched.

Reversible: ``downgrade`` restores the per-tenant *shape* — re-adds the
``tenant_id`` column (NULLABLE: the surviving global rows have no tenant to
attribute, and ADR 0047 leaves reconciliation to the operator), the FK to
``organizations``, the tenant indexes, RLS + the ``tenant_isolation``
policy, the per-tenant unique constraint, and drops ``button_label``. The
rows consolidated away on upgrade are NOT resurrected (Alembic's
"data loss on downgrade is explicit" stance, matching migration 0033).

Single head before this migration is ``0075_memory_source_human_ws``;
this is ``0076_sso_global``. Proven up/down/up by
``tests/integration/test_migrations.py`` and the global-config behaviour by
``tests/integration/test_sso_global_config.py``.

Revision ID: 0076_sso_global
Revises: 0075_memory_source_human_ws
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0076_sso_global"
down_revision: str | Sequence[str] | None = "0075_memory_source_human_ws"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) New nullable login-button label column.
    op.add_column(
        "sso_configurations",
        sa.Column("button_label", sa.String(length=120), nullable=True),
    )

    # 2) Consolidate per-tenant rows into one global row per provider/kind.
    #    Keep the most-recently-updated LIVE row per provider; remove every
    #    other row (other tenants' duplicates AND soft-deleted rows). A
    #    NOTICE reports the per-provider drop count so the operator can
    #    reconcile (ADR 0047). Ordering ties break on created_at then id so
    #    the choice is deterministic.
    op.execute(
        """
        DO $$
        DECLARE
            dropped integer;
        BEGIN
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY provider
                           ORDER BY (deleted_at IS NULL) DESC,
                                    updated_at DESC,
                                    created_at DESC,
                                    id DESC
                       ) AS rn
                  FROM sso_configurations
            ), removed AS (
                DELETE FROM sso_configurations s
                 USING ranked r
                 WHERE s.id = r.id
                   AND r.rn > 1
                RETURNING s.id
            )
            SELECT count(*) INTO dropped FROM removed;
            IF dropped > 0 THEN
                RAISE NOTICE
                    'sso_configurations: consolidated per-tenant rows into '
                    'global; removed % duplicate/soft-deleted row(s)', dropped;
            END IF;
        END $$;
        """
    )

    # 3) Drop RLS + the tenant-isolation policy (platform-global now).
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON sso_configurations")
    op.execute("ALTER TABLE sso_configurations NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sso_configurations DISABLE ROW LEVEL SECURITY")

    # 4) Swap the per-tenant identity for a global one. The old unique
    #    constraint (tenant_id, provider) and the tenant indexes go; a
    #    global unique on provider replaces them.
    op.drop_constraint("uq_sso_config_tenant_provider", "sso_configurations", type_="unique")
    op.drop_index("ix_sso_configurations_tenant_enabled", table_name="sso_configurations")
    op.drop_index("ix_sso_configurations_tenant_id", table_name="sso_configurations")
    op.create_unique_constraint("uq_sso_config_provider", "sso_configurations", ["provider"])
    op.create_index(
        "ix_sso_configurations_enabled",
        "sso_configurations",
        ["provider", "enabled"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # 5) Drop the FK to organizations + the tenant_id column itself.
    op.drop_constraint("fk_sso_configurations_tenant", "sso_configurations", type_="foreignkey")
    op.drop_column("sso_configurations", "tenant_id")


def downgrade() -> None:
    # Restore the per-tenant SHAPE. tenant_id comes back NULLABLE: the
    # surviving global rows have no tenant to attribute (ADR 0047 leaves
    # reconciliation to the operator), and the rows consolidated away on
    # upgrade are not resurrected (data loss on downgrade is explicit).
    op.add_column(
        "sso_configurations",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # Undo the global identity.
    op.drop_index("ix_sso_configurations_enabled", table_name="sso_configurations")
    op.drop_constraint("uq_sso_config_provider", "sso_configurations", type_="unique")

    # Restore the FK to organizations + the per-tenant indexes + unique.
    op.create_foreign_key(
        "fk_sso_configurations_tenant",
        "sso_configurations",
        "organizations",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
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
    op.create_unique_constraint(
        "uq_sso_config_tenant_provider",
        "sso_configurations",
        ["tenant_id", "provider"],
    )

    # Restore RLS + the tenant-isolation policy (same shape as migration 0032).
    op.execute("ALTER TABLE sso_configurations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sso_configurations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON sso_configurations FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )

    # Drop the login-button label column.
    op.drop_column("sso_configurations", "button_label")
