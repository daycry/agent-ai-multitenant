"""kb_categories + knowledge_bases.category_id (Plan 06.10 task_06_10_01).

Categorizing KBs. Two flavours of category coexist in the same table:

  * **Built-in** (`tenant_id IS NULL`) — seeded by the platform under
    PLATFORM_TENANT_ID concept. Visible to every tenant via a SELECT
    policy that allows unconditional reads. Created and edited only
    via seed scripts; the tenant API cannot touch them.
  * **Custom** (`tenant_id IS NOT NULL`) — created by a tenant_admin.
    Standard tenant isolation policy (FOR ALL).

Why two policies in one table:
  Same pattern Plan 01 used for `approval_policy_templates` (the four
  built-in presets share the table with tenant-created policies, with
  a `_builtin_read` SELECT policy granting cross-tenant visibility on
  `is_builtin=true` rows). Avoids a second table + UNION queries.

`knowledge_bases.category_id` is `NULLABLE` and uses `ON DELETE SET
NULL`: deleting a category leaves the KBs in "uncategorised" rather
than cascading. The tenant_admin re-categorises them later. Mass-
delete of KBs is a separate concern that should be explicit, never
a side effect of touching a category.

Revision ID: 0028_kb_categories
Revises: 0027_projects_default_kb_grants
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_kb_categories"
down_revision: str | Sequence[str] | None = "0027_projects_default_kb_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # kb_categories
    # ------------------------------------------------------------------
    op.create_table(
        "kb_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # NULL = built-in del platform; NOT NULL = custom del tenant.
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Slug ASCII kebab-case estable. Único per-tenant (incluyendo
        # built-ins donde tenant_id es NULL).
        sa.Column("slug", sa.String(60), nullable=False),
        # Nombre legible (mostrado en la UI).
        sa.Column("name", sa.String(120), nullable=False),
        # Color hex opcional (`#3b82f6`). La UI lo usa para el badge.
        sa.Column("color", sa.String(16), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
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
        sa.Column(
            "deleted_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_kb_categories_created_by", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_kb_categories"),
    )
    # Unique slug per "scope" — NULL tenant_id (built-ins) is its own
    # scope. The partial index uses COALESCE to make NULL group with
    # other NULLs (otherwise NULL never equals NULL in a UNIQUE).
    op.execute(
        "CREATE UNIQUE INDEX ix_kb_categories_scope_slug"
        " ON kb_categories (COALESCE(tenant_id::text, ''), slug)"
        " WHERE deleted_at IS NULL"
    )
    op.create_index(
        "ix_kb_categories_tenant_id",
        "kb_categories",
        ["tenant_id"],
    )

    # ------------------------------------------------------------------
    # RLS — two policies coexist:
    #   * tenant_isolation: standard "tenant_id = app.tenant_id" for
    #     custom rows; non-bypass app_user reads/writes only its own.
    #   * builtin_read: unconditional SELECT for tenant sessions on
    #     rows where tenant_id IS NULL (built-ins are public).
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE kb_categories ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE kb_categories FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY kb_categories_tenant_isolation ON kb_categories FOR ALL "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    op.execute(
        "CREATE POLICY kb_categories_builtin_read ON kb_categories FOR SELECT "
        "USING (tenant_id IS NULL)"
    )

    # ------------------------------------------------------------------
    # knowledge_bases.category_id
    # ------------------------------------------------------------------
    op.add_column(
        "knowledge_bases",
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_knowledge_bases_category_id",
        "knowledge_bases",
        "kb_categories",
        ["category_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_knowledge_bases_category_id",
        "knowledge_bases",
        ["category_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_bases_category_id", table_name="knowledge_bases")
    op.drop_constraint("fk_knowledge_bases_category_id", "knowledge_bases", type_="foreignkey")
    op.drop_column("knowledge_bases", "category_id")

    op.execute("DROP POLICY IF EXISTS kb_categories_builtin_read ON kb_categories")
    op.execute("DROP POLICY IF EXISTS kb_categories_tenant_isolation ON kb_categories")
    op.execute("ALTER TABLE kb_categories DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_kb_categories_tenant_id", table_name="kb_categories")
    op.execute("DROP INDEX IF EXISTS ix_kb_categories_scope_slug")
    op.drop_table("kb_categories")
