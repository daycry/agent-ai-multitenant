"""Integration test — prod-06 task_prod06_budget_03 (db-5).

The hot dispatch path (`_route_ai`) must not start executions for tasks whose
project was soft-deleted. The cancellation cascade (task_prod06_cancel_02)
already cancels in-flight work on soft-delete, but a stale ``ready`` event could
still arrive afterwards; the dispatcher loads the project with a
``deleted_at IS NULL`` filter and skips (returns None, logs) when it is gone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import Agent, Project, Task
from api_server.db.models import Organization
from orchestrator.config import Settings as OrchestratorSettings
from orchestrator.dispatch import TaskDispatcher
from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.celery_app import build_celery_app
from workers.config import Settings as WorkerSettings

pytestmark = pytest.mark.integration

TEST_REDIS_URL = "redis://localhost:6379/15"
_SCRIPTED_FINISH = {
    "kind": "scripted",
    "decisions": [{"kind": "finish", "output": "done"}],
}


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(
    sm: async_sessionmaker, *, project_deleted: bool, project_status: str = "active"
) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "agent": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, agents, projects,"
                " organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="T", slug="t-bud03"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="P",
                status=project_status,
                is_template=False,
                worker_config={"assignment_policy": "load_balanced"},
                deleted_at=datetime.now(UTC) if project_deleted else None,
            )
        )
        await s.flush()
        s.add(
            Agent(
                id=ids["agent"],
                tenant_id=ids["tenant"],
                name="Writer",
                role="backend-dev",
                system_prompt="x",
                agent_type="ai",
                scope="project_local",
                project_id=ids["project"],
                model_config=_SCRIPTED_FINISH,
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="t",
                description="d",
                status="ready",
                priority="medium",
            )
        )
    return ids


def _dispatcher(sm: async_sessionmaker) -> TaskDispatcher:
    celery_app = build_celery_app(WorkerSettings(broker_url=TEST_REDIS_URL))
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=celery_app,
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL),
    )


def _ready_event(ids: dict[str, UUID]) -> TaskEvent:
    return TaskEvent(
        stream_id="1-0",
        type=EVENT_TASK_STATUS_CHANGED,
        tenant_id=str(ids["tenant"]),
        project_id=str(ids["project"]),
        task_id=str(ids["task"]),
        occurred_at="2026-06-25T00:00:00+00:00",
        payload={"old_status": "backlog", "new_status": "ready"},
    )


@pytest.mark.asyncio
async def test_dispatch_skips_task_of_soft_deleted_project(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, project_deleted=True)

        await _dispatcher(sm).handle(_ready_event(ids))

        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        # Not dispatched: still ready, no agent assigned, no execution started.
        assert task.status == "ready"
        assert task.assigned_agent_id is None
        assert task.started_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_skips_task_of_paused_project(
    _migrated: None, admin_database_url: str
) -> None:
    """P1-01: un proyecto `paused` no despacha — la tarea queda `ready` y se
    re-despacha cuando el proyecto vuelva a `active`."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, project_deleted=False, project_status="paused")

        await _dispatcher(sm).handle(_ready_event(ids))

        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        assert task.status == "ready"
        assert task.assigned_agent_id is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_proceeds_for_live_project(_migrated: None, admin_database_url: str) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, project_deleted=False)

        await _dispatcher(sm).handle(_ready_event(ids))

        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        # Control: a live project dispatches normally.
        assert task.status == "in_progress"
        assert task.assigned_agent_id == ids["agent"]
    finally:
        await engine.dispose()
