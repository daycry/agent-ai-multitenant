"""teams.chat_model_config + projects.chat_model_config — modelo del chat de proyecto.

El chat de planificación es interactivo y conviene poder usar un modelo más
rápido/ligero que el (potente pero lento) que los agentes usan para ejecutar
tareas reales. Esta columna fija el modelo SOLO para el chat, independiente del
``model_config`` de ejecución. JSONB ``{}`` = el chat hereda el ``model_config``
de ejecución (cadena ADR 0065 plataforma → proyecto → equipo). El override de
proyecto gana sobre el de equipo.

Aditiva y reversible: ``upgrade`` añade ambas columnas NOT NULL con default
``'{}'::jsonb`` (filas existentes = sin modelo de chat fijado, heredan como hasta
ahora); ``downgrade`` las retira. Las tablas tienen RLS (TenantScopedMixin); la
columna no cambia las políticas.

Revision ID: 0089_chat_model_config
Revises: 0088_project_git_config
Create Date: 2026-06-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0089_chat_model_config"
down_revision: str | Sequence[str] | None = "0088_project_git_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("teams", "projects"):
        op.add_column(
            table,
            sa.Column(
                "chat_model_config",
                JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )


def downgrade() -> None:
    for table in ("projects", "teams"):
        op.drop_column(table, "chat_model_config")
