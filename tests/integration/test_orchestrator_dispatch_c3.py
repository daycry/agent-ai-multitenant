"""Integration tests — C3 dispatch robustness (F02 / F04 / F09).

Covers the orchestrator dispatch fixes that need a real Postgres + Redis:

F04 (double dispatch): two CONCURRENT deliveries of the same ``ready`` event
must dispatch the run exactly once — the atomic ``UPDATE ... WHERE status='ready'
RETURNING id`` claim lets one delivery win and makes the other a no-op.

F09 (review not idempotent): an ``in_review`` event for a task that already has a
RUNNING execution (the review the worker is conducting) is a no-op — a
re-delivered event does not launch a second review.

F02 (Kanban re-sync on revert): when the broker enqueue fails and the task
reverts to ``ready``, the orchestrator re-emits the ``in_progress -> ready``
status event so the board does not keep showing ``in_progress``.

Harness mirrors ``test_orchestrator_dispatch_atomicity.py`` /
``test_in_review_dispatch.py``. NOT run in the unit lane (DB + broker required).
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import Agent, Execution, Project, Task
from api_server.db.models import Organization
from orchestrator.config import Settings as OrchestratorSettings
from orchestrator.dispatch import TaskDispatcher
from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

TEST_REDIS_URL = "redis://localhost:6379/15"
_SCRIPTED_FINISH = {
    "kind": "scripted",
    "decisions": [{"kind": "finish", "output": "ok"}],
}


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(
    sm: async_sessionmaker,
    *,
    task_status: str = "ready",
    reviewer: bool = False,
    running_execution: bool = False,
) -> dict[str, UUID]:
    """Seed a tenant/project/agent/task. ``reviewer`` attaches an AI reviewer
    and a prior implementer output; ``running_execution`` adds a RUNNING
    execution for the task (the F09 in-flight marker)."""
    ids = {"tenant": uuid4(), "project": uuid4(), "agent": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, agents, projects,"
                " organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="C3 tenant", slug="c3-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="C3 project",
                status="active",
                is_template=False,
                worker_config={"assignment_policy": "load_balanced"},
            )
        )
        await s.flush()
        s.add(
            Agent(
                id=ids["agent"],
                tenant_id=ids["tenant"],
                name="Worker",
                role="reviewer" if reviewer else "backend-dev",
                system_prompt="do the thing",
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
                title="do the thing",
                description="acceptance: it works",
                status=task_status,
                priority="medium",
                reviewer_agent_id=ids["agent"] if reviewer else None,
            )
        )
        await s.flush()
        if running_execution:
            s.add(
                Execution(
                    id=uuid4(),
                    tenant_id=ids["tenant"],
                    task_id=ids["task"],
                    status="running",
                    steps_log=[],
                )
            )
        if reviewer:
            s.add(
                Execution(
                    id=uuid4(),
                    tenant_id=ids["tenant"],
                    task_id=ids["task"],
                    status="done",
                    output="implemented",
                    steps_log=[],
                )
            )
    return ids


def _event(ids: dict[str, UUID], *, new_status: str, old_status: str) -> TaskEvent:
    return TaskEvent(
        stream_id="1-0",
        type=EVENT_TASK_STATUS_CHANGED,
        tenant_id=str(ids["tenant"]),
        project_id=str(ids["project"]),
        task_id=str(ids["task"]),
        occurred_at="2026-06-27T00:00:00+00:00",
        payload={"old_status": old_status, "new_status": new_status},
    )


class _CountingCelery:
    def __init__(self) -> None:
        self.calls = 0

    def send_task(self, *args: object, **kwargs: object) -> None:
        self.calls += 1


class _BrokerDownCelery:
    def __init__(self) -> None:
        self.calls = 0

    def send_task(self, *args: object, **kwargs: object) -> None:
        self.calls += 1
        raise ConnectionError("broker down")


def _dispatcher(
    sm: async_sessionmaker, celery: object, *, redis: Redis | None = None
) -> TaskDispatcher:
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=celery,  # type: ignore[arg-type]
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL, dispatch_queue="default"),
        redis=redis,
    )


# ===========================================================================
# F04 — concurrent deliveries dispatch exactly once
# ===========================================================================
@pytest.mark.asyncio
async def test_concurrent_ready_events_dispatch_once(
    _migrated: None, admin_database_url: str
) -> None:
    """Two concurrent ``ready`` deliveries: the atomic claim lets exactly one
    win — one enqueue, the task ends ``in_progress`` with a single assignee."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        celery = _CountingCelery()
        disp = _dispatcher(sm, celery)

        ev = _event(ids, new_status="ready", old_status="backlog")
        await asyncio.gather(disp.handle(ev), disp.handle(ev))

        # Exactly one run enqueued — the loser of the claim was a no-op.
        assert celery.calls == 1
        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        assert task.status == "in_progress"
        assert task.assigned_agent_id == ids["agent"]
    finally:
        await engine.dispose()


# ===========================================================================
# F09 — review dispatch is idempotent against a running execution
# ===========================================================================
@pytest.mark.asyncio
async def test_in_review_noop_when_execution_already_running(
    _migrated: None, admin_database_url: str
) -> None:
    """An ``in_review`` event for a task with a RUNNING execution does not
    enqueue a second review run."""
    engine = create_async_engine(admin_database_url)
    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.delete("default")
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, task_status="in_review", reviewer=True, running_execution=True)
        celery = _CountingCelery()

        await _dispatcher(sm, celery).handle(
            _event(ids, new_status="in_review", old_status="in_progress")
        )

        assert celery.calls == 0
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_in_review_dispatches_when_no_execution_running(
    _migrated: None, admin_database_url: str
) -> None:
    """Control: with NO running execution the review IS dispatched (the F09
    guard does not block the first delivery)."""
    engine = create_async_engine(admin_database_url)
    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.delete("default")
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, task_status="in_review", reviewer=True, running_execution=False)
        celery = _CountingCelery()

        await _dispatcher(sm, celery).handle(
            _event(ids, new_status="in_review", old_status="in_progress")
        )

        assert celery.calls == 1
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


# ===========================================================================
# F02 — a revert re-emits the in_progress -> ready status event
# ===========================================================================
@pytest.mark.asyncio
async def test_revert_publishes_ready_status_event(
    _migrated: None, admin_database_url: str
) -> None:
    """Broker down → the task reverts to ``ready`` AND an ``in_progress -> ready``
    event is published on ``events:tasks`` so the Kanban re-syncs."""
    engine = create_async_engine(admin_database_url)
    redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await redis.delete("events:tasks")
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        disp = _dispatcher(sm, _BrokerDownCelery(), redis=redis)

        # Snapshot the stream tail, dispatch (which fails + reverts), read new entries.
        await disp.handle(_event(ids, new_status="ready", old_status="backlog"))

        entries = await redis.xrange("events:tasks", "-", "+")
        # The published revert event carries new_status=ready for this task.
        ready_events = [
            f
            for _id, f in entries
            if f.get("task_id") == str(ids["task"])
            and json.loads(f.get("payload", "{}")).get("new_status") == "ready"
        ]
        assert ready_events, "expected a ready status event published on revert"

        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        assert task.status == "ready"
        assert task.assigned_agent_id is None
    finally:
        await redis.delete("events:tasks")
        await redis.aclose()
        await engine.dispose()
