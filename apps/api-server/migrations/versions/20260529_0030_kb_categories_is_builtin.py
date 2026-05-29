"""kb_categories alineada al patrón (A) del catálogo global (Plan 06.12
task_06_12_05, ADR 0029).

En el Plan 06.10 `kb_categories` se hizo con el patrón (B) —
`tenant_id IS NULL` para built-ins + policy `USING (tenant_id IS NULL)`.
ADR 0029 fija el patrón (A) canónico (bandera `is_builtin` + platform
tenant), así que aquí migramos el outlier:

  1. añade `is_builtin BOOLEAN NOT NULL DEFAULT false` + índice parcial,
  2. backfill: las built-in actuales (`tenant_id IS NULL`) pasan a
     `tenant_id = PLATFORM_TENANT_ID`, `is_builtin = true`,
  3. swap de la policy `kb_categories_builtin_read` de
     `USING (tenant_id IS NULL)` a `USING (is_builtin = true)`.

Los ids de las built-in son deterministas (`uuid5`), así que las KB que
referencian `category_id` siguen válidas. El índice único COALESCE de la
0028 se mantiene (ya no hay tenant_id NULL, pero sigue correcto).

Revision ID: 0030_kb_categories_is_builtin
Revises: 0029_knowledge_bases_is_builtin
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_kb_categories_is_builtin"
down_revision: str | Sequence[str] | None = "0029_knowledge_bases_is_builtin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PLATFORM_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.add_column(
        "kb_categories",
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_kb_categories_is_builtin",
        "kb_categories",
        ["is_builtin"],
        postgresql_where=sa.text("is_builtin = true"),
    )
    # Backfill: los built-in del patrón (B) (tenant_id NULL) pasan al
    # patrón (A): viven bajo el platform tenant con is_builtin=true.
    op.execute(
        "UPDATE kb_categories"
        f" SET is_builtin = true, tenant_id = '{_PLATFORM_TENANT_ID}'"
        " WHERE tenant_id IS NULL"
    )
    # Swap de la policy de catálogo: ahora keya por la bandera.
    op.execute("DROP POLICY IF EXISTS kb_categories_builtin_read ON kb_categories")
    op.execute(
        "CREATE POLICY kb_categories_builtin_read ON kb_categories"
        " FOR SELECT USING (is_builtin = true)"
    )


def downgrade() -> None:
    # Vuelve al patrón (B): built-ins a tenant_id NULL + policy por NULL.
    op.execute("DROP POLICY IF EXISTS kb_categories_builtin_read ON kb_categories")
    op.execute(
        "UPDATE kb_categories SET tenant_id = NULL"
        f" WHERE is_builtin = true AND tenant_id = '{_PLATFORM_TENANT_ID}'"
    )
    op.execute(
        "CREATE POLICY kb_categories_builtin_read ON kb_categories"
        " FOR SELECT USING (tenant_id IS NULL)"
    )
    op.drop_index("ix_kb_categories_is_builtin", table_name="kb_categories")
    op.drop_column("kb_categories", "is_builtin")
