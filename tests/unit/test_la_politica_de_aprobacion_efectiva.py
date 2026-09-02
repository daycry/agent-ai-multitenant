"""La solicitud de aprobación se evalúa con la MISMA política con la que el runtime aparcó.

Auditoría 2026-09-01 (A-01). El worker resuelve la política EFECTIVA del proyecto
—el preset `development` de plataforma cuando el proyecto no tiene ninguna (ADR
0104)— y se la inyecta al runtime, que aparca en `awaiting_human_approval` si la
acción la exige. Al finalizar, `request_approval_if_needed` evaluaba
`project.human_approval_policy` CRUDA: `None` → «no hace falta humano» → no
creaba `ApprovalRequest`… con la ejecución ya sellada como `awaiting`. Nadie
recupera ese estado (el reconciler exige un run terminal, el sweeper sólo barre
`running`, `approval_expiry` necesita una solicitud): tarea `in_progress` para
siempre, agente cargado, plan congelado. Un proyecto creado por API o por chat
está en ese estado por defecto.

El docstring de la función lo decía en voz alta: «una política ausente/vacía
[…] NO es de este ADR». Las dos mitades de un mismo gate tienen que leer la
misma política, o el gate aparca por un lado y no abre por el otro.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from api_server.db.approval_repo import request_approval_if_needed
from api_server.db.domain import Execution, Project, Task

pytestmark = pytest.mark.unit

_POLITICA_EFECTIVA: dict[str, Any] = {
    "preset": "development",
    "categories": {"http_post": "human_required", "code_changes": "auto"},
}


class _Resultado:
    def scalars(self) -> _Resultado:
        return self

    def all(self) -> list[Any]:
        return []


class _Sesion:
    """Lo mínimo que `request_approval_if_needed` toca: execute/add/get/flush."""

    def __init__(self, task: Task) -> None:
        self._task = task
        self.added: list[Any] = []

    async def execute(self, _stmt: Any) -> _Resultado:
        return _Resultado()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def get(self, _model: Any, _id: Any) -> Task:
        return self._task

    async def flush(self) -> None:
        return None


def _escenario() -> tuple[_Sesion, Execution, Project, Task]:
    tenant, project_id, task_id = uuid4(), uuid4(), uuid4()
    project = Project(id=project_id, tenant_id=tenant, name="sin política", status="active")
    project.human_approval_policy = None  # creado por API/chat: sin política explícita
    task = Task(id=task_id, tenant_id=tenant, project_id=project_id, title="t")
    task.status = "in_progress"
    execution = Execution(id=uuid4(), tenant_id=tenant, task_id=task_id)
    execution.status = "awaiting_human_approval"  # así lo selló finalize_execution
    return _Sesion(task), execution, project, task


@pytest.mark.asyncio
async def test_con_la_politica_efectiva_se_crea_la_solicitud_que_el_runtime_espera() -> None:
    session, execution, project, task = _escenario()

    request = await request_approval_if_needed(
        session,  # type: ignore[arg-type]
        execution=execution,
        project=project,
        category="http_post",
        action={"tool": "http_post", "args": {"url": "https://x"}},
        policy=_POLITICA_EFECTIVA,
    )

    assert request is not None, (
        "el runtime aparcó con la política efectiva y el worker no creó la solicitud: "
        "tarea in_progress para siempre"
    )
    assert request in session.added
    assert str(task.status) == "awaiting_human_approval"
    assert task.assigned_agent_id is None, "la tarea aparcada libera al agente (ADR 0020)"


@pytest.mark.asyncio
async def test_sin_politica_explicita_la_cruda_sigue_diciendo_que_no_hace_falta() -> None:
    """La cara que motivó el kwarg: con la política cruda del proyecto (`None`) la
    función devuelve `None`. No se cambia esa semántica —el ADR 0104 la quiere
    así para no gatear proyectos recién creados—; lo que se cambia es que el
    WORKER ya no llama con la cruda cuando el runtime aparcó con la efectiva."""
    session, execution, project, _task = _escenario()

    request = await request_approval_if_needed(
        session,  # type: ignore[arg-type]
        execution=execution,
        project=project,
        category="http_post",
        action={"tool": "http_post", "args": {}},
    )

    assert request is None
