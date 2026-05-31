"""Índices FK faltantes (Plan 06.14 task_06_14_15, db-models-migrations-3/4).

Dos índices de soporte que la auditoría marcó como huecos de rendimiento:

  1. `ix_projects_team_id` — `projects.team_id` tiene la FK
     (`teams.id ON DELETE SET NULL`) pero ningún índice. Sin él, borrar un
     team obliga a Postgres a escanear toda la tabla `projects` buscando
     hijos, y "todos los proyectos del team X" es un seq-scan. Índice
     parcial sobre filas vivas (`deleted_at IS NULL`) y no nulas
     (`team_id IS NOT NULL`) — los proyectos sin team no aportan al índice.

  2. `ix_review_sessions_plan_status` — composite `(plan_id, status)`
     sobre filas vivas. `plan_id` ya tenía índice simple
     (`ix_review_sessions_plan_id`, migración 0024); este lo complementa
     para la consulta "sesiones de un plan filtradas por estado" sin
     reemplazarlo.

Ambos son aditivos y reversibles: `downgrade` los elimina sin tocar datos.

Revision ID: 0031_fk_indexes_cleanup
Revises: 0030_kb_categories_is_builtin
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_fk_indexes_cleanup"
down_revision: str | Sequence[str] | None = "0030_kb_categories_is_builtin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_projects_team_id",
        "projects",
        ["team_id"],
        postgresql_where=sa.text("team_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_review_sessions_plan_status",
        "review_sessions",
        ["plan_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_review_sessions_plan_status", table_name="review_sessions")
    op.drop_index("ix_projects_team_id", table_name="projects")
