"""Slug de proyecto único por tenant (P1-02, auditoría proyecto 2026-07-17).

El layout de bare repos usa `/repos/{tenant}/{project_slug}`: dos proyectos
vivos del mismo tenant con el mismo slug operan sobre el MISMO repo git. El
router ya deduplica al crear (`-{id8}` en colisión); este índice único parcial
es el backstop contra carreras y escrituras fuera del router.

Antes de crear el índice se deduplican los slugs vivos ya colisionados
(conserva el más antiguo, renombra el resto a `{slug}-{id8}` — mismo esquema
que el router). Reversible: el downgrade solo tira el índice (los renombrados
se quedan; un slug es estable por diseño, ADR 0085).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0114_projects_slug_unique"
down_revision: str | Sequence[str] | None = "0113_notification_log_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Dedupe de vivos: conserva el más antiguo de cada (tenant_id, slug) y
    # renombra los demás con el sufijo -{id8} (primeros 8 hex de su id).
    op.execute("""
        UPDATE projects p
        SET slug = p.slug || '-' || substr(replace(p.id::text, '-', ''), 1, 8)
        FROM (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY tenant_id, slug ORDER BY created_at, id
                   ) AS rn
            FROM projects
            WHERE deleted_at IS NULL
        ) ranked
        WHERE p.id = ranked.id AND ranked.rn > 1
        """)
    op.create_index(
        "uq_projects_tenant_slug_live",
        "projects",
        ["tenant_id", "slug"],
        unique=True,
        postgresql_where="deleted_at IS NULL",
    )


def downgrade() -> None:
    op.drop_index("uq_projects_tenant_slug_live", table_name="projects")
