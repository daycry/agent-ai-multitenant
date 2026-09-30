"""Uso de las memorias: contador, última recuperación y log (task_mem_02).

Cada hit de ``memory_recall`` (``kind='index'``) y cada memoria traída por
``memory_get`` (``kind='detail'``) deja huella:

  - ``memory_entries.recall_count`` / ``last_recalled_at``: cuántos RUNS la
    recuperaron y cuándo fue la última vez;
  - una fila en ``memory_recall_log`` (append-only, particionada por mes — ADR
    0151) con quién y en qué ejecución.

Idempotente por run: dentro de una misma ejecución una memoria cuenta UNA vez en
``recall_count`` aunque se recupere varias veces o por las dos vías (el agente
repite búsquedas; sin esto el contador mediría insistencia, no utilidad). El log
sí registra cada vía (una fila por memoria, ejecución y ``kind``).

**Best-effort**: el uso es telemetría y JAMÁS rompe un recall (regla del plan y de
CLAUDE.md). Todo va dentro de un SAVEPOINT: si algo falla, se deshace sólo esto y
la transacción del recall sigue viva.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

RecallKind = Literal["index", "detail"]


@dataclass(frozen=True)
class UsagePlan:
    """Qué contar y qué registrar, ya sin duplicados y en orden de aparición."""

    to_count: list[UUID]
    to_log: list[UUID]


def plan_usage(memory_ids: Iterable[UUID], *, already_used_in_run: set[UUID] | None) -> UsagePlan:
    """Decisión pura de idempotencia por run.

    ``already_used_in_run`` son las memorias que este run ya recuperó (por
    cualquier vía). ``None`` = no se conoce el run: no hay base para deduplicar y
    cada llamada cuenta.
    """
    distinct = list(dict.fromkeys(memory_ids))
    used = already_used_in_run or set()
    return UsagePlan(to_count=[m for m in distinct if m not in used], to_log=distinct)


async def record_recall_usage(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    memory_ids: Sequence[UUID],
    kind: RecallKind,
    agent_id: UUID | None,
    execution_id: UUID | None,
) -> None:
    """Registra el uso de ``memory_ids``. Nunca lanza: un fallo se loguea y se olvida."""
    if not memory_ids:
        return
    try:
        async with session.begin_nested():
            already_used: set[UUID] | None = None
            already_same_kind: set[UUID] = set()
            if execution_id is not None:
                rows = await session.execute(
                    text(
                        "SELECT memory_id, kind FROM memory_recall_log"
                        " WHERE tenant_id = :tenant_id AND execution_id = :execution_id"
                        "   AND memory_id = ANY(:ids)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "execution_id": execution_id,
                        "ids": list(memory_ids),
                    },
                )
                seen = rows.all()
                already_used = {r[0] for r in seen}
                already_same_kind = {r[0] for r in seen if r[1] == kind}
            plan = plan_usage(memory_ids, already_used_in_run=already_used)

            await session.execute(
                text(
                    "UPDATE memory_entries SET"
                    " recall_count = recall_count"
                    "   + CASE WHEN id = ANY(:count_ids) THEN 1 ELSE 0 END,"
                    " last_recalled_at = now()"
                    " WHERE tenant_id = :tenant_id AND id = ANY(:ids)"
                ),
                {
                    "tenant_id": tenant_id,
                    "ids": plan.to_log,
                    "count_ids": plan.to_count,
                },
            )
            to_log = [m for m in plan.to_log if m not in already_same_kind]
            if to_log:
                await session.execute(
                    text(
                        "INSERT INTO memory_recall_log"
                        " (id, tenant_id, memory_id, execution_id, agent_id, kind)"
                        " SELECT gen_random_uuid(), :tenant_id, m, :execution_id,"
                        "        :agent_id, :kind"
                        " FROM unnest(CAST(:ids AS uuid[])) AS m"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "execution_id": execution_id,
                        "agent_id": agent_id,
                        "kind": kind,
                        "ids": to_log,
                    },
                )
    except Exception as exc:  # best-effort: la telemetría nunca rompe el recall
        logger.warning("memory.recall_usage_failed", error=str(exc), kind=kind)


__all__ = ["RecallKind", "UsagePlan", "plan_usage", "record_recall_usage"]
