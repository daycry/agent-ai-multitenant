"""DAG-promotion safety net — `workers.promote_ready_plans`, every 30s
(prod-06 task_prod06_dag_02). Best-effort: never crashes beat.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.maintenance")


@app.task(name="workers.promote_ready_plans")  # type: ignore[untyped-decorator]
def promote_ready_plans() -> dict[str, Any]:
    """Safety-net DAG promotion: across every ``in_progress`` plan, promote
    eligible ``backlog`` tasks to ``ready`` and announce the undispatched ones.

    The instant path is ``start-execution`` (roots) + the DB trigger (dependents),
    but the trigger flips status WITHOUT publishing the event the dispatcher
    consumes, and an event can be lost — so a ready task could sit undispatched.
    This beat reconciles every 30s: it re-announces any ``ready`` task of an
    in-progress plan with no execution row. Idempotent (a dispatched task is
    skipped) and best-effort (never crashes beat)."""
    settings = get_settings()
    return asyncio.run(_promote_ready_plans_async(settings))


async def _promote_ready_plans_async(settings: Settings) -> dict[str, Any]:
    """Async core — owns the engine + redis lifecycle."""
    from api_server.dag_promotion import announce_ready_tasks, promote_ready_tasks
    from api_server.db.domain import Plan, PlanStatus
    from redis.asyncio import Redis
    from sqlalchemy import select

    engine = create_async_engine(settings.database_url)
    redis = Redis.from_url(settings.events_redis_url)
    plans_touched = 0
    promoted = 0
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            plan_ids = list(
                (
                    await db.execute(
                        select(Plan.id).where(Plan.status == PlanStatus.IN_PROGRESS.value)
                    )
                ).scalars()
            )
        for plan_id in plan_ids:
            async with sessionmaker() as db, db.begin():
                announced = await promote_ready_tasks(db, plan_id)
            if announced:
                plans_touched += 1
                promoted += len(announced)
                await announce_ready_tasks(redis, announced)
    except Exception as exc:  # pragma: no cover — defensive logging
        _log.warning("maintenance.promote_ready_plans.error", error=str(exc))
        return {"plans_touched": plans_touched, "promoted": promoted, "error": str(exc)}
    finally:
        await engine.dispose()
        with contextlib.suppress(Exception):
            await redis.aclose()

    _log.info(
        "maintenance.promote_ready_plans.done",
        plans_touched=plans_touched,
        promoted=promoted,
    )
    return {"plans_touched": plans_touched, "promoted": promoted}
