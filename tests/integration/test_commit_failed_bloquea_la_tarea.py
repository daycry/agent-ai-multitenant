"""Un commit fallido deja la tarea `blocked` en la BD, no `in_review` (`task_cv_11`).

Auditoría 2026-09-01 (A-06, C-04). El marcador de commit fallido escribía el
`abort_code` en la ejecución y nada más: la tarea seguía `in_review` y el
reviewer juzgaba un worktree cuyo trabajo no está en la rama del plan. Ahora el
marcador, en la MISMA transacción, mueve la tarea `in_review → blocked` y deja un
evento de auditoría escalado que el panel muestra.

Exige PostgreSQL con migraciones (`admin_database_url`).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import Execution, Project, Task
from api_server.db.models import Organization, TaskAuditEvent
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.execution import _mark_commit_failed

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(sm: async_sessionmaker, *, task_status: str) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4(), "execution": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE task_audit_events, executions, task_dependencies, tasks,"
                " projects, organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Commit falla", slug="commit-falla"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="P",
                status="active",
                is_template=False,
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="implementa el login",
                status=task_status,
                priority="medium",
            )
        )
        s.add(
            Execution(
                id=ids["execution"],
                tenant_id=ids["tenant"],
                task_id=ids["task"],
                status="done",
                output="hecho",
            )
        )
    return ids


@pytest.mark.asyncio
async def test_el_marcador_bloquea_la_tarea_en_review(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, task_status="in_review")

        event = await _mark_commit_failed(
            sm,
            ids["execution"],
            "commit_failed",
            task_id=ids["task"],
            tenant_id=ids["tenant"],
        )

        async with sm() as s:
            task = await s.get(Task, ids["task"])
            execution = await s.get(Execution, ids["execution"])
            audit = list(
                (
                    await s.execute(
                        select(TaskAuditEvent).where(TaskAuditEvent.task_id == ids["task"])
                    )
                ).scalars()
            )
        assert task is not None and str(task.status) == "blocked"
        assert execution is not None and execution.abort_code == "commit_failed"
        assert event is not None and event[1:] == ("in_review", "blocked")
        assert any(
            (a.payload or {}).get("escalated")
            and (a.payload or {}).get("abort_code") == "commit_failed"
            for a in audit
        ), "el bloqueo no dejó rastro escalado en la auditoría"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_una_tarea_ya_movida_por_otro_camino_no_se_toca(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, task_status="done")

        event = await _mark_commit_failed(
            sm,
            ids["execution"],
            "rebase_conflict",
            task_id=ids["task"],
            tenant_id=ids["tenant"],
        )

        async with sm() as s:
            task = await s.get(Task, ids["task"])
            execution = await s.get(Execution, ids["execution"])
        assert event is None
        assert task is not None and str(task.status) == "done"
        assert execution is not None and execution.abort_code == "rebase_conflict"
    finally:
        await engine.dispose()
