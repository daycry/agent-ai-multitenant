"""Queue/status metrics sampler — `workers.sample_queue_metrics`, every 30s
(prod-06 task_prod06_dag_03, parte B). Best-effort: never crashes beat.

The textfile WRITER lives in :mod:`workers.queue_metrics`; this module only
samples (Redis LLEN per queue + a tasks GROUP BY) and delegates the write.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import structlog
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine

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


# Streams DLQ conocidos (dispatcher de notificaciones), en el events redis.
_DLQ_STREAMS: tuple[str, ...] = ("dlq:notifications",)


async def _collect_dlq_depths(redis: Any, streams: tuple[str, ...]) -> dict[str, int]:
    """Redis ``XLEN`` por stream DLQ (stream ausente = 0)."""
    depths: dict[str, int] = {}
    for name in streams:
        with contextlib.suppress(Exception):
            depths[name] = int(await redis.xlen(name))
    return depths


async def _collect_celery_task_counters(
    redis: Any,
) -> tuple[dict[tuple[str, str], int], dict[str, float]]:
    """Contadores de RESULTADO de las tareas Celery, acumulados por las señales.

    Las señales (`workers/task_metrics.py`) corren en cada proceso hijo del pool
    prefork, así que acumulan en Redis; aquí se leen y se publican una sola vez
    por pasada. Sin esto, `agentic_celery_queue_depth` deja un punto ciego: un
    worker que drena la cola fallando el 100% de las tareas se ve exactamente
    igual que uno sano.
    """
    from workers.task_metrics import DURATION_HASH_KEY, TASKS_HASH_KEY, parse_task_counters

    raw_counts = await redis.hgetall(TASKS_HASH_KEY)
    raw_durations = await redis.hgetall(DURATION_HASH_KEY)
    return parse_task_counters(raw_counts, raw_durations)


async def _collect_memorizer_failures(redis: Any) -> int:
    """Destilaciones del Memorizer fallidas SEGUIDAS (0 = sano).

    La memorización es best-effort y se traga sus excepciones para no tumbar el
    pipeline; sin este gauge, un destilador caído solo se nota en que nadie
    aprende nada. Lo escriben los N hijos del prefork en el Redis del broker
    (`workers/memorizer_metrics.py`).
    """
    from workers.memorizer_metrics import read_consecutive_failures

    return await read_consecutive_failures(redis)


async def _collect_execution_counts(session: Any) -> dict[str, int]:
    """Ejecuciones por estado en las últimas 24h — la actividad real de runs
    que el dashboard «Plataforma Agéntica» muestra como pulso del sistema."""
    rows = await session.execute(
        sa_text(
            "SELECT status, count(*) FROM executions"
            " WHERE created_at > now() - interval '24 hours' GROUP BY status"
        )
    )
    return {str(status): int(count) for status, count in rows.all()}


async def _collect_approval_metrics(session: Any) -> tuple[int, float]:
    """Aprobaciones humanas pendientes: cuántas y qué edad tiene la más vieja.

    Cuando un agente pide aprobación su ejecución se DETIENE. Una petición
    olvidada no genera error, ni log de fallo, ni cola creciendo: el plan
    simplemente no avanza. Y el contador solo no basta — tres pendientes recién
    pedidas son sanas, una de hace tres días no —, de ahí la edad, que es sobre
    lo que alerta `HumanApprovalsStale`.

    Devuelve ``(0, 0.0)`` cuando no hay ninguna: aquí cero es el estado sano y
    es un dato, no una ausencia.
    """
    row = (
        await session.execute(
            sa_text(
                "SELECT count(*),"
                " COALESCE(EXTRACT(EPOCH FROM (now() - min(requested_at))), 0)"
                " FROM approval_requests WHERE status = 'pending'"
            )
        )
    ).first()
    if row is None:
        return 0, 0.0
    pending, oldest = row
    return int(pending or 0), float(oldest or 0.0)


async def _collect_llm_cost(session: Any) -> dict[str, tuple[int, float]]:
    """Tokens y coste por PROVEEDOR del consumo LLM fuera del pipeline de runs.

    `llm_usage_events` es la única tabla con dimensión de proveedor, pero solo
    cubre asistente, córtex y planning. El gasto de los runs va aparte
    (:func:`_collect_run_spend`) porque `executions` no tiene esa columna, y
    repartirlo entre proveedores sería inventárselo.
    """
    rows = await session.execute(
        sa_text(
            "SELECT COALESCE(provider_kind, 'unknown'),"
            " SUM(input_tokens + output_tokens), COALESCE(SUM(cost_usd), 0)"
            " FROM llm_usage_events"
            " WHERE created_at > now() - interval '24 hours'"
            " GROUP BY 1"
        )
    )
    return {
        str(provider): (int(tokens or 0), float(cost or 0)) for provider, tokens, cost in rows.all()
    }


async def _collect_run_spend(session: Any) -> tuple[int, float]:
    """Tokens y coste del pipeline de runs en 24 h, SIN dimensión de proveedor.

    `executions` no guarda con qué proveedor corrió (el modelo se resuelve por
    agente en tiempo de ejecución), así que la métrica se publica agregada. Es la
    mayor parte del gasto real: presentar solo el coste con `{provider}` de
    `llm_usage_events` haría pasar una fracción por el total.
    """
    row = (
        await session.execute(
            sa_text(
                "SELECT COALESCE(SUM(total_tokens), 0), COALESCE(SUM(total_cost_usd), 0)"
                " FROM executions WHERE created_at > now() - interval '24 hours'"
            )
        )
    ).first()
    if row is None:
        return 0, 0.0
    tokens, cost = row
    return int(tokens or 0), float(cost or 0)


async def _collect_status_counts(session: Any) -> dict[str, int]:
    """Count ``tasks`` rows grouped by lifecycle status (all tenants — the worker
    engine is BYPASSRLS). ``tasks`` is not soft-deletable (no ``deleted_at``)."""
    rows = await session.execute(sa_text("SELECT status, count(*) FROM tasks GROUP BY status"))
    return {str(status): int(count) for status, count in rows.all()}


async def _guarded(
    name: str,
    awaitable: Any,
    failures: set[str],
    default: Any,
) -> Any:
    """Corre un colector aislado del resto: si falla, se anota y se sigue.

    AUD16-09 exige que cada colector falle POR SEPARADO (un DB caído no puede
    llevarse por delante las métricas de Redis) y que su fallo quede visible en
    ``agentic_sampler_collector_up``. El try/except/anota/loguea era idéntico en
    cada colector, y repetirlo es donde se olvida el ``failures.add`` — que
    convierte un colector muerto en una familia simplemente ausente, o sea muda.
    """
    try:
        return await awaitable
    except Exception as exc:
        failures.add(name)
        _log.warning("maintenance.sample_queue_metrics.error", collector=name, error=str(exc))
        return default


# Colectores que dependen de la BD. Si la sesión ni siquiera abre, ninguno
# corrió y TODOS deben marcarse caídos — si no, sus familias saldrían
# simplemente ausentes, que es el fallo mudo que AUD16-09 vino a cerrar.
_DB_COLLECTORS = frozenset({"tasks_by_status", "executions_24h", "approvals", "llm_spend"})


async def _collect_from_redis(
    broker: Any, settings: Settings, failures: set[str]
) -> dict[str, Any]:
    """Lo que sale de Redis: profundidad de colas, contadores de tareas y DLQ."""
    from redis.asyncio import Redis

    from workers.celery_app import QUEUE_NAMES

    # Los colectores de Redis tragan errores POR CLAVE (contextlib.suppress):
    # con el broker sano, cada cola/stream conocido produce SIEMPRE una entrada
    # (LLEN/XLEN de clave ausente = 0) — un dict vacío solo puede significar que
    # el colector no pudo hablar con Redis.
    queue_depths = await _collect_queue_depths(broker, QUEUE_NAMES)
    if not queue_depths and QUEUE_NAMES:
        failures.add("queue_depths")
    task_counts, task_durations = await _guarded(
        "celery_tasks", _collect_celery_task_counters(broker), failures, ({}, {})
    )
    # prod-07 task_prod07_15: la racha de destilaciones fallidas del Memorizer
    # vive en el MISMO Redis del broker (la escriben los N hijos del prefork).
    memorizer_failures = await _guarded(
        "memorizer", _collect_memorizer_failures(broker), failures, None
    )

    # La DLQ vive en OTRO Redis (el de eventos), no en el del broker.
    events_redis = Redis.from_url(settings.events_redis_url)
    try:
        dlq_depths = await _collect_dlq_depths(events_redis, _DLQ_STREAMS)
        if not dlq_depths and _DLQ_STREAMS:
            failures.add("dlq_depth")
    finally:
        with contextlib.suppress(Exception):
            await events_redis.aclose()

    return {
        "queue_depths": queue_depths,
        "task_counts": task_counts,
        "task_durations": task_durations,
        "dlq_depths": dlq_depths,
        "memorizer_failures": memorizer_failures,
    }


async def _collect_from_db(db: Any, failures: set[str]) -> dict[str, Any]:
    """Lo que sale de una consulta a la BD, cada colector aislado del resto."""
    approvals_pending, approvals_oldest_age_s = await _guarded(
        "approvals", _collect_approval_metrics(db), failures, (None, None)
    )
    run_tokens, run_cost_usd = await _guarded(
        "llm_spend", _collect_run_spend(db), failures, (None, None)
    )
    return {
        "status_counts": await _guarded(
            "tasks_by_status", _collect_status_counts(db), failures, {}
        ),
        "execution_counts": await _guarded(
            "executions_24h", _collect_execution_counts(db), failures, {}
        ),
        "approvals_pending": approvals_pending,
        "approvals_oldest_age_s": approvals_oldest_age_s,
        "llm_usage": await _guarded("llm_spend", _collect_llm_cost(db), failures, {}),
        "run_tokens": run_tokens,
        "run_cost_usd": run_cost_usd,
    }


async def _sample_queue_metrics_async(
    settings: Settings,
    *,
    redis: Any | None = None,
) -> dict[str, Any]:
    """Async core — owns the redis + engine lifecycle. ``redis`` is injectable for
    tests. Always writes the textfile (even if a collector fails → that dimension
    is simply absent), so the file reflects the freshest successful sample.

    AUD16-09: cada colector falla POR SEPARADO (un DB caído ya no arrastra a los
    de Redis) y el fichero lleva heartbeat + ``collector_up`` 1/0 por colector,
    para que «No data» en un panel sea distinguible de «sampler/colector muerto»
    (regla MetricsSamplerStale + gauge agentic_sampler_collector_up)."""
    from redis.asyncio import Redis

    from workers.queue_metrics import write_queue_metrics

    own_redis = redis is None
    redis_client = redis if redis is not None else Redis.from_url(settings.broker_url)
    engine = worker_engine(settings)
    # Un único acumulador cuyas claves SON los parámetros de
    # `write_queue_metrics`: con doce dimensiones, arrastrar doce variables
    # locales es donde se olvida pasar una y la familia desaparece del fichero
    # sin que nada se queje.
    samples: dict[str, Any] = {"queue_depths": {}, "status_counts": {}}
    failures: set[str] = set()
    try:
        samples |= await _collect_from_redis(redis_client, settings, failures)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            samples |= await _collect_from_db(db, failures)
    except Exception as exc:  # pragma: no cover — best-effort: never crash beat
        # Fallo de infraestructura fuera de los colectores (p.ej. la sesión DB
        # no abre): los colectores DB no corrieron.
        failures.update(_DB_COLLECTORS | {"celery_tasks", "memorizer"})
        _log.warning("maintenance.sample_queue_metrics.error", error=str(exc))
    finally:
        await engine.dispose()
        if own_redis:
            with contextlib.suppress(Exception):
                await redis_client.aclose()

    written = write_queue_metrics(
        settings.queue_metrics_textfile_path,
        sampled_at=time.time(),
        collector_failures=frozenset(failures),
        **samples,
    )
    _log.info(
        "maintenance.sample_queue_metrics.done",
        queues=len(samples["queue_depths"]),
        statuses=len(samples["status_counts"]),
        collector_failures=sorted(failures),
        written=written,
    )
    return {
        "queue_depths": samples["queue_depths"],
        "status_counts": samples["status_counts"],
        "written": written,
    }
