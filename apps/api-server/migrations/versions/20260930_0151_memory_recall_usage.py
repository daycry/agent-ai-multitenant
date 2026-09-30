"""Uso de las memorias: `recall_count`, `last_recalled_at` y `memory_recall_log` (task_mem_02).

ADITIVA y reversible. Tres cambios:

1. ``memory_entries.recall_count`` (``integer NOT NULL DEFAULT 0``) y
   ``memory_entries.last_recalled_at`` (``timestamptz`` NULL). ``ADD COLUMN`` con
   DEFAULT constante no reescribe la tabla en PostgreSQL 11+.
2. ``memory_recall_log`` — quién recuperó qué memoria, en qué ejecución y por qué
   vía (``index`` = hit de ``memory_recall``, ``detail`` = ``memory_get``).
   Append-only y **particionada por mes desde su nacimiento** (misma familia que
   las cinco del ADR 0151: no se borra nada, así que no hay plazo que acertar).
   PK ``(id, created_at)`` porque PostgreSQL exige la clave de partición en la PK.
   Sin FK a ``memory_entries`` / ``executions`` / ``agents`` a propósito (ADR
   0154): es un log, no debe impedir ni arrastrar el borrado de lo que cita.
3. RLS ``FOR ALL`` por ``tenant_id`` en el padre y en CADA partición (una consulta
   directa a una partición sólo pasa por las policies de esa relación). El job
   ``workers.ensure_partitions`` la conoce (``PARTITIONED_TABLES``) y crea el mes
   siguiente; esta migración crea el mes en curso más tres de colchón, igual que
   las conversiones del ADR 0151, y no depende del código de los workers.

Revision ID: 0151_memory_recall_usage
Revises: 0150_documents_kb_created_index
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0151_memory_recall_usage"
down_revision: str | Sequence[str] | None = "0150_documents_kb_created_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "memory_recall_log"

#: Meses de colchón hacia adelante (= ``PARTITION_HEADROOM_MONTHS`` del job; no se
#: importa: una migración no puede depender del código de otro despliegue).
HEADROOM_MONTHS = 3

#: El predicado canónico de aislamiento por tenant, LITERAL de la migración 0052.
_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _add_months(start: date, months: int) -> date:
    total = (start.year * 12 + (start.month - 1)) + months
    return date(total // 12, total % 12 + 1, 1)


def _partition_name(first_of_month: date) -> str:
    return f"{TABLE}_{first_of_month.year:04d}_{first_of_month.month:02d}"


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column(
        "memory_entries",
        sa.Column("recall_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "memory_entries",
        sa.Column("last_recalled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.execute(
        f"""
        CREATE TABLE {TABLE} (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            memory_id uuid NOT NULL,
            execution_id uuid,
            agent_id uuid,
            kind varchar(8) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_{TABLE} PRIMARY KEY (id, created_at),
            CONSTRAINT ck_{TABLE}_kind CHECK (kind IN ('index', 'detail'))
        ) PARTITION BY RANGE (created_at)
        """
    )
    # El «ahora» se pide a PostgreSQL (el reloj que pone el DEFAULT now()).
    present = bind.execute(sa.text("SELECT now()")).scalar_one()
    first = date(present.year, present.month, 1)
    months = [_add_months(first, i) for i in range(HEADROOM_MONTHS + 1)]
    for month in months:
        start = month.isoformat()
        end = _add_months(month, 1).isoformat()
        op.execute(
            f"CREATE TABLE {_partition_name(month)} PARTITION OF {TABLE}"
            f" FOR VALUES FROM ('{start}') TO ('{end}')"
        )
    op.execute(
        f"CREATE INDEX ix_{TABLE}_tenant_memory ON {TABLE} (tenant_id, memory_id, created_at)"
    )
    op.execute(
        f"CREATE INDEX ix_{TABLE}_tenant_execution ON {TABLE} (tenant_id, execution_id)"
        " WHERE execution_id IS NOT NULL"
    )
    for relation in [TABLE, *[_partition_name(m) for m in months]]:
        policy = f"{relation}_tenant_isolation"
        op.execute(f"ALTER TABLE {relation} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {relation} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {policy} ON {relation} FOR ALL"
            f" USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
        )


def downgrade() -> None:
    # Tirar la padre se lleva sus particiones (y sus índices y policies).
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
    op.drop_column("memory_entries", "last_recalled_at")
    op.drop_column("memory_entries", "recall_count")
