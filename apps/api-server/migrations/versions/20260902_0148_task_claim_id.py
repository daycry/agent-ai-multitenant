"""`tasks.claim_id`: la reclamación de una tarea viaja con identidad (`task_cv_13`).

Auditoría 2026-09-01 (A-05). El dispatch reclama la tarea (`ready → in_progress`)
y encola el run; si la cola va con retraso, el reconciler V-1 revierte la
reclamación a los 30 minutos y la tarea se vuelve a despachar: dos mensajes para
la misma tarea, y el viejo —sin el feedback del rechazo— puede ganar. Con esta
columna el dispatch escribe un `claim_id` nuevo en cada reclamación y lo manda en
el `ExecutionRequest`; el worker descarta todo mensaje cuyo `claim_id` no sea el
vigente, antes de tocar nada.

Columna nullable sin backfill: una tarea `in_progress` reclamada antes de esta
migración no tiene `claim_id`, y su mensaje tampoco lo lleva, así que el worker
la trata como antes (compatibilidad durante el despliegue: primero el worker,
después el orquestador). El `downgrade` la retira.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0148_task_claim_id"
down_revision: str | Sequence[str] | None = "0147_unpin_anthropic_builtin_forks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("claim_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "claim_id")
