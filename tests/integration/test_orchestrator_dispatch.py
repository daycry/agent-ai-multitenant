"""Integration tests: the orchestrator dispatches tasks (task_02_31).

The third task of Plan 02 Fase G closes the front of the pipeline.
`TaskDispatcher` is the orchestrator's event handler: on a task that
has gone `ready` it picks an agent (the assignment policies of
task_02_03), moves the task to `in_progress`, and enqueues the worker's
`run_execution` Celery task — the muscle built in task_02_30.

Tests 1-4 need only Postgres + Redis (no container). The last test
drives the enqueued worker task for real and so also needs Docker; it
carries its own `requires_docker` marker.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import Agent, ExecutionStatus, Project, Task
from api_server.db.execution_repo import list_executions_for_task
from api_server.db.models import Organization
from orchestrator.config import Settings as OrchestratorSettings
from orchestrator.dispatch import TaskDispatcher
from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.celery_app import build_celery_app
from workers.config import Settings as WorkerSettings
from workers.config import reset_settings_cache

import docker

from ._docker_helpers import docker_client, requires_docker

pytestmark = pytest.mark.integration

_IMAGE = "agent-runtime:v1"
TEST_REDIS_URL = "redis://localhost:6379/15"

# A one-step scripted model: the agent carries its ModelClient spec in
# `model_config`; the dispatcher forwards it verbatim into the payload.
_SCRIPTED_FINISH = {
    "kind": "scripted",
    "decisions": [{"kind": "finish", "output": "the sea poem"}],
}


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(sm: async_sessionmaker, *, task_status: str = "ready") -> dict[str, UUID]:
    """Insert a tenant / project / agent / task; return their ids."""
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "agent": uuid4(),
        "task": uuid4(),
    }
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, agents, projects,"
                " organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Dispatch tenant", slug="dispatch-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Dispatch project",
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
                name="Writer",
                role="backend-dev",
                system_prompt="You write things.",
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
                title="Write a sea poem",
                description="exercise the dispatch pipeline",
                status=task_status,
                priority="medium",
            )
        )
    return ids


def _ready_event(ids: dict[str, UUID], *, new_status: str = "ready") -> TaskEvent:
    return TaskEvent(
        stream_id="1-0",
        type=EVENT_TASK_STATUS_CHANGED,
        tenant_id=str(ids["tenant"]),
        project_id=str(ids["project"]),
        task_id=str(ids["task"]),
        occurred_at="2026-05-22T00:00:00+00:00",
        payload={"old_status": "backlog", "new_status": new_status},
    )


def _dispatcher(sm: async_sessionmaker) -> TaskDispatcher:
    celery_app = build_celery_app(WorkerSettings(broker_url=TEST_REDIS_URL))
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=celery_app,
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL),
    )


async def _drain_queue(redis: Redis, queue: str) -> list[dict]:
    """Pop every Celery message off `queue`, decoded."""
    raw = await redis.lrange(queue, 0, -1)
    await redis.delete(queue)
    return [json.loads(item) for item in raw]


# ---------------------------------------------------------------------------
# Dispatch — task transition + agent assignment
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dispatch_moves_task_to_in_progress_and_assigns_an_agent(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        await redis.delete("default")

        await _dispatcher(sm).handle(_ready_event(ids))

        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        assert task.status == "in_progress"
        assert task.assigned_agent_id == ids["agent"]
        assert task.started_at is not None
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_enqueues_the_worker_celery_task(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        await redis.delete("default")

        await _dispatcher(sm).handle(_ready_event(ids))

        messages = await _drain_queue(redis, "default")
        assert len(messages) == 1
        message = messages[0]
        assert message["headers"]["task"] == "workers.run_execution"
        # The body carries the execution request the worker deserialises.
        body = json.loads(base64.b64decode(message["body"]))
        _args, kwargs, _embed = body
        request = kwargs["request"]
        assert request["task_id"] == str(ids["task"])
        assert request["tenant_id"] == str(ids["tenant"])
        assert request["agent_id"] == str(ids["agent"])
        assert request["model"] == _SCRIPTED_FINISH
        assert request["task"]["title"] == "Write a sea poem"
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_ignores_a_non_ready_event(_migrated: None, admin_database_url: str) -> None:
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, task_status="backlog")
        await redis.delete("default")

        # A status change to in_review is not a dispatch trigger.
        await _dispatcher(sm).handle(_ready_event(ids, new_status="in_review"))

        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        assert task.status == "backlog"
        assert await redis.llen("default") == 0
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_skips_a_task_not_in_ready_state(
    _migrated: None, admin_database_url: str
) -> None:
    """A stale `ready` event for a task already moved on is a no-op —
    the dispatcher re-checks the live task state, not just the event."""
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, task_status="in_progress")
        await redis.delete("default")

        await _dispatcher(sm).handle(_ready_event(ids))

        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        # Untouched — no second assignment, no duplicate enqueue.
        assert task.assigned_agent_id is None
        assert await redis.llen("default") == 0
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


# ---------------------------------------------------------------------------
# The enqueued worker task actually conducts the execution
# ---------------------------------------------------------------------------
@pytest.fixture()
def _agent_runtime_image() -> None:
    """Skip cleanly if agent-runtime:v1 has not been built on this host."""
    client = docker_client()
    try:
        client.images.get(_IMAGE)
    except docker.errors.ImageNotFound:  # pragma: no cover - env-dependent
        pytest.skip(f"{_IMAGE} not built — run: docker build -t {_IMAGE} ...")
    finally:
        client.close()


async def _seed_via(url: str) -> dict[str, UUID]:
    """Seed against a fresh engine — for the sync test below, which
    cannot reuse an engine across separate `asyncio.run` calls."""
    engine = create_async_engine(url)
    try:
        return await _seed(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


async def _execution_statuses(url: str, task_id: UUID) -> list[str]:
    engine = create_async_engine(url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s:
            return [e.status for e in await list_executions_for_task(s, task_id)]
    finally:
        await engine.dispose()


@requires_docker
def test_run_execution_celery_task_conducts_the_execution(
    _migrated: None,
    _agent_runtime_image: None,
    admin_database_url: str,
) -> None:
    """`workers.run_execution` — the dispatch target — builds its DB and
    Redis handles from `Settings` and conducts a real execution.

    Sync, like Celery itself calls the task: it owns its event loop via
    `asyncio.run`, so the test must not run inside one.
    """
    from workers.tasks import run_execution

    ids = asyncio.run(_seed_via(admin_database_url))

    os.environ["WORKERS_DATABASE_URL"] = admin_database_url
    os.environ["WORKERS_EVENTS_REDIS_URL"] = TEST_REDIS_URL
    reset_settings_cache()
    try:
        outcome = run_execution(
            {
                "tenant_id": str(ids["tenant"]),
                "task_id": str(ids["task"]),
                "agent_id": str(ids["agent"]),
                "task": {"id": str(ids["task"]), "title": "Write a sea poem", "description": ""},
                "model": _SCRIPTED_FINISH,
                "budgets": None,
            }
        )
    finally:
        os.environ.pop("WORKERS_DATABASE_URL", None)
        os.environ.pop("WORKERS_EVENTS_REDIS_URL", None)
        reset_settings_cache()

    assert outcome["status"] == ExecutionStatus.DONE
    assert json.loads(json.dumps(outcome)) == outcome  # JSON-safe result

    statuses = asyncio.run(_execution_statuses(admin_database_url, ids["task"]))
    assert statuses == [ExecutionStatus.DONE]
