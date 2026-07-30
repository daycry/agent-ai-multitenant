"""Consolidación idempotente de memorias duplicadas EXACTAS (AUD16-18).

El dedup del persist (P1-2, 2026-07-12) previene duplicados NUEVOS, pero no
limpia los preexistentes: quedaron grupos de filas idénticas (mismo tenant,
scope, owner y contenido) sembrados por el mismo batch, y cada una quemaba un
slot del recall (límite 5) hasta que el cinturón de dedup del propio recall
los filtra. Esta consolidación soft-borra todas menos la MÁS ANTIGUA de cada
grupo. Se ejecuta manualmente en el deploy (o desde una shell del worker):

    python -c "import asyncio; from workers.maintenance.memory_dedup import run; asyncio.run(run())"

Nunca cruza scopes ni owners — el mismo texto en otro scope es otra memoria.
"""

from __future__ import annotations

from typing import Any, cast

import structlog
from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

_log = structlog.get_logger("workers.maintenance")

# Todas-menos-la-más-antigua por grupo exacto. COALESCE con el UUID cero para
# que los owners NULL agrupen entre sí (NULL != NULL en un PARTITION BY crudo
# no rompe, pero el cero-UUID lo hace explícito y legible en el plan).
_CONSOLIDATE_SQL = text(
    """
    WITH ranked AS (
        SELECT id,
               row_number() OVER (
                   PARTITION BY tenant_id,
                                scope,
                                COALESCE(user_id,    '00000000-0000-0000-0000-000000000000'::uuid),
                                COALESCE(team_id,    '00000000-0000-0000-0000-000000000000'::uuid),
                                COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid),
                                content
                   ORDER BY created_at ASC, id ASC
               ) AS rn
        FROM memory_entries
        WHERE deleted_at IS NULL
    )
    UPDATE memory_entries m
    SET deleted_at = now()
    FROM ranked r
    WHERE m.id = r.id AND r.rn > 1
    """
)


async def consolidate_exact_duplicate_memories(session: AsyncSession) -> int:
    """Soft-borra los duplicados exactos vivos; devuelve cuántas filas selló.

    Idempotente: una segunda pasada devuelve 0. Corre sobre la sesión admin
    BYPASSRLS del worker (opera cross-tenant a propósito — es mantenimiento
    de plataforma; el PARTITION BY tenant_id garantiza que jamás compara
    contenido entre tenants).
    """
    result = cast("CursorResult[Any]", await session.execute(_CONSOLIDATE_SQL))
    sealed = int(result.rowcount or 0)
    if sealed:
        _log.info("memory_dedup.consolidated", sealed=sealed)
    return sealed


async def run() -> int:
    """Entry point de deploy: engine propio, una transacción, dispose."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from workers.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            return await consolidate_exact_duplicate_memories(session)
    finally:
        await engine.dispose()


__all__ = ["consolidate_exact_duplicate_memories", "run"]
