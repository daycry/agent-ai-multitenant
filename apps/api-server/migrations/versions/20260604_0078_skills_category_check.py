"""skills closed category (Plan 06.18 task_06_18_13, ADR 0050).

La tabla ``skills`` envió (migración 0001/0005) ``category`` como ``String``
libre y el enum ``SkillCategory`` divergía del seed real
(``api_server.seeds.builtin_skills``): el enum tenía nueve valores
(``coding``/``review``/``planning``/``data``/``security``…) y el seed solo usa
seis (``backend``/``frontend``/``devops``/``qa``/``research``/``docs``). ADR 0050
(Opción A) alinea el enum con el seed y aplica un ``CHECK`` en BD construido
desde ``SkillCategory`` — la única declaración — de modo que base de datos y
aplicación concuerdan.

Pre-flight sanitización (misma postura que migración 0077 para tools):

  * Cualquier skill **custom** cuya ``category`` quede fuera del conjunto cerrado
    se remapea a ``research`` (bucket genérico) *antes* de añadir el CHECK; los
    built-ins siempre conforman (el seed usa exactamente las seis categorías),
    así que nunca se tocan.

Reversible: ``downgrade`` elimina el CHECK y restaura 0077 exactamente (la
columna vuelve a ser ``String`` libre — no se pierde schema ni datos; el remap
de categorías es one-way, el trade-off aceptado del ADR, igual que 0077).

Revision ID: 0078_skills_category_check
Revises: 0077_tools_dedup_taxonomy
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0078_skills_category_check"
down_revision: str | Sequence[str] | None = "0077_tools_dedup_taxonomy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Bucket genérico al que se remapea cualquier categoría custom fuera del enum.
_FALLBACK_CATEGORY = "research"


def _allowed_categories() -> tuple[str, ...]:
    """Conjunto cerrado de categorías, derivado de ``SkillCategory``.

    Importado dentro de la migración para que el value set sea genuinamente
    data-driven desde la única declaración del enum; un assert cruza el enum
    contra el seed real para que nunca diverjan (igual que la migración 0077
    hace con ``ToolCategory``).
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
    categories = _allowed_categories()
    bind = op.get_bind()

    # Sanitiza: remapea cualquier categoría CUSTOM fuera del enum al bucket
    # genérico (los built-ins siempre conforman y se dejan intactos).
    bind.execute(
        sa.text(
            "UPDATE skills SET category = :fallback"
            " WHERE is_builtin = false AND category NOT IN :allowed"
        ).bindparams(
            sa.bindparam("fallback", _FALLBACK_CATEGORY),
            sa.bindparam("allowed", tuple(categories), expanding=True),
        )
    )

    op.create_check_constraint("ck_skills_category", "skills", _in_list("category", categories))


def downgrade() -> None:
    op.drop_constraint("ck_skills_category", "skills", type_="check")
