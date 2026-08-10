"""Particiones mensuales para las filas **retrofechadas** que siembran los tests.

Las cinco tablas append-only del ADR 0151 (``guardrail_events``,
``notification_logs``, ``llm_usage_events``, ``audit_log``, ``executions``) están
particionadas por mes y **no tienen partición ``DEFAULT``**, decisión deliberada
de `workers/maintenance/partitions.py`. La cobertura que crean las migraciones en
una base recién levantada es «mes en curso + 3 por delante»: no hay nada detrás,
porque no hay datos detrás.

Eso choca con una siembra legítima muy común: un test de ventana temporal necesita
una fila **fuera** de la ventana para comprobar que queda excluida, y la escribe
con `now - 100 días`. El INSERT muere así:

    asyncpg.exceptions.CheckViolationError: no partition of relation
    "executions" found for row
    DETAIL: Partition key of the failing row contains (created_at) = (2026-05-04…)

El error no menciona ni el test ni el particionado, y aparece meses después de la
migración, cuando alguien toca el test por otro motivo.

Por qué el DDL sale de producción y no se escribe aquí a mano
-------------------------------------------------------------
`partition_statements()` no sólo crea la partición: le activa RLS, la fuerza y le
engancha la policy de aislamiento por tenant. Una partición creada a mano con un
``CREATE TABLE … PARTITION OF`` suelto **sería legible entre tenants**, y los
tests que sembrasen en ella pasarían el aislamiento por no tenerlo activado —
justo el modo de fallo que los tests cross-tenant existen para cazar. Se reusa la
función de producción para que el arnés no pueda ser más laxo que el sistema.
"""

from __future__ import annotations

from datetime import date, datetime

import asyncpg
from workers.maintenance.partitions import (
    PartitionSpec,
    add_months,
    month_start,
    partition_name,
    partition_statements,
)

__all__ = ["ensure_partition_for"]


async def ensure_partition_for(dsn: str, table: str, moment: datetime | date) -> str:
    """Crea (si falta) la partición mensual de ``table`` que aceptaría ``moment``.

    Idempotente: el DDL de producción es ``CREATE TABLE IF NOT EXISTS`` y
    ``DROP POLICY IF EXISTS`` + ``CREATE POLICY``, así que llamarla dos veces con
    el mismo mes no rompe. Devuelve el nombre de la partición.

    ``dsn`` tiene que ser el del **superusuario/owner**: crear una partición es
    DDL sobre la tabla padre, que ``app_user`` no puede (ni debe) hacer.
    """
    start = month_start(moment)
    spec = PartitionSpec(
        table=table,
        name=partition_name(table, start),
        start=start,
        end=add_months(start, 1),
    )
    conn = await asyncpg.connect(dsn)
    try:
        # Una transacción, igual que en producción: una partición creada pero
        # todavía sin policy es una ventana sin aislamiento entre tenants.
        async with conn.transaction():
            for statement in partition_statements(spec):
                await conn.execute(statement)
    finally:
        await conn.close()
    return spec.name
