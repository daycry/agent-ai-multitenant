"""El worker que se apaga no deja runs facturando (`task_cv_43`, G-08).

El quiesce nocturno del backup —y cualquier ``docker compose stop``— manda
SIGTERM al worker; Celery espera al job, el job espera al contenedor, y a los
segundos de gracia el worker muere. El agent-runtime seguía vivo, facturando
tokens hasta 6-7 h, y su fila ``executions`` quedaba ``running`` hasta que el
sweeper la sellaba como ``stale_after_worker_loss``.

Cada contenedor lleva la etiqueta del worker que lo lanzó
(:data:`workers.container.WORKER_LABEL`). Al recibir ``worker_shutting_down``
el worker mata SUS contenedores de agent-runtime y sella sus filas en el acto
(``failed``, ``abort_code=quiesced``), con el mismo camino que el sweeper.
Todo best-effort: apagarse nunca puede fallar por esto.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import structlog

from workers.container import WORKER_LABEL, worker_identity

_log = structlog.get_logger(__name__)

ABORT_CODE = "quiesced"
_EXECUTION_LABEL = "com.agentic-platform.execution-id"
_installed = False


def quiesce_worker_runs(
    client: Any, *, worker_id: str, seal: Callable[[Sequence[str]], int]
) -> dict[str, int]:
    """Mata los contenedores de agent-runtime de ``worker_id`` y sella sus runs.

    ``seal`` recibe los ``execution_id`` de los contenedores encontrados (mueran
    o no: un contenedor que no murió sigue sin worker que lo atienda) y
    devuelve cuántas filas selló. Devuelve ``{"killed", "sealed"}``.
    """
    try:
        containers = client.containers.list(
            all=True,
            filters={
                "label": [
                    f"{WORKER_LABEL}={worker_id}",
                    "com.agentic-platform.component=agent-runtime",
                ]
            },
        )
    except Exception as exc:
        _log.warning("workers.quiesce.list_failed", error=str(exc))
        return {"killed": 0, "sealed": 0}
    killed = 0
    execution_ids: list[str] = []
    for container in containers:
        labels = getattr(container, "labels", None) or {}
        execution_id = labels.get(_EXECUTION_LABEL)
        if execution_id:
            execution_ids.append(str(execution_id))
        try:
            container.kill()
            killed += 1
        except Exception as exc:
            _log.warning(
                "workers.quiesce.kill_failed",
                container=getattr(container, "name", "?"),
                error=str(exc),
            )
            continue
        with contextlib.suppress(Exception):
            container.remove(force=True)
    sealed = 0
    if execution_ids:
        try:
            sealed = int(seal(execution_ids))
        except Exception as exc:
            _log.warning("workers.quiesce.seal_failed", error=str(exc))
    _log.info("workers.quiesce.done", worker=worker_id, killed=killed, sealed=sealed)
    return {"killed": killed, "sealed": sealed}


async def _seal_quiesced_async(settings: Any, execution_ids: Sequence[str]) -> int:
    from api_server.db.domain import Execution
    from api_server.db.domain.enums import ExecutionStatus
    from api_server.db.execution_repo import seal_terminal_execution
    from api_server.db.task_audit_repo import append_audit_event
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from workers.db import worker_engine
    from workers.execution import transition_task_after_run

    engine = worker_engine(settings)
    sealed = 0
    now = datetime.now(UTC)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db, db.begin():
            rows = (
                await db.execute(
                    select(Execution).where(
                        Execution.id.in_(list(execution_ids)),
                        Execution.status == ExecutionStatus.RUNNING.value,
                    )
                )
            ).scalars()
            for execution in rows:
                if not seal_terminal_execution(
                    execution,
                    status=ExecutionStatus.FAILED.value,
                    abort_code=ABORT_CODE,
                    now=now,
                ):
                    continue
                await transition_task_after_run(db, execution.task_id, ExecutionStatus.FAILED.value)
                await append_audit_event(
                    db,
                    tenant_id=execution.tenant_id,
                    task_id=execution.task_id,
                    kind="execution_sealed_by_quiesce",
                    actor="system:worker_quiesce",
                    payload={
                        "execution_id": str(execution.id),
                        "abort_code": ABORT_CODE,
                        "worker": worker_identity(),
                    },
                )
                sealed += 1
    finally:
        await engine.dispose()
    return sealed


def seal_quiesced_executions(execution_ids: Sequence[str]) -> int:
    """Sella como ``failed:quiesced`` las filas ``running`` de ``execution_ids``."""
    from workers.config import get_settings

    return asyncio.run(_seal_quiesced_async(get_settings(), execution_ids))


def _on_worker_shutting_down(**_kwargs: Any) -> None:
    try:
        import docker

        client = docker.from_env()
    except Exception as exc:
        _log.warning("workers.quiesce.docker_unavailable", error=str(exc))
        return
    quiesce_worker_runs(client, worker_id=worker_identity(), seal=seal_quiesced_executions)


def install_quiesce_signal() -> None:
    """Conecta ``worker_shutting_down``. Idempotente."""
    global _installed  # noqa: PLW0603 - estado de instalación por proceso
    if _installed:
        return
    from celery.signals import worker_shutting_down

    worker_shutting_down.connect(_on_worker_shutting_down, weak=False)
    _installed = True


def is_installed() -> bool:
    return _installed


__all__ = [
    "ABORT_CODE",
    "install_quiesce_signal",
    "is_installed",
    "quiesce_worker_runs",
    "seal_quiesced_executions",
]
