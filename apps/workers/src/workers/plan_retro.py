"""Retrospectiva automática de planes (ADR 0124).

Al cerrarse un plan (``completed``/``cancelled``), el sistema destila lo
aprendido — tareas hechas/canceladas, runs, escalados, abortos, coste,
duración y una lección redactada — y lo persiste como memoria
``project_shared``: los agentes del SIGUIENTE plan del proyecto la
recuerdan vía el recall normal. Cierra el bucle de aprendizaje a nivel de
plan (a nivel de run ya memorizamos fracasos, AUD16-17).

El beat ``workers.plan_retro`` (cada 15 min) barre los planes cerrados en
las últimas 48 h sin retro. Idempotencia SIN migración: un marker en el
Redis del worker (``retro:plan:<id>``, TTL 30 días). El LLM solo REDACTA la
lección sobre los datos SQL (fail-open: sin LLM, la retro estructurada se
persiste igual — nunca se pierde). La memoria se inserta con ``embedding``
NULL: el back-fill de embeddings existente la indexa en su siguiente pasada.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from shared_domain.memory_tags import RETRO_TAG, retro_plan_tag

from workers.celery_app import app
from workers.config import get_settings
from workers.standup import _redact

_log = structlog.get_logger(__name__)

#: Prefijo del tag por plan (`retro_plan_tag`), para el NOT EXISTS de la selección.
_PLAN_TAG_PREFIX = retro_plan_tag("")

RETRO_WINDOW_HOURS = 48  # histórico; ver RETRO_LOOKBACK_DAYS (`task_cv_45`)
#: `task_cv_45` (G-12): la selección ya no es «cerrados en 48 h» sino «cerrados
#: en los últimos N días SIN retro persistida». Beat parado un fin de semana
#: largo ya no deja planes sin retro para siempre.
RETRO_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class ClosedPlan:
    plan_id: str
    tenant_id: str
    project_id: str
    title: str
    status: str


@dataclass(frozen=True)
class PlanStats:
    tasks_total: int
    tasks_done: int
    tasks_cancelled: int
    runs_total: int
    runs_escalated: int
    runs_aborted: int
    total_cost_usd: float
    duration_hours: float


class RetroMarker(Protocol):
    async def is_done(self, plan_id: str) -> bool: ...
    async def mark(self, plan_id: str) -> None: ...


class RetroPersister(Protocol):
    async def save(self, *, plan: ClosedPlan, content: str) -> None: ...


def _format_retro(plan: ClosedPlan, stats: PlanStats) -> str:
    """La retro SIN LLM: determinista y completa — base factual y fail-open."""
    return (
        f"Retrospectiva del plan «{plan.title}» ({plan.status})\n"
        f"- Tareas: {stats.tasks_done}/{stats.tasks_total} hechas, "
        f"{stats.tasks_cancelled} canceladas\n"
        f"- Runs: {stats.runs_total} en total, {stats.runs_escalated} escalados "
        f"a humano, {stats.runs_aborted} abortados\n"
        f"- Coste LLM total: ${stats.total_cost_usd:.2f}\n"
        f"- Duración: {stats.duration_hours:.1f} h"
    )


async def _run_retros(
    *,
    plans: list[ClosedPlan],
    marker: RetroMarker,
    collector: Callable[[ClosedPlan], Awaitable[PlanStats]],
    llm_factory: Callable[[str], Any],
    persister: RetroPersister,
) -> dict[str, int]:
    """Una pasada: cada plan cerrado sin retro → stats → lección → memoria.

    Un plan que falla se salta con log y no frena a los demás; el marker se
    escribe DESPUÉS de persistir (un fallo a mitad reintenta en la próxima
    pasada — el insert duplicado es inofensivo frente a una retro perdida).
    """
    processed = 0
    skipped = 0
    for plan in plans:
        try:
            if await marker.is_done(plan.plan_id):
                skipped += 1
                continue
            stats = await collector(plan)
            structured = _format_retro(plan, stats)
            content = await _redact(llm_factory(plan.tenant_id), structured)
            if content != structured:
                content = f"{structured}\n\nLección del PM: {content}"
            await persister.save(plan=plan, content=content)
            await marker.mark(plan.plan_id)
            processed += 1
        except Exception:
            _log.exception("plan_retro.failed", plan_id=plan.plan_id)
            skipped += 1
    return {"processed": processed, "skipped": skipped}


# ---------------------------------------------------------------------------
# Cableado real (SQL + Redis + Celery) — integración.
# ---------------------------------------------------------------------------
class DbRetroMarker:
    """`task_cv_45` (G-12): hecho = hay una retro con el tag del plan en
    `memory_entries`. Sobrevive a un Redis restaurado y no caduca."""

    def __init__(self, sessionmaker: Any) -> None:
        self._sessionmaker = sessionmaker

    async def is_done(self, plan_id: str) -> bool:
        from sqlalchemy import text as sa_text

        async with self._sessionmaker() as session:
            result = await session.execute(
                sa_text("SELECT 1 FROM memory_entries WHERE jsonb_exists(tags, :tag) LIMIT 1"),
                {"tag": retro_plan_tag(plan_id)},
            )
            return result.scalar() is not None

    async def mark(self, _plan_id: str) -> None:
        return None  # la fila persistida ES la marca


class RedisRetroMarker:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def is_done(self, plan_id: str) -> bool:
        return bool(await self._redis.exists(f"retro:plan:{plan_id}"))

    async def mark(self, plan_id: str) -> None:
        await self._redis.set(f"retro:plan:{plan_id}", "1", ex=30 * 24 * 3600)


async def _load_closed_plans_without_retro(sessionmaker: Any) -> list[ClosedPlan]:
    """Planes cerrados en los últimos `RETRO_LOOKBACK_DAYS` días que aún no
    tienen su retro en `memory_entries` (`task_cv_45`, G-12): la idempotencia
    es por tag `plan:<id>` en la BD, no por un marker en Redis."""
    from sqlalchemy import text as sa_text

    async with sessionmaker() as session:
        rows = await session.execute(
            sa_text(
                "SELECT p.id, p.tenant_id, p.project_id, p.title, p.status FROM plans p"
                " WHERE p.status IN ('completed', 'cancelled')"
                "   AND p.deleted_at IS NULL"
                "   AND p.updated_at >= now() - make_interval(days => :d)"
                "   AND NOT EXISTS ("
                "     SELECT 1 FROM memory_entries m"
                "      WHERE m.tenant_id = p.tenant_id"
                "        AND jsonb_exists(m.tags, :prefix || p.id::text))"
            ),
            {"d": RETRO_LOOKBACK_DAYS, "prefix": _PLAN_TAG_PREFIX},
        )
        return [
            ClosedPlan(
                plan_id=str(r[0]),
                tenant_id=str(r[1]),
                project_id=str(r[2]),
                title=str(r[3]),
                status=str(r[4]),
            )
            for r in rows.fetchall()
        ]


async def _collect_plan_stats(sessionmaker: Any, plan: ClosedPlan) -> PlanStats:
    from sqlalchemy import text as sa_text

    async with sessionmaker() as session:
        row = (
            await session.execute(
                sa_text(
                    "SELECT"
                    " count(*) AS tasks_total,"
                    " count(*) FILTER (WHERE t.status = 'done') AS tasks_done,"
                    " count(*) FILTER (WHERE t.status = 'cancelled') AS tasks_cancelled"
                    " FROM tasks t WHERE t.plan_id = :pid"
                ),
                {"pid": plan.plan_id},
            )
        ).one()
        runs = (
            await session.execute(
                sa_text(
                    "SELECT count(*),"
                    " count(*) FILTER (WHERE e.status IN"
                    "   ('needs_human_review', 'awaiting_human_approval')),"
                    " count(*) FILTER (WHERE e.status = 'aborted'),"
                    " coalesce(sum(e.total_cost_usd), 0),"
                    " coalesce(extract(epoch FROM (max(e.updated_at) - min(e.created_at))), 0)"
                    " FROM executions e JOIN tasks t ON t.id = e.task_id"
                    " WHERE t.plan_id = :pid"
                ),
                {"pid": plan.plan_id},
            )
        ).one()
        return PlanStats(
            tasks_total=int(row[0]),
            tasks_done=int(row[1]),
            tasks_cancelled=int(row[2]),
            runs_total=int(runs[0]),
            runs_escalated=int(runs[1]),
            runs_aborted=int(runs[2]),
            total_cost_usd=float(runs[3]),
            duration_hours=float(runs[4]) / 3600.0,
        )


class DbRetroPersister:
    """Inserta la retro como memoria semántica `project_shared` del proyecto.

    ``embedding`` queda NULL a propósito: el back-fill de embeddings del beat
    existente (Plan 06.17) la indexa en su siguiente pasada.
    """

    def __init__(self, sessionmaker: Any) -> None:
        self._sessionmaker = sessionmaker

    async def save(self, *, plan: ClosedPlan, content: str) -> None:
        """`task_cv_45` (E-06): por la persistencia común del memorizer —
        `metadata`, dedup, `tenant_id` en la fila— en vez de un INSERT crudo."""
        from uuid import UUID

        from api_server.memorizer import persistence
        from api_server.memorizer.distillation import MemoryCandidate

        candidate = MemoryCandidate(
            content=content,
            type="semantic",
            tags=(RETRO_TAG, retro_plan_tag(plan.plan_id)),
        )
        async with self._sessionmaker() as session, session.begin():
            await persistence.persist_memory_candidates(
                session,
                [candidate],
                tenant_id=UUID(plan.tenant_id),
                scope="project_shared",
                project_id=UUID(plan.project_id),
                extra_metadata={"source": "plan_retro", "plan_id": plan.plan_id},
            )


@app.task(name="workers.plan_retro")  # type: ignore[untyped-decorator]
def plan_retro_task() -> dict[str, int]:
    """Beat cada 15 min: retro de los planes cerrados sin retro."""
    settings = get_settings()

    async def _main() -> dict[str, int]:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from workers.db import worker_engine
        from workers.memorizer import _default_llm_factory

        engine = worker_engine(settings)
        try:
            sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
            plans = await _load_closed_plans_without_retro(sessionmaker)
            return await _run_retros(
                plans=plans,
                marker=DbRetroMarker(sessionmaker),
                collector=lambda p: _collect_plan_stats(sessionmaker, p),
                llm_factory=lambda _tid: _default_llm_factory(settings),
                persister=DbRetroPersister(sessionmaker),
            )
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_main())
    except Exception:
        _log.exception("plan_retro.run_failed")
        return {"processed": 0, "skipped": 0}
