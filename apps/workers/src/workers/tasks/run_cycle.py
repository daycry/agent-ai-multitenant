"""Ciclo Celery del run del agente (task_02_06, task_02_31).

`run_agent_container` lanza un contenedor suelto; `run_execution` conduce una
ejecución completa (lo que encola el dispatcher del orchestrator) con su
run-lock por-task (A6), el manejo del soft-timeout (prod-06 MUST-a) y el
dead-letter stream para fallos no manejados (task_06_14_04).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any
from uuid import UUID

import structlog
from api_server.events import publish_task_status_changed
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import EXECUTION_VISIBILITY_TIMEOUT_S, app
from workers.config import Settings, get_settings
from workers.container import AgentContainerRunner, ContainerSpec
from workers.execution import ExecutionRequest, conduct_execution

_log = structlog.get_logger("workers.tasks")

# Failed `run_execution` jobs land here for operator visibility / manual
# reprocessing — we deliberately do NOT auto-retry agent runs (each retry
# is a full, costly LLM run with side effects). Plan 06.14 task_06_14_04.
_DEAD_LETTER_STREAM = "dlq:executions"


@app.task(name="workers.run_agent_container")  # type: ignore[untyped-decorator]
def run_agent_container(
    image: str | None = None,
    command: list[str] | None = None,
    env: dict[str, str] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Launch one agent-runtime container and return its result.

    `image` defaults to the configured agent-runtime image. The result
    is the JSON-safe dict from `ContainerResult.as_dict()`.
    """
    settings = get_settings()
    runner = AgentContainerRunner(settings)
    spec = ContainerSpec(
        image=image or settings.agent_runtime_image,
        command=command,
        env=env or {},
        workspace_host_path=workspace,
    )
    return runner.run(spec).as_dict()


