"""teams: retirar la columna muerta `shared_memory_namespace` (Plan 06.17
task_06_17_15 / ADR 0053).

El ADR 0053 (Opción B) decide NO crear un subsistema TeamKnowledgeBase ni un
scope de memoria de equipo propio: la capacidad de equipo es la UNIÓN agregada
read-only de la de sus miembros. Como parte de esa decisión se **retira** el
campo muerto ``teams.shared_memory_namespace`` — no tenía ningún lector
productivo en los paths de recall/store (la memoria ``team_shared`` se resuelve
por ``project.team_id``, no por ese namespace).

Reversible: el ``downgrade`` recrea la columna con la MISMA definición que el
schema original (``20260521_0002_domain_minimum``): ``VARCHAR(120) NULL``, sin
default ni constraint. Como la columna no tenía lectores, dropearla no cambia
ningún comportamiento; recrearla la deja vacía (``NULL``) — idéntico estado al
de un ``teams`` recién creado antes del drop.

Revision ID: 0082_drop_shared_mem_ns
Revises: 0081_model_config_sanitize
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0082_drop_shared_mem_ns"
down_revision: str | Sequence[str] | None = "0081_model_config_sanitize"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("teams", "shared_memory_namespace")


def downgrade() -> None:
    op.add_column(
        "teams",
        sa.Column("shared_memory_namespace", sa.String(length=120), nullable=True),
    )
