"""skills.category gains 'atlassian' — rebuild ck_skills_category (ADR 0127/0128).

La categoría cerrada de skills (migración 0078) se construyó con seis valores
(``backend``/``frontend``/``devops``/``qa``/``research``/``docs``). ADR 0127/0128
añaden un séptimo bucket, ``atlassian``, para las skills builtin que enseñan a
los agentes a usar el MCP de Atlassian del proyecto (Jira + Confluence). El
``CHECK`` ya materializado en BD por 0078 rechazaría filas ``atlassian``, así que
esta migración lo RECONSTRUYE.

``upgrade`` es data-driven desde ``SkillCategory`` (la única declaración; un
assert cruza el enum contra el seed real, igual que 0078) → el CHECK refleja
automáticamente el conjunto actual (los siete valores). ``downgrade`` es
reversible: remapea cualquier fila ``atlassian`` al bucket genérico ``research``
(el remap de categoría es one-way, el trade-off aceptado del ADR 0050, idéntico
a 0077/0078) y reinstala el CHECK con los seis valores previos, de modo que la
BD queda consistente con el código anterior a esta migración.

Revision ID: 0117_skills_atlassian_category
Revises: 0116_projects_mcp_tool_roles
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0117_skills_atlassian_category"
down_revision: str | Sequence[str] | None = "0116_projects_mcp_tool_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Bucket genérico al que se remapea 'atlassian' en el downgrade (igual que 0078).
_FALLBACK_CATEGORY = "research"

# El conjunto PREVIO a esta migración (lo que el downgrade debe reinstalar).
# Hardcodeado a propósito: el downgrade no puede derivarlo del enum, que para
# entonces ya incluirá 'atlassian'.
_PREVIOUS_CATEGORIES: tuple[str, ...] = (
    "backend",
    "devops",
    "docs",
    "frontend",
    "qa",
    "research",
)


def _current_categories() -> tuple[str, ...]:
    """Conjunto cerrado actual, derivado de ``SkillCategory`` (única fuente).

    Un assert cruza el enum contra el seed real para que nunca diverjan — la
    misma postura data-driven que la migración 0078.
    """
    from api_server.db.domain import SkillCategory
    from api_server.seeds.builtin_skills import BUILTIN_SKILLS

    enum_values = {c.value for c in SkillCategory}
    seed_categories = {s.category for s in BUILTIN_SKILLS}
    missing = seed_categories - enum_values
    assert not missing, f"SkillCategory no cubre categorías del seed: {sorted(missing)}"
    return tuple(sorted(enum_values))


def _in_list(column: str, values: Sequence[str]) -> str:
    rendered = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    categories = _current_categories()
    # Reconstruye el CHECK con el conjunto actual (incluye 'atlassian').
    op.drop_constraint("ck_skills_category", "skills", type_="check")
    op.create_check_constraint("ck_skills_category", "skills", _in_list("category", categories))


def downgrade() -> None:
    bind = op.get_bind()
    # Remapea cualquier fila 'atlassian' a 'research' ANTES de reinstalar el
    # CHECK previo (que no la admitiría). One-way, como 0077/0078.
    bind.execute(
        sa.text("UPDATE skills SET category = :fallback WHERE category = 'atlassian'").bindparams(
            sa.bindparam("fallback", _FALLBACK_CATEGORY),
        )
    )
    op.drop_constraint("ck_skills_category", "skills", type_="check")
    op.create_check_constraint(
        "ck_skills_category", "skills", _in_list("category", _PREVIOUS_CATEGORIES)
    )
