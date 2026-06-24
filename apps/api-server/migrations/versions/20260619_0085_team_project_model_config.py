"""teams.model_config + projects.model_config — herencia de modelo (Ola A / ADR 0055).

Añade el default de modelo a nivel de EQUIPO y de PROYECTO, para la cadena de
herencia plataforma → proyecto → equipo → agente (gana el más específico que
pinee provider+model). Aditiva y reversible: ``upgrade`` añade ambas columnas
NOT NULL con default ``'{}'::jsonb`` (filas existentes = sin modelo fijado, que
heredan como hasta ahora); ``downgrade`` las retira. Las tablas tienen RLS
(TenantScopedMixin); la columna no cambia las políticas.

Revision ID: 0085_team_project_model_config
Revises: 0084_memory_entities
Create Date: 2026-06-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0085_team_project_model_config"
down_revision: str | Sequence[str] | None = "0084_memory_entities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("teams", "projects"):
        op.add_column(
            table,
            sa.Column(
                "model_config",
                JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )


def downgrade() -> None:
    for table in ("projects", "teams"):
        op.drop_column(table, "model_config")
