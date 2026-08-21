"""Zombie-execution sweeper — `workers.sweep_stale_executions`, every 5 min
(prod-06 task_prod06_zombi_01). Best-effort: never crashes beat.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine

_log = structlog.get_logger("workers.maintenance")

# A `running` execution older than this is presumed lost: the Celery child was
# SIGKILLed (OOM or the hard time limit) without finalising the row, leaving it
# `running` forever and possibly an orphan container. 7h = the 6h hard-limit cap
# (prod-06 decision 2 / zombi_03) + a 1h margin so a legitimately-long run is
# never reaped early.
_STALE_EXECUTION_AFTER = timedelta(hours=7)

# Sweep de huérfanos (2026-07-03, gotcha engine-restart): una fila `running`
# cuyo contenedor YA NO EXISTE no puede terminar jamás — no hace falta esperar
# las 7 h. La gracia cubre la ventana provisión→launch (la fila se crea antes
# de arrancar el contenedor: resolución de modelo + worktree, segundos).
_ORPHAN_CONTAINER_GRACE = timedelta(minutes=5)


@app.task(name="workers.sweep_stale_executions")  # type: ignore[untyped-decorator]
def sweep_stale_executions() -> dict[str, Any]:
    """Close zombie executions + reap their orphan containers.

    No sweeper existed (workers-2): a hard-limit/OOM SIGKILL of the Celery child
    left ``executions.running`` rows and dangling agent-runtime containers. This
    beat finds ``running`` rows older than the stale threshold, marks them
    ``failed`` (``abort_code=stale_after_worker_loss``), transitions their task off
    ``in_progress`` (reusing the dag_01 policy → ``blocked``), ``docker rm -f``
    their container by label, and frees their per-task run-lock so the task can
    actually be re-dispatched (:func:`_release_locks_of_sealed_runs`).
    Best-effort (never crashes beat)."""
    settings = get_settings()
    return asyncio.run(_sweep_stale_executions_async(settings))


async def _release_locks_of_sealed_runs(redis: Any, sealed: list[tuple[str, str | None]]) -> int:
    """Free the per-task run-lock of every run this sweep just declared dead.

    The lock (``workers.run_lock``) stops a Celery re-delivery from starting a
    SECOND run of a task while the first is live — its worktree
    ``reset --hard`` + ``clean -fdx`` would destroy the first's work. Its TTL is
    the broker's visibility timeout (7 h) because that is the earliest a
    competitor can exist; anything shorter reopens the gap C-05 closed.

    But the holder only releases it in a ``finally``, and a SIGKILL (OOM, hard
    limit, ``docker stop``) never runs one. So the lock outlived the run by up to
    7 h and VETOED every attempt to recover the task in between — including this
    sweeper's own: it seals the row and moves the task to ``blocked``, the
    operator unblocks it, the dispatch fires, and ``run_execution`` returns
    ``concurrent_run_locked`` as a SUCCESS, so Celery acks and the retry is gone.
    The same reasoning already applied to the execution ROW (the orphan-container
    sweep exists so it does not sit 7 h "vetando el re-despacho de su task"); the
    lock was simply never given the same treatment.

    The sweeper has just PROVEN the holder is dead, so it is the right authority
    to free it. Release stays token-guarded: the token is the run's Celery job
    id, which is also stamped on the execution row, so a lock re-acquired by a
    NEWER legitimate run (whose token differs) is never touched. A row with no
    ``celery_task_id`` — a direct, non-Celery invocation — is skipped rather than
    force-deleted, because then ownership cannot be proven.
    """
    from workers.run_lock import release_run_lock

    released = 0
    for task_id, token in sealed:
        if not token:
            continue
        if await release_run_lock(redis, task_id, token=token):
            released += 1
    return released


async def _sweep_run_locks(
    settings: Settings, redis: Any, sealed: list[tuple[str, str | None]]
) -> int:
    """Owns the Redis connection around :func:`_release_locks_of_sealed_runs`.

    Only connects when there is actually a lock to free, and closes only what it
    opened (the test injects its own client)."""
    if not sealed:
        return 0
    owned = None
    if redis is None:
        from redis.asyncio import Redis

        redis = owned = Redis.from_url(settings.events_redis_url, decode_responses=True)
    try:
        return await _release_locks_of_sealed_runs(redis, sealed)
    finally:
        if owned is not None:
            with contextlib.suppress(Exception):
                await owned.aclose()


async def _remove_exited_terminal_containers(engine: Any, runner: Any) -> int:
    """F0.6 (auditoría 2026-07-02): reap de contenedores ``exited`` cuya
    execution ya es terminal (o cuya fila no existe). ``run_streamed`` solo
    limpia su contenedor si el proceso worker sigue vivo; el path de supersede
    no limpia el contenedor del run viejo — en un host que duerme a diario se
    acumulan exited(255). Un run VIVO (fila running) nunca se toca: su
    contenedor exited puede ser forense en curso."""
    from api_server.db.domain import Execution, ExecutionStatus
    from sqlalchemy import select

    exited = list(runner.list_exited_managed())
    if not exited:
        return 0
    exec_uuids: list[UUID] = []
    for _cid, eid in exited:
        with contextlib.suppress(ValueError):
            exec_uuids.append(UUID(eid))
    statuses: dict[str, str] = {}
    if exec_uuids:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            rows = await db.execute(
                select(Execution.id, Execution.status).where(Execution.id.in_(exec_uuids))
            )
            statuses = {str(row[0]): str(row[1]) for row in rows}
    removed = 0
    for container_id, eid in exited:
        if statuses.get(eid) == ExecutionStatus.RUNNING.value:
            continue
        with contextlib.suppress(Exception):
            if runner.remove_container(container_id):
                removed += 1
    return removed


async def _sweep_stale_executions_async(
    settings: Settings,
    *,
    runner: Any = None,
    stale_after: timedelta = _STALE_EXECUTION_AFTER,
    now: datetime | None = None,
    redis: Any = None,
) -> dict[str, Any]:
    """Async core. ``runner`` (a container runner with ``kill_by_label``),
    ``redis`` (for the run-lock release) and ``now`` are injectable so the test
    drives it without Docker, Redis or wall-clock."""
    from api_server.db.domain import Execution, ExecutionStatus
    from api_server.db.execution_repo import seal_terminal_execution
    from sqlalchemy import select

    from workers.container import AgentContainerRunner
    from workers.execution import transition_task_after_run

    moment = now or datetime.now(UTC)
    cutoff = moment - stale_after
    orphan_cutoff = moment - _ORPHAN_CONTAINER_GRACE
    engine = worker_engine(settings)
    swept = 0
    reaped = 0
    containers_removed = 0
    released = 0
    # (task_id, run-lock token) of every row this pass seals — see
    # `_release_locks_of_sealed_runs`.
    sealed_runs: list[tuple[str, str | None]] = []
    try:
        if runner is None:
            runner = AgentContainerRunner(settings)
        # Contenedores gestionados EXISTENTES (cualquier estado) — una llamada al
        # daemon, FUERA de la txn. None = daemon sin respuesta: el sweep de
        # huérfanos no barre nada (solo actúa el umbral por edad).
        alive_ids: set[str] | None = None
        if hasattr(runner, "list_managed_execution_ids"):
            alive_ids = runner.list_managed_execution_ids()
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db, db.begin():
            candidates = list(
                (
                    await db.execute(
                        select(Execution).where(
                            Execution.status == ExecutionStatus.RUNNING.value,
                            Execution.started_at < orphan_cutoff,
                        )
                    )
                ).scalars()
            )
            stale_ids = []
            for execution in candidates:
                # El SELECT ya exige started_at < orphan_cutoff (no-NULL); el
                # narrow explícito lo hace visible para mypy y a prueba de refactors.
                stale_by_age = execution.started_at is not None and execution.started_at < cutoff
                # Huérfano (2026-07-03): pasada la gracia, sin contenedor en el
                # daemon → el run no puede terminar jamás; cerrarlo YA en vez de
                # dejarlo 7 h de zombi vetando el re-despacho de su task.
                # M1: SOLO es huérfano si el contenedor llegó a existir
                # (container_launched_at no NULL). Una fila `running` aún en provisión
                # (pull en frío / checkout git / Vault lento) no tiene contenedor que se
                # haya perdido — protegida del reap temprano, cae solo por edad (7 h).
                orphaned = (
                    alive_ids is not None
                    and execution.container_launched_at is not None
                    and str(execution.id) not in alive_ids
                )
                if not (stale_by_age or orphaned):
                    continue
                # Defensa en profundidad (M2): sella por el primitivo idempotente —
                # si una finalize legítima concurrente ya cerró la fila, no la pisa.
                if not seal_terminal_execution(
                    execution,
                    status=ExecutionStatus.FAILED.value,
                    abort_code="stale_after_worker_loss",
                    now=moment,
                ):
                    continue
                stale_ids.append(str(execution.id))
                sealed_runs.append((str(execution.task_id), execution.celery_task_id))
                # Move the orphaned task off in_progress (dag_01 policy → blocked).
                await transition_task_after_run(db, execution.task_id, ExecutionStatus.FAILED.value)
                # AUD16-21: rastro reconstruible desde BD — quién selló y por qué.
                from api_server.db.task_audit_repo import append_audit_event

                await append_audit_event(
                    db,
                    tenant_id=execution.tenant_id,
                    task_id=execution.task_id,
                    kind="execution_sealed_by_sweeper",
                    actor="system:stale_sweeper",
                    payload={
                        "execution_id": str(execution.id),
                        "abort_code": "stale_after_worker_loss",
                        "reason": "stale_by_age" if stale_by_age else "orphaned_container",
                    },
                )
                swept += 1
        # Reap lingering containers OUTSIDE the txn — Docker I/O must never hold
        # the DB transaction open. Best-effort per execution.
        for execution_id in stale_ids:
            with contextlib.suppress(Exception):
                reaped += runner.kill_by_label(execution_id)
        containers_removed = await _remove_exited_terminal_containers(engine, runner)
        # LAST, and best-effort: a Redis blip must not cost us the Docker cleanup
        # above.
        released = await _sweep_run_locks(settings, redis, sealed_runs)
    except Exception as exc:  # pragma: no cover — defensive logging
        _log.warning("maintenance.sweep_stale_executions.error", error=str(exc))
        return {
            "swept": swept,
            "reaped": reaped,
            "containers_removed": containers_removed,
            "run_locks_released": released,
            "error": str(exc),
        }
    finally:
        await engine.dispose()

    _log.info(
        "maintenance.sweep_stale_executions.done",
        swept=swept,
        reaped=reaped,
        containers_removed=containers_removed,
        run_locks_released=released,
    )
    return {
        "swept": swept,
        "reaped": reaped,
        "containers_removed": containers_removed,
        "run_locks_released": released,
    }