@app.task(bind=True, name="workers.run_execution")  # type: ignore[untyped-decorator]
def run_execution(self: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Conduct one agent execution end to end (Plan 02 Fase G).

    The orchestrator (task_02_31) enqueues this with the execution
    request as a plain dict. The DB and Redis handles are built from
    `Settings`; the result is the JSON-safe `ExecutionOutcome` dict.

    Bound (``bind=True``) so we can persist ``self.request.id`` — the Celery job
    id — onto the `executions` row (prod-06 cancel_01). Without it the operator
    cancel endpoint's `revoke` branch was dead code (the column stayed NULL).

    On an unhandled failure (e.g. a tampered cross-tenant payload, or a
    DB/broker outage) the job is recorded to a dead-letter stream and the
    exception re-raised so Celery marks it failed. Agent runs are NOT
    auto-retried — re-running is expensive and side-effecting; an operator
    reprocesses from the dead-letter stream (task_06_14_04).
    """
    from celery.exceptions import SoftTimeLimitExceeded

    settings = get_settings()
    celery_task_id = getattr(self.request, "id", None)
    try:
        return asyncio.run(
            _run_execution(
                ExecutionRequest.from_dict(request),
                settings,
                celery_task_id=celery_task_id,
            )
        )
    except SoftTimeLimitExceeded as exc:
        # prod-06 (MUST-ADDRESS a): el soft-timeout de Celery lo captura AQUÍ (hilo
        # principal), no `run_streamed`. Clasificamos por el flag de cancelación:
        # con flag → `cancelled` SIN DLQ (fue un cancel del operador); sin flag →
        # `failed(soft_time_limit_exceeded)` CON DLQ. En ambos casos finalizamos la
        # fila `running` (no la dejamos colgada hasta el sweeper) y matamos el
        # contenedor huérfano (el SIGKILL del hijo Celery no toca el contenedor DooD).
        was_cancel = asyncio.run(_finalize_soft_timeout(settings, request))
        if not was_cancel:
            _record_execution_dead_letter(settings, request, exc)
        raise
    except Exception as exc:
        _record_execution_dead_letter(settings, request, exc)
        raise


def _record_execution_dead_letter(
    settings: Settings, request: dict[str, Any], exc: Exception
) -> None:
    """Best-effort: push a failed run_execution onto the dead-letter stream.
    Never masks the original error (a DLQ outage just logs a warning)."""
    try:
        asyncio.run(_push_execution_dead_letter(settings, request, exc))
    except Exception as dlq_exc:  # pragma: no cover - DLQ is best-effort
        _log.warning(
            "workers.dead_letter_record_failed",
            task_id=str(request.get("task_id", "")),
            error=str(dlq_exc),
        )


async def _finalize_soft_timeout(settings: Settings, request: dict[str, Any]) -> bool:
    """Finalize a soft-timed-out run's `running` row(s) + kill its container.

    Returns ``True`` iff it was an operator CANCEL (``cancel_requested_at`` set) —
    the caller then skips the dead-letter. A genuine timeout (no flag) becomes
    ``failed(soft_time_limit_exceeded)`` and IS dead-lettered. Sets
    ``completed_at`` so a late finalize from the (killed) run is idempotent-guarded
    (F46/F52). Best-effort: any error just leaves the row for the zombie sweeper."""
    from datetime import UTC, datetime

    from api_server.db.domain import Execution, ExecutionStatus
    from api_server.db.execution_repo import seal_terminal_execution

    from workers.container import AgentContainerRunner

    task_id_raw = str(request.get("task_id", ""))
    if not task_id_raw:
        return False
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    was_cancel = False
    exec_ids: list[str] = []
    try:
        async with sessionmaker() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        select(Execution).where(
                            Execution.task_id == UUID(task_id_raw),
                            Execution.status == ExecutionStatus.RUNNING.value,
                        )
                    )
                )
                .scalars()
                .all()
            )
            now = datetime.now(UTC)
            for execution in rows:
                if execution.cancel_requested_at is not None:
                    seal_terminal_execution(
                        execution,
                        status=ExecutionStatus.CANCELLED.value,
                        abort_code="cancelled",
                        now=now,
                    )
                    was_cancel = True
                else:
                    seal_terminal_execution(
                        execution,
                        status=ExecutionStatus.FAILED.value,
                        abort_code="soft_time_limit_exceeded",
                        now=now,
                    )
                exec_ids.append(str(execution.id))
    except Exception as exc:  # best-effort — the zombie sweeper is the backstop
        _log.warning("workers.soft_timeout_finalize_failed", task_id=task_id_raw, error=str(exc))
    finally:
        await engine.dispose()
    # Kill the orphaned container(s) — the DooD container outlives the SIGKILL of
    # the Celery worker child; without this it burns LLM budget until it times out.
    if exec_ids:
        runner = AgentContainerRunner(settings)
        for eid in exec_ids:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(runner.kill_by_label, eid)
    return was_cancel


async def _push_execution_dead_letter(
    settings: Settings, request: dict[str, Any], exc: Exception
) -> None:
    redis: Redis = Redis.from_url(settings.events_redis_url, decode_responses=True)
    try:
        await redis.xadd(
            _DEAD_LETTER_STREAM,
            {
                "task": "workers.run_execution",
                "tenant_id": str(request.get("tenant_id", "")),
                "task_id": str(request.get("task_id", "")),
                "error": f"{type(exc).__name__}: {exc}",
                "failed_at_unix": str(time.time()),
            },
            maxlen=10_000,
            approximate=True,
        )
    finally:
        await redis.aclose()


def run_lock_ttl_s() -> int:
    """TTL del run-lock: la ventana de visibilidad del broker (C-05).

    El lock impide que una re-entrega (`acks_late`) arranque un SEGUNDO run de
    la misma tarea mientras el primero vive — su `sync_to_head` hace
    `reset --hard` + `clean -fdx` y se llevaría por delante el trabajo en vuelo.

    Se calculaba desde el presupuesto de CONTENEDOR
    (``container_timeout_with_grace_for_kind("claude_sdk") + 300`` = 7620 s), por
    debajo del `execution_hard_time_limit_s` (7800 s por defecto, hasta 6 h si el
    operador lo sube). Un run que llegara al límite duro **perdía el lock antes de
    morir**, y en esa ventana la re-entrega podía adquirirlo: exactamente lo que
    el lock existe para impedir.

    El ancla correcta no es el presupuesto del contenedor sino
    :data:`EXECUTION_VISIBILITY_TIMEOUT_S`: es el instante en que Redis re-entrega
    el mensaje, o sea el primer momento en que puede existir un competidor.
    Mientras el lock viva exactamente eso, no queda hueco — y tampoco retiene la
    tarea de más, porque pasado ese punto la re-entrega ya es legítima (el run
    anterior está muerto: el hard limit tiene su techo POR DEBAJO, cosa que
    `test_hard_limit_registry_validation` fija).
    """
    return EXECUTION_VISIBILITY_TIMEOUT_S


async def _run_execution(
    request: ExecutionRequest, settings: Settings, *, celery_task_id: str | None = None
) -> dict[str, Any]:
    """Async core of `run_execution` — owns the engine + Redis lifecycle."""
    from workers.execution import ExecutionOutcome
    from workers.run_lock import acquire_run_lock, release_run_lock

    engine = create_async_engine(settings.database_url)
    redis: Redis = Redis.from_url(settings.events_redis_url, decode_responses=True)
    # prod-18 A6: a per-task lock so a re-delivered message (acks_late; the DooD
    # container survives a worker crash) can't spawn a SECOND run of the same task
    # whose worktree `reset --hard`+`clean -fdx` would corrupt the first's in-flight
    # work. TTL = the longest container budget + margin, so a crashed holder frees
    # the lock roughly when its orphaned container would time out anyway.
    lock_token = celery_task_id or "1"
    lock_ttl = run_lock_ttl_s()
    acquired = False
    outcome: ExecutionOutcome | None = None
    try:
        acquired = await acquire_run_lock(redis, request.task_id, ttl_s=lock_ttl, token=lock_token)
        if not acquired:
            _log.warning(
                "workers.run_lock_held_skip",
                task_id=request.task_id,
                reason="another run of this task is live (concurrent re-delivery)",
            )
            return ExecutionOutcome(
                execution_id="", status="skipped", abort_code="concurrent_run_locked"
            ).as_dict()
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        outcome = await conduct_execution(
            request,
            settings=settings,
            sessionmaker=sessionmaker,
            redis=redis,
            celery_task_id=celery_task_id,
        )
        return outcome.as_dict()
    finally:
        if acquired:
            await release_run_lock(redis, request.task_id, token=lock_token)
        # H1: publish the deferred finish event ONLY once the lock is free — the
        # orchestrator reacts in milliseconds (review dispatch on `in_review`,
        # re-dispatch on reject→backlog) and a run_execution that lands while
        # this task's lock is still held is dropped as `concurrent_run_locked`
        # (then only the reconciler's ~6 min re-announce saves the cycle).
        if outcome is not None and outcome.pending_task_event is not None:
            task_obj, old_status, new_status = outcome.pending_task_event
            try:
                await publish_task_status_changed(
                    redis, task_obj, old_status=old_status, new_status=new_status
                )
            except Exception:
                # Best-effort: the state IS committed; the reconciler's pass (b)
                # re-announces stale in_review/backlog tasks, so losing the event
                # costs latency, never correctness. Crashing here instead would
                # let Celery re-deliver an already-finished (and paid) run.
                _log.exception(
                    "workers.task_event_publish_failed",
                    task_id=request.task_id,
                    old_status=old_status,
                    new_status=new_status,
                )
        await redis.aclose()
        await engine.dispose()
