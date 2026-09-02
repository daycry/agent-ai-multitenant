"""Una reclamación viaja con identidad: `claim_id` (`task_cv_13`, A-05).

Auditoría 2026-09-01. El dispatch reclama la tarea (`ready → in_progress`) y
encola el run; si la cola va con retraso (prefetch 1, dos hilos, runs de dos
horas: pasa con tres tareas `ready`), el reconciler V-1 revierte la reclamación
a los 30 minutos y la tarea se vuelve a despachar. Entonces hay DOS mensajes
para la misma tarea, y el viejo —sin el feedback del rechazo, con un contexto
caducado— puede ganar. Ahora cada reclamación lleva un `claim_id` que el
dispatch escribe en la tarea y en el `ExecutionRequest`; el worker descarta
(`skipped/stale_claim`) todo mensaje cuyo `claim_id` no sea el vigente, ANTES de
tocar nada (ni supersede, ni fila, ni worktree).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from workers import execution
from workers.execution import _claim_is_current, _SkippedRun
from workers.run_contract import ExecutionRequest

pytestmark = pytest.mark.unit


# ----------------------------------------------------------------- regla pura


def test_a_matching_claim_is_current() -> None:
    assert _claim_is_current(task_claim_id="c-1", request_claim_id="c-1") is True


def test_a_different_claim_is_stale() -> None:
    assert _claim_is_current(task_claim_id="c-2", request_claim_id="c-1") is False


def test_a_request_without_claim_is_trusted_for_compatibility() -> None:
    """Orquestador anterior al campo, o invocación manual: sin `claim_id` en el
    mensaje no hay nada que comparar y se conserva el comportamiento previo
    (desplegar el worker antes que el orquestador es seguro)."""
    assert _claim_is_current(task_claim_id="c-1", request_claim_id=None) is True


def test_a_task_without_claim_rejects_a_message_that_carries_one() -> None:
    """La tarea fue revertida (`claim_id` limpiado) y este mensaje es de la
    reclamación que se revirtió: no es vigente."""
    assert _claim_is_current(task_claim_id=None, request_claim_id="c-1") is False


# --------------------------------------------------------------- contrato


def _raw(**extra: Any) -> dict[str, Any]:
    return {
        "tenant_id": str(uuid4()),
        "task_id": str(uuid4()),
        "agent_id": None,
        "task": {"title": "t"},
        "model": {"kind": "scripted"},
        **extra,
    }


def test_the_request_carries_the_claim_from_the_celery_payload() -> None:
    assert ExecutionRequest.from_dict(_raw(claim_id="c-9")).claim_id == "c-9"


def test_an_old_payload_without_claim_still_parses() -> None:
    assert ExecutionRequest.from_dict(_raw()).claim_id is None


# --------------------------------------------------------------- el worker


class _Session:
    """Doble mínimo: `_prepare_run` sólo llega a `get(Task)` antes de descartar."""

    def __init__(self, task: Any) -> None:
        self._task = task
        self.executed: list[Any] = []

    async def get(self, _model: Any, _id: Any) -> Any:
        return self._task

    async def execute(self, statement: Any, *_a: Any, **_k: Any) -> Any:
        self.executed.append(statement)
        raise AssertionError("un mensaje rancio no debe tocar la BD")


@pytest.mark.asyncio
async def test_a_stale_claim_is_skipped_before_touching_anything() -> None:
    tenant_id, task_id = uuid4(), uuid4()
    task = SimpleNamespace(
        id=task_id, tenant_id=tenant_id, status="in_progress", claim_id="c-vigente"
    )
    session = _Session(task)
    request = ExecutionRequest(
        tenant_id=str(tenant_id),
        task_id=str(task_id),
        agent_id=None,
        task={"title": "t"},
        model={"kind": "scripted"},
        claim_id="c-revertida",
    )

    prepared = await execution._prepare_run(
        session,  # type: ignore[arg-type]
        request,
        task_id=task_id,
        tenant_id=tenant_id,
        vault_store=None,
        celery_task_id="celery-old",
    )

    assert isinstance(prepared, _SkippedRun)
    assert prepared.abort_code == "stale_claim"
    assert session.executed == [], "el mensaje rancio ejecutó algo (¿el supersede?)"
