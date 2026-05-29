"""knowledge_bases.is_builtin + builtin_read policy (Plan 06.12 task_06_12_01).

Adopta el patrón canónico (A) del catálogo global (ADR 0029) para
`knowledge_bases`: una bandera `is_builtin` + una política
`knowledge_bases_builtin_read FOR SELECT USING (is_builtin = true)` que
expone las KB built-in (sembradas bajo PLATFORM_TENANT_ID) a TODA sesión
de tenant — igual que `skills`/`tools`/`teams`/`approval_policy_templates`.

Antes de esto las KB built-in eran invisibles: estaban bajo el platform
tenant pero `knowledge_bases` solo tenía `knowledge_bases_tenant_isolation`
(FOR ALL por tenant_id), sin política de catálogo (el bug del Plan 04).

`tenant_id` permanece NOT NULL (no usamos `tenant_id IS NULL` como señal
de global — ADR 0029). Backfill: marca `is_builtin=true` las filas bajo
PLATFORM_TENANT_ID.

Revision ID: 0029_knowledge_bases_is_builtin
Revises: 0028_kb_categories
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_knowledge_bases_is_builtin"
down_revision: str | Sequence[str] | None = "0028_kb_categories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Platform tenant — owner/parking-spot del catálogo global (ADR 0029).
_PLATFORM_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.add_column(
        "knowledge_bases",
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_knowledge_bases_is_builtin",
        "knowledge_bases",
        ["is_builtin"],
        postgresql_where=sa.text("is_builtin = true"),
    )
    # Backfill: las KB sembradas bajo el platform tenant son el catálogo.
    op.execute(
        "UPDATE knowledge_bases SET is_builtin = true" f" WHERE tenant_id = '{_PLATFORM_TENANT_ID}'"
    )
    # Catálogo legible por toda sesión de tenant (keya por la bandera, no
    # por tenant_id). Convive con knowledge_bases_tenant_isolation (FOR
    # ALL): PostgreSQL hace OR de ambas para SELECT; las escrituras solo
    # pasan la de aislamiento, así que un tenant no puede mutar catálogo.
    op.execute(
        "CREATE POLICY knowledge_bases_builtin_read ON knowledge_bases"
        " FOR SELECT USING (is_builtin = true)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS knowledge_bases_builtin_read ON knowledge_bases")
    op.drop_index("ix_knowledge_bases_is_builtin", table_name="knowledge_bases")
    op.drop_column("knowledge_bases", "is_builtin")
