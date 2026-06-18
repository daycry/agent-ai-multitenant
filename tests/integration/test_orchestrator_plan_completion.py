"""Integration: the orchestrator transitions a plan to
``pending_human_validation`` when its last task reaches ``done``
(sesión 2026-06-18, gap "autoarranque").

The investigation found this behaviour ORPHANED in production: the live
event-driven path (``TaskDispatcher.handle``) only reacted to ``ready``
triggers; nothing recomputed plan progress when a task reached ``done``,
so a plan whose tasks all completed never auto-moved to human
validation. ``transition_to_pending_human_validation`` (a pure function)
was only wired into the in-memory ``plan_runner`` used by demos.

These tests pin the live hook: on a ``task.status_changed`` →
``done`` event, the dispatcher recomputes the owning plan from the DB and
atomically flips ``in_progress`` → ``pending_human_validation`` once every
task is terminal — idempotently (at-least-once stream delivery).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import Plan, Project, Task
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


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(
    sm: async_sessionmaker, *, task_statuses: list[str], plan_status: str = "in_progress"
) -> dict:
    ids: dict = {
        "tenant": uuid4(),
        "project": uuid4(),
        "plan": uuid4(),
        "tasks": [uuid4() for _ in task_statuses],
    }
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, plans, agents,"
                " projects, organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Plan tenant", slug="plan-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Plan project",
                status="active",
                is_template=False,
            )
        )
        await s.flush()
        s.add(
            Plan(
                id=ids["plan"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="Completion plan",
                status=plan_status,
            )
        )
        await s.flush()
        for task_id, st in zip(ids["tasks"], task_statuses, strict=False):
            s.add(
                Task(
                    id=task_id,
                    tenant_id=ids["tenant"],
                    project_id=ids["project"],
                    plan_id=ids["plan"],
                    title="t",
                    status=st,
                    priority="medium",
                )
            )
    return ids


def _done_event(ids: dict, task_index: int = 0) -> TaskEvent:
    return TaskEvent(
        stream_id="1-0",
        type=EVENT_TASK_STATUS_CHANGED,
        tenant_id=str(ids["tenant"]),
        project_id=str(ids["project"]),
        task_id=str(ids["tasks"][task_index]),
        occurred_at="2026-06-18T00:00:00+00:00",
        payload={"old_status": "in_review", "new_status": "done"},
    )


def _dispatcher(sm: async_sessionmaker) -> TaskDispatcher:
    celery_app = build_celery_app(WorkerSettings(broker_url=TEST_REDIS_URL))
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=celery_app,
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL),
    )


async def _plan_status(sm: async_sessionmaker, plan_id: UUID) -> str:
    async with sm() as s:
        plan = (await s.execute(select(Plan).where(Plan.id == plan_id))).scalar_one()
        return plan.status


@pytest.mark.asyncio
async def test_last_task_done_transitions_plan_to_pending_human_validation(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, task_statuses=["done", "done"])

        await _dispatcher(sm).handle(_done_event(ids))

        assert await _plan_status(sm, ids["plan"]) == "pending_human_validation"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_open_task_keeps_plan_in_progress(_migrated: None, admin_database_url: str) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, task_statuses=["done", "in_progress"])

        await _dispatcher(sm).handle(_done_event(ids))

        assert await _plan_status(sm, ids["plan"]) == "in_progress"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_done_event_is_idempotent(_migrated: None, admin_database_url: str) -> None:
    """At-least-once delivery: re-handling the done event must not error nor
    re-transition (the plan is already pending_human_validation)."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, task_statuses=["done", "done"])
        dispatcher = _dispatcher(sm)

        await dispatcher.handle(_done_event(ids))
        await dispatcher.handle(_done_event(ids, task_index=1))

        assert await _plan_status(sm, ids["plan"]) == "pending_human_validation"
    finally:
        await engine.dispose()
