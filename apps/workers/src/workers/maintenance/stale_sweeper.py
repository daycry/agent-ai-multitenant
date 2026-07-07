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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

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


@app.task(name="workers.sweep_stale_executions")  # type: ignore[misc]
def sweep_stale_executions() -> dict[str, Any]:
    """Close zombie executions + reap their orphan containers.

    No sweeper existed (workers-2): a hard-limit/OOM SIGKILL of the Celery child
    left ``executions.running`` rows and dangling agent-runtime containers. This
    beat finds ``running`` rows older than the stale threshold, marks them
    ``failed`` (``abort_code=stale_after_worker_loss``), transitions their task off
    ``in_progress`` (reusing the dag_01 policy → ``blocked``), and ``docker rm -f``
    their container by label. Best-effort (never crashes beat)."""
    settings = get_settings()
    return asyncio.run(_sweep_stale_executions_async(settings))


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
) -> dict[str, Any]:
    """Async core. ``runner`` (a container runner with ``kill_by_label``) and
    ``now`` are injectable so the test drives it without Docker or wall-clock."""
    from api_server.db.domain import Execution, ExecutionStatus
    from api_server.db.execution_repo import seal_terminal_execution
    from sqlalchemy import select

    from workers.container import AgentContainerRunner
    from workers.execution import transition_task_after_run

    moment = now or datetime.now(UTC)
    cutoff = moment - stale_after
    orphan_cutoff = moment - _ORPHAN_CONTAINER_GRACE
    engine = create_async_engine(settings.database_url)
    swept = 0
    reaped = 0
    containers_removed = 0
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
                stale_by_age = execution.started_at < cutoff
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
                # Move the orphaned task off in_progress (dag_01 policy → blocked).
                await transition_task_after_run(db, execution.task_id, ExecutionStatus.FAILED.value)
                swept += 1
        # Reap lingering containers OUTSIDE the txn — Docker I/O must never hold
        # the DB transaction open. Best-effort per execution.
        for execution_id in stale_ids:
            with contextlib.suppress(Exception):
                reaped += runner.kill_by_label(execution_id)
        containers_removed = await _remove_exited_terminal_containers(engine, runner)
    except Exception as exc:  # pragma: no cover — defensive logging
        _log.warning("maintenance.sweep_stale_executions.error", error=str(exc))
        return {
            "swept": swept,
            "reaped": reaped,
            "containers_removed": containers_removed,
            "error": str(exc),
        }
    finally:
        await engine.dispose()

    _log.info(
        "maintenance.sweep_stale_executions.done",
        swept=swept,
        reaped=reaped,
        containers_removed=containers_removed,
    )
    return {"swept": swept, "reaped": reaped, "containers_removed": containers_removed}
