"""executions: guía humana sobre un run EN MARCHA (`task_wf_71`).

La única intervención posible sobre un run en vuelo era **matarlo**: si el
agente iba por mal camino, se tiraba todo el trabajo hecho y se relanzaba a
ciegas, con el mismo prompt que ya había fallado.

Esta columna es el canal para redirigirlo. Un humano escribe la guía desde el
visor del run; el bucle del agente la consulta una vez por iteración por la API
interna (el canal que ya existe) y la inyecta como sticky del turno siguiente,
igual que el feedback de review.

Se BORRA al entregarla — de ahí `pending_`. Es una intervención puntual: dejarla
puesta la repetiría en cada turno y el agente acabaría obedeciendo una
corrección que ya aplicó.

Revision ID: 0121_execution_pending_guidance
Revises: 0120_execution_runtime_digest
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0121_execution_pending_guidance"
down_revision: str | Sequence[str] | None = "0120_execution_runtime_digest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("executions", sa.Column("pending_guidance", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("executions", "pending_guidance")
