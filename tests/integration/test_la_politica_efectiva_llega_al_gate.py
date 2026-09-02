"""Las dos mitades del gate de aprobación leen la MISMA política (A-01, 2026-09-01).

El runtime aparca con la política EFECTIVA que el worker le inyectó (preset de
plataforma cuando el proyecto no tiene ninguna, ADR 0104). Al cerrar, el worker
tiene que crear la `ApprovalRequest` con ESA política — no con
`project.human_approval_policy` cruda, que en un proyecto creado por API o chat
es `None` y decía «no hace falta»— y, si aun así las dos mitades discrepan,
fallar cerrado con nombre en vez de dejar la ejecución `awaiting_human_approval`
sin solicitud y la tarea `in_progress` para siempre.

Exige PostgreSQL con migraciones (`admin_database_url`): la transición de la
tarea y la solicitud son filas reales.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import ApprovalRequest, Execution, Project, Task
from api_server.db.models import Organization
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.execution import _finalize_and_transition, _RuntimeResult
from workers.run_contract import ExecutionRequest

pytestmark = pytest.mark.integration

_EFECTIVA: dict[str, Any] = {
    "preset": "development",
    "categories": {"http_post": "human_required", "code_changes": "auto"},
}
_LAXA: dict[str, Any] = {"preset": "sandbox", "categories": {"http_post": "auto"}}


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(sm: async_sessionmaker) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4(), "execution": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE approval_requests, task_audit_events, executions,"
                " task_dependencies, tasks, projects, organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Sin política", slug="sin-politica"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Creado por API",
                status="active",
                is_template=False,
                human_approval_policy=None,  # el estado que el ADR 0104 preserva a propósito
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="publica el webhook",
                status="in_progress",
                priority="medium",
            )
        )
        s.add(
            Execution(
                id=ids["execution"],
                tenant_id=ids["tenant"],
                task_id=ids["task"],
                status="running",
            )
        )
    return ids


def _request(ids: dict[str, UUID]) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(ids["tenant"]),
        task_id=str(ids["task"]),
        agent_id=None,
        task={"title": "publica el webhook", "description": ""},
        model={"kind": "scripted"},
    )


def _aparcado() -> _RuntimeResult:
    return _RuntimeResult(
        status="awaiting_human_approval",
        abort_code=None,
        output="quiero hacer un POST",
        iterations=1,
        steps=[],
        usage={},
    )


_ACCION = {"category": "http_post", "action": {"tool": "http_post", "args": {"url": "https://x"}}}


@pytest.mark.asyncio
async def test_el_worker_crea_la_solicitud_con_la_politica_efectiva(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        await _finalize_and_transition(
            sm,
            _request(ids),
            execution_id=ids["execution"],
            task_id=ids["task"],
            tenant_id=ids["tenant"],
            result=_aparcado(),
            approval=_ACCION,
            approval_policy=_EFECTIVA,
        )

        async with sm() as s:
            solicitudes = list(
                (
                    await s.execute(
                        select(ApprovalRequest).where(
                            ApprovalRequest.execution_id == ids["execution"]
                        )
                    )
                ).scalars()
            )
            task = await s.get(Task, ids["task"])
        assert len(solicitudes) == 1, "el run aparcó y nadie creó la solicitud"
        assert task is not None and str(task.status) == "awaiting_human_approval"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_si_las_dos_mitades_discrepan_se_falla_cerrado_y_la_tarea_sigue(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        await _finalize_and_transition(
            sm,
            _request(ids),
            execution_id=ids["execution"],
            task_id=ids["task"],
            tenant_id=ids["tenant"],
            result=_aparcado(),
            approval=_ACCION,
            approval_policy=_LAXA,  # el worker cree que no hacía falta
        )

        async with sm() as s:
            execution = await s.get(Execution, ids["execution"])
            task = await s.get(Task, ids["task"])
        assert execution is not None
        assert str(execution.status) == "failed"
        assert execution.abort_code == "approval_policy_mismatch"
        assert execution.completed_at is not None, (
            "una ejecución sin solicitud no puede quedar viva"
        )
        assert task is not None and str(task.status) != "in_progress", (
            "la tarea quedó reclamada para siempre"
        )
    finally:
        await engine.dispose()
