"""teams.memory_scope — política de memoria del equipo (ADR 0071).

El equipo gobierna la política de memoria de las ejecuciones de sus proyectos
(resuelta por ``project.team_id``). NULLABLE: NULL = el equipo no fija política y
sus miembros caen al ``memory_scope`` del agente / default de plataforma. Aditiva
y reversible. String(32), mismo dominio que ``agents.memory_scope`` (sin CHECK:
el set canónico se valida en la capa de aplicación, igual que en el agente).

Revision ID: 0087_team_memory_scope
Revises: 0086_team_forked_from
Create Date: 2026-06-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0087_team_memory_scope"
down_revision: str | Sequence[str] | None = "0086_team_forked_from"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "teams",
        sa.Column("memory_scope", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("teams", "memory_scope")
