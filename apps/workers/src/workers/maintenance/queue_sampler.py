"""Queue/status metrics sampler — `workers.sample_queue_metrics`, every 30s
(prod-06 task_prod06_dag_03, parte B). Best-effort: never crashes beat.

The textfile WRITER lives in :mod:`workers.queue_metrics`; this module only
samples (Redis LLEN per queue + a tasks GROUP BY) and delegates the write.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import structlog
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.maintenance")


@app.task(name="workers.sample_queue_metrics")  # type: ignore[untyped-decorator]
def sample_queue_metrics() -> dict[str, Any]:
    """Sample Celery queue depth + task counts per status and write the
    node-exporter textfile (prod-06 task_prod06_dag_03).

    Emits ``agentic_celery_queue_depth{queue}`` (Redis LLEN per Celery queue) and
    ``agentic_tasks_by_status{status}`` (non-deleted tasks per lifecycle status,
    all tenants). prod-08 owns the scrape job + CeleryQueueGrowing alert + the
    dashboard; this only EMITS. Cheap (one LLEN per queue + one GROUP BY) and
    best-effort (a sampling failure never crashes beat)."""
    return asyncio.run(_sample_queue_metrics_async(get_settings()))


async def _collect_queue_depths(redis: Any, queue_names: tuple[str, ...]) -> dict[str, int]:
    """Redis ``LLEN`` per Celery queue (a queue is a Redis list under its name)."""
    depths: dict[str, int] = {}
    for name in queue_names:
        with contextlib.suppress(Exception):  # a missing key LLENs to 0; other errors skip
            depths[name] = int(await redis.llen(name))
    return depths


async def _collect_status_counts(session: Any) -> dict[str, int]:
    """Count ``tasks`` rows grouped by lifecycle status (all tenants — the worker
    engine is BYPASSRLS). ``tasks`` is not soft-deletable (no ``deleted_at``)."""
    rows = await session.execute(sa_text("SELECT status, count(*) FROM tasks GROUP BY status"))
    return {str(status): int(count) for status, count in rows.all()}


async def _sample_queue_metrics_async(
    settings: Settings,
    *,
    redis: Any | None = None,
) -> dict[str, Any]:
    """Async core — owns the redis + engine lifecycle. ``redis`` is injectable for
    tests. Always writes the textfile (even if a collector fails → that dimension
    is simply absent), so the file reflects the freshest successful sample."""
    from redis.asyncio import Redis

    from workers.celery_app import QUEUE_NAMES
    from workers.queue_metrics import write_queue_metrics

    own_redis = redis is None
    redis_client = redis if redis is not None else Redis.from_url(settings.broker_url)
    engine = create_async_engine(settings.database_url)
    queue_depths: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    try:
        queue_depths = await _collect_queue_depths(redis_client, QUEUE_NAMES)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            status_counts = await _collect_status_counts(db)
    except Exception as exc:  # pragma: no cover — best-effort: never crash beat
        _log.warning("maintenance.sample_queue_metrics.error", error=str(exc))
    finally:
        await engine.dispose()
        if own_redis:
            with contextlib.suppress(Exception):
                await redis_client.aclose()

    written = write_queue_metrics(
        settings.queue_metrics_textfile_path,
        queue_depths=queue_depths,
        status_counts=status_counts,
    )
    _log.info(
        "maintenance.sample_queue_metrics.done",
        queues=len(queue_depths),
        statuses=len(status_counts),
        written=written,
    )
    return {"queue_depths": queue_depths, "status_counts": status_counts, "written": written}
