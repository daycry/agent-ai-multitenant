"""executions.container_launched_at — marca cuándo el contenedor pasó a existir (M1)

El sweep de huérfanos (workers.maintenance) confundía dos estados: (a) el
contenedor se lanzó y desapareció (engine-restart / rm externo → reap legítimo) y
(b) el contenedor AÚN NO existe porque el run sigue provisionando (pull en frío de
la imagen, checkout git grande, Vault lento). Con una gracia fija de 5 min, un run
cuya provisión supera ese umbral se marcaba failed(stale_after_worker_loss) y su
resultado sano se descartaba al finalizar (guarda idempotente F46/F52).

Esta columna nullable marca el instante del `docker create`; el sweep solo trata
como huérfana una fila con ``container_launched_at`` no NULL (sí tuvo contenedor).
Una fila todavía en provisión (NULL) queda protegida del reap temprano y solo cae
por el umbral conservador de edad (7 h). Aditiva y reversible.

Revision ID: 0104_execution_container_launched
Revises: 0103_cortex_pursuit_surfaced
Create Date: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0104_exec_container_launched"
down_revision: str | Sequence[str] | None = "0103_cortex_pursuit_surfaced"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column("container_launched_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("executions", "container_launched_at")
