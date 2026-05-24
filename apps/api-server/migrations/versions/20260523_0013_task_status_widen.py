"""tasks.status widened to VARCHAR(32) to hold `awaiting_human_approval` (ADR 0020).

Tras ADR 0020, una tarea entra explícitamente en
`awaiting_human_approval` (23 chars) cuando su ejecución se aparca por
una acción sensible. Espejo de lo que la migración 0012 hizo con
`executions.status`. Reversible: el downgrade trunca a 16 chars
(con USING substr) por si quedara algún registro con un valor que no
cabe — aceptable porque ese estado es siempre transitorio.

Revision ID: 0013_task_status_widen
Revises: 0012_approval_requests
Create Date: 2026-05-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_task_status_widen"
down_revision: str | Sequence[str] | None = "0012_approval_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# El trigger `trg_compute_task_ready` (migración 0009) está atado a la
# columna `status` (AFTER UPDATE OF status), y Postgres no deja alterar
# el tipo de una columna mientras un trigger la referencia: hay que
# tirarlo, ampliar la columna y recrearlo. La definición que se recrea
# es bit-a-bit la de 0009 — si esa cambia en el futuro, hay que actualizar
# este SQL en consecuencia.
_RECREATE_TRIGGER = """
CREATE TRIGGER trg_compute_task_ready
    AFTER UPDATE OF status ON tasks
    FOR EACH ROW
    WHEN (NEW.status = 'done' AND OLD.status IS DISTINCT FROM 'done')
    EXECUTE FUNCTION fn_compute_task_ready();
"""


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_compute_task_ready ON tasks")
    op.alter_column(
        "tasks",
        "status",
        type_=sa.String(length=32),
        existing_type=sa.String(length=16),
        existing_nullable=False,
        existing_server_default=sa.text("'backlog'"),
    )
    op.execute(_RECREATE_TRIGGER)


def downgrade() -> None:
    # Si alguna tarea quedara aparcada en `awaiting_human_approval` al
    # revertir, truncar a 16 chars la dejaría con un valor inválido —
    # devolverla a `in_progress` es la decisión menos sorprendente
    # (era su estado previo antes de aparcarse).
    op.execute("UPDATE tasks SET status = 'in_progress' WHERE status = 'awaiting_human_approval'")
    op.execute("DROP TRIGGER IF EXISTS trg_compute_task_ready ON tasks")
    op.alter_column(
        "tasks",
        "status",
        type_=sa.String(length=16),
        existing_type=sa.String(length=32),
        existing_nullable=False,
        existing_server_default=sa.text("'backlog'"),
        postgresql_using="substr(status, 1, 16)",
    )
    op.execute(_RECREATE_TRIGGER)
