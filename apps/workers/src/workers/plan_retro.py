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
from uuid import uuid4

import structlog
from shared_domain.memory_tags import RETRO_TAG, retro_plan_tag

from workers.celery_app import app
from workers.config import get_settings
from workers.standup import _redact

_log = structlog.get_logger(__name__)

RETRO_WINDOW_HOURS = 48


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
class RedisRetroMarker:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def is_done(self, plan_id: str) -> bool:
        return bool(await self._redis.exists(f"retro:plan:{plan_id}"))

    async def mark(self, plan_id: str) -> None:
        await self._redis.set(f"retro:plan:{plan_id}", "1", ex=30 * 24 * 3600)


async def _load_recently_closed_plans(sessionmaker: Any) -> list[ClosedPlan]:
    from sqlalchemy import text as sa_text

    async with sessionmaker() as session:
        rows = await session.execute(
            sa_text(
                "SELECT id, tenant_id, project_id, title, status FROM plans"
                " WHERE status IN ('completed', 'cancelled')"
                "   AND updated_at >= now() - make_interval(hours => :h)"
            ),
            {"h": RETRO_WINDOW_HOURS},
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
        import json

        from sqlalchemy import text as sa_text

        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                sa_text(
                    "INSERT INTO memory_entries"
                    " (id, tenant_id, scope, type, content, project_id, tags)"
                    " VALUES (:id, :tid, 'project_shared', 'semantic', :content, :pid,"
                    "         CAST(:tags AS jsonb))"
                ),
                {
                    "id": str(uuid4()),
                    "tid": plan.tenant_id,
                    "content": content,
                    "pid": plan.project_id,
                    "tags": json.dumps([RETRO_TAG, retro_plan_tag(plan.plan_id)]),
                },
            )


@app.task(name="workers.plan_retro")  # type: ignore[untyped-decorator]
def plan_retro_task() -> dict[str, int]:
    """Beat cada 15 min: retro de los planes cerrados sin retro."""
    settings = get_settings()

    async def _main() -> dict[str, int]:
        from redis.asyncio import Redis
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from workers.memorizer import _default_llm_factory

        engine = create_async_engine(settings.database_url)
        redis = Redis.from_url(settings.events_redis_url, decode_responses=True)
        try:
            sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
            plans = await _load_recently_closed_plans(sessionmaker)
            return await _run_retros(
                plans=plans,
                marker=RedisRetroMarker(redis),
                collector=lambda p: _collect_plan_stats(sessionmaker, p),
                llm_factory=lambda _tid: _default_llm_factory(settings),
                persister=DbRetroPersister(sessionmaker),
            )
        finally:
            await redis.aclose()
            await engine.dispose()

    try:
        return asyncio.run(_main())
    except Exception:
        _log.exception("plan_retro.run_failed")
        return {"processed": 0, "skipped": 0}
