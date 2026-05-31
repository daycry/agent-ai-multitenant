"""Integration tests: orchestrator human route (Plan 16 task_16_05).

When a ``ready`` task's assignee Agent is ``agent_type='human'`` the
orchestrator must NOT request a runtime container from the pool. Instead it:

  1. resolves the concrete User from ``human_agent_config.assigned_user_id``;
  2. creates a ``HumanTaskAssignment`` (status ``pending_acceptance``);
  3. transitions the task ``ready -> assigned_to_human`` via the §7.2 state
     machine (task_16_04) — legal ONLY because the assignee is a Human Agent;
  4. enqueues a Plan 10 ``notification_dispatcher.dispatch_event`` fan-out so
     the assigned user is notified.

An AI-assigned task keeps the existing runtime-pool path untouched: it lands on
``in_progress`` and a ``workers.run_execution`` is enqueued — NO assignment row,
NO notification.

The suite drives the REAL :class:`TaskDispatcher` against the REAL Postgres +
Redis (the dev stack on PG 15432 / Redis 6379 DB 15) — no mocks. Each test
seeds with unique uuids (no TRUNCATE) so the cases are isolated. The
``@pytest.mark.cross_tenant`` test proves the human-assignee resolution is
tenant-scoped: a stale event whose ``tenant_id`` is wrong for the task's tenant
never lets a foreign session route the task. The whole human-route create +
transition is exercised against the live DB row, and the migration's
reversibility is asserted.
"""

from __future__ import annotations

import base64
import json
from uuid import UUID

import pytest
from alembic import command
from api_server.db.domain import Agent, HumanTaskAssignment, Task
from orchestrator.config import Settings as OrchestratorSettings
from orchestrator.dispatch import TaskDispatcher
from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from uuid6 import uuid7
from workers.celery_app import build_celery_app
from workers.config import Settings as WorkerSettings

pytestmark = pytest.mark.integration

TEST_REDIS_URL = "redis://localhost:6379/15"

# A one-step scripted model for the AI agent: the dispatcher forwards the
# `model_config` verbatim into the worker payload.
_SCRIPTED_FINISH = {
    "kind": "scripted",
    "decisions": [{"kind": "finish", "output": "done"}],
}

# The two Celery lanes the dispatcher publishes onto: the AI run goes to
# `default`; the human-assignment notification fan-out to `notifications.priority`.
_DISPATCH_QUEUE = "default"
_NOTIFY_QUEUE = "notifications.priority"


# ---------------------------------------------------------------------------
# Seeding (BYPASSRLS migrations role — the orchestrator runs BYPASSRLS too)
# ---------------------------------------------------------------------------
async def _seed(
    sm: async_sessionmaker,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user_id: UUID,
    human_agent_id: UUID,
    ai_agent_id: UUID,
    human_task_id: UUID,
    ai_task_id: UUID,
) -> None:
    """Insert a tenant, project, user, a human + an AI agent and two ready tasks."""
    async with sm() as s, s.begin():
        await s.execute(
            text("INSERT INTO organizations (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": "Route tenant", "slug": f"route-{tenant_id.hex}"},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, email, password_hash)"
                " VALUES (:id, :email, 'argon2-placeholder')"
            ),
            {"id": user_id, "email": f"worker-{user_id.hex}@route.test"},
        )
        await s.execute(
            text(
                "INSERT INTO projects (id, tenant_id, name, status, worker_config)"
                " VALUES (:id, :tid, 'Route project', 'active',"
                ' \'{"assignment_policy": "manual"}\'::jsonb)'
            ),
            {"id": project_id, "tid": tenant_id},
        )
        # A human Agent + its config (assigned to the concrete user).
        s.add(
            Agent(
                id=human_agent_id,
                tenant_id=tenant_id,
                name="Legal Reviewer",
                role="reviewer",
                system_prompt="You review legal copy.",
                agent_type="human",
                scope="project_local",
                project_id=project_id,
            )
        )
        # An AI Agent carrying its scripted model spec (assignment_policy=manual
        # picks the task's preset assigned_agent_id).
        s.add(
            Agent(
                id=ai_agent_id,
                tenant_id=tenant_id,
                name="Backend Dev",
                role="backend_dev",
                system_prompt="You write code.",
                agent_type="ai",
                scope="project_local",
                project_id=project_id,
                model_config=_SCRIPTED_FINISH,
            )
        )
        await s.flush()
        await s.execute(
            text(
                "INSERT INTO human_agent_config"
                " (id, tenant_id, agent_id, assignment_mode, assigned_user_id,"
                "  acceptance_timeout_hours)"
                " VALUES (:id, :tid, :aid, 'specific_user', :uid, 24)"
            ),
            {"id": uuid7(), "tid": tenant_id, "aid": human_agent_id, "uid": user_id},
        )
        # A ready task assigned to the human agent.
        s.add(
            Task(
                id=human_task_id,
                tenant_id=tenant_id,
                project_id=project_id,
                title="Legal review of the contract",
                status="ready",
                priority="high",
                assigned_agent_id=human_agent_id,
            )
        )
        # A ready task assigned to the AI agent (manual policy → this preset).
        s.add(
            Task(
                id=ai_task_id,
                tenant_id=tenant_id,
                project_id=project_id,
                title="Implement the endpoint",
                status="ready",
                priority="medium",
                assigned_agent_id=ai_agent_id,
            )
        )


def _ready_event(*, tenant_id: UUID, project_id: UUID, task_id: UUID) -> TaskEvent:
    return TaskEvent(
        stream_id="1-0",
        type=EVENT_TASK_STATUS_CHANGED,
        tenant_id=str(tenant_id),
        project_id=str(project_id),
        task_id=str(task_id),
        occurred_at="2026-05-31T00:00:00+00:00",
        payload={"old_status": "backlog", "new_status": "ready"},
    )


def _dispatcher(sm: async_sessionmaker) -> TaskDispatcher:
    celery_app = build_celery_app(WorkerSettings(broker_url=TEST_REDIS_URL))
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=celery_app,
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL),
    )


def _engine_sm(url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _drain(redis: Redis, queue: str) -> list[dict]:
    raw = await redis.lrange(queue, 0, -1)
    await redis.delete(queue)
    return [json.loads(item) for item in raw]


def _decoded_task_names(messages: list[dict]) -> list[str]:
    return [m["headers"]["task"] for m in messages]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]


# ===========================================================================
# Human route: NO container, a HumanTaskAssignment row, task -> assigned_to_human,
# a notification enqueued.
# ===========================================================================
@pytest.mark.asyncio
async def test_human_task_routes_to_assignment_not_container(
    _migrated: None, admin_database_url: str
) -> None:
    engine, sm = _engine_sm(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    ids = {k: uuid7() for k in ("tenant", "project", "user", "human_agent", "ai_agent", "ht", "at")}
    try:
        await _seed(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            user_id=ids["user"],
            human_agent_id=ids["human_agent"],
            ai_agent_id=ids["ai_agent"],
            human_task_id=ids["ht"],
            ai_task_id=ids["at"],
        )
        await redis.delete(_DISPATCH_QUEUE)
        await redis.delete(_NOTIFY_QUEUE)

        await _dispatcher(sm).handle(
            _ready_event(tenant_id=ids["tenant"], project_id=ids["project"], task_id=ids["ht"])
        )

        # NO runtime container requested: the dispatch lane is empty.
        assert await redis.llen(_DISPATCH_QUEUE) == 0

        # The task moved to assigned_to_human (NOT in_progress) and did NOT
        # take an AI agent assignment overwrite — it stays the human agent.
        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["ht"]))).scalar_one()
        assert task.status == "assigned_to_human"
        assert task.assigned_agent_id == ids["human_agent"]
        # A human task starts no execution.
        assert task.started_at is None

        # A HumanTaskAssignment row exists, resolved to the concrete user.
        async with sm() as s:
            assignment = (
                await s.execute(
                    select(HumanTaskAssignment).where(HumanTaskAssignment.task_id == ids["ht"])
                )
            ).scalar_one()
        assert assignment.tenant_id == ids["tenant"]
        assert assignment.human_agent_id == ids["human_agent"]
        assert assignment.assigned_to_user_id == ids["user"]
        assert assignment.status == "pending_acceptance"

        # A notification fan-out was enqueued onto the priority lane.
        notify_messages = await _drain(redis, _NOTIFY_QUEUE)
        assert _decoded_task_names(notify_messages) == ["notification_dispatcher.dispatch_event"]
        body = json.loads(base64.b64decode(notify_messages[0]["body"]))
        args, _kwargs, _embed = body
        event = args[0]
        assert event["event_type"] == "human_task_assigned"
        assert event["tenant_id"] == str(ids["tenant"])
        assert event["context"]["task_id"] == str(ids["ht"])
        assert event["context"]["assigned_to_user_id"] == str(ids["user"])
        assert event["context"]["task_title"] == "Legal review of the contract"
    finally:
        await redis.delete(_DISPATCH_QUEUE)
        await redis.delete(_NOTIFY_QUEUE)
        await redis.aclose()
        await engine.dispose()


# ===========================================================================
# AI route: unchanged — container requested, NO assignment, NO notification.
# ===========================================================================
@pytest.mark.asyncio
async def test_ai_task_still_goes_the_container_route(
    _migrated: None, admin_database_url: str
) -> None:
    engine, sm = _engine_sm(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    ids = {k: uuid7() for k in ("tenant", "project", "user", "human_agent", "ai_agent", "ht", "at")}
    try:
        await _seed(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            user_id=ids["user"],
            human_agent_id=ids["human_agent"],
            ai_agent_id=ids["ai_agent"],
            human_task_id=ids["ht"],
            ai_task_id=ids["at"],
        )
        await redis.delete(_DISPATCH_QUEUE)
        await redis.delete(_NOTIFY_QUEUE)

        await _dispatcher(sm).handle(
            _ready_event(tenant_id=ids["tenant"], project_id=ids["project"], task_id=ids["at"])
        )

        # The AI task moved to in_progress and was assigned its AI agent.
        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["at"]))).scalar_one()
        assert task.status == "in_progress"
        assert task.assigned_agent_id == ids["ai_agent"]
        assert task.started_at is not None

        # A worker run was enqueued onto the dispatch lane.
        dispatch_messages = await _drain(redis, _DISPATCH_QUEUE)
        assert _decoded_task_names(dispatch_messages) == ["workers.run_execution"]
        body = json.loads(base64.b64decode(dispatch_messages[0]["body"]))
        _args, kwargs, _embed = body
        request = kwargs["request"]
        assert request["task_id"] == str(ids["at"])
        assert request["agent_id"] == str(ids["ai_agent"])
        assert request["model"] == _SCRIPTED_FINISH

        # NO human-assignment row, NO notification fan-out for the AI task.
        async with sm() as s:
            count = (
                (
                    await s.execute(
                        select(HumanTaskAssignment).where(HumanTaskAssignment.task_id == ids["at"])
                    )
                )
                .scalars()
                .all()
            )
        assert count == []
        assert await redis.llen(_NOTIFY_QUEUE) == 0
    finally:
        await redis.delete(_DISPATCH_QUEUE)
        await redis.delete(_NOTIFY_QUEUE)
        await redis.aclose()
        await engine.dispose()


# ===========================================================================
# A stale event with no live ready task is a no-op (no assignment, no enqueue).
# ===========================================================================
@pytest.mark.asyncio
async def test_human_route_is_noop_when_task_not_ready(
    _migrated: None, admin_database_url: str
) -> None:
    engine, sm = _engine_sm(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    ids = {k: uuid7() for k in ("tenant", "project", "user", "human_agent", "ai_agent", "ht", "at")}
    try:
        await _seed(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            user_id=ids["user"],
            human_agent_id=ids["human_agent"],
            ai_agent_id=ids["ai_agent"],
            human_task_id=ids["ht"],
            ai_task_id=ids["at"],
        )
        # Move the human task past ready — the event below is now stale.
        async with sm() as s, s.begin():
            t = (await s.execute(select(Task).where(Task.id == ids["ht"]))).scalar_one()
            t.status = "assigned_to_human"
        await redis.delete(_NOTIFY_QUEUE)

        await _dispatcher(sm).handle(
            _ready_event(tenant_id=ids["tenant"], project_id=ids["project"], task_id=ids["ht"])
        )

        # No second assignment row, no duplicate notification.
        async with sm() as s:
            rows = (
                (
                    await s.execute(
                        select(HumanTaskAssignment).where(HumanTaskAssignment.task_id == ids["ht"])
                    )
                )
                .scalars()
                .all()
            )
        assert rows == []
        assert await redis.llen(_NOTIFY_QUEUE) == 0
    finally:
        await redis.delete(_NOTIFY_QUEUE)
        await redis.aclose()
        await engine.dispose()


# ===========================================================================
# Cross-tenant: the human-assignee resolution carries an explicit tenant
# predicate, so a stale event whose tenant_id is wrong cannot route the task.
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_human_route_resolution_is_tenant_scoped(
    _migrated: None, admin_database_url: str
) -> None:
    """The human task belongs to tenant A. The dispatcher resolves the assignee
    Agent with ``Agent.tenant_id == task.tenant_id`` (the BYPASSRLS orchestrator
    cannot lean on RLS), so the human route is keyed on the task's OWN tenant,
    never a foreign one. We verify the route fires for the real (tenant-A) task
    and that a task whose assignee belongs to a DIFFERENT tenant is NOT treated
    as a human task (the cross-tenant agent is invisible to the predicate, so it
    falls through to the AI route and finds no AI candidate)."""
    engine, sm = _engine_sm(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    ids = {k: uuid7() for k in ("tenant", "project", "user", "human_agent", "ai_agent", "ht", "at")}
    tenant_b = uuid7()
    project_b = uuid7()
    cross_task = uuid7()
    try:
        await _seed(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            user_id=ids["user"],
            human_agent_id=ids["human_agent"],
            ai_agent_id=ids["ai_agent"],
            human_task_id=ids["ht"],
            ai_task_id=ids["at"],
        )
        # Tenant B with a project + a task whose assigned_agent_id points at
        # tenant A's human agent — a forged cross-tenant assignment.
        async with sm() as s, s.begin():
            await s.execute(
                text("INSERT INTO organizations (id, name, slug) VALUES (:id, :name, :slug)"),
                {"id": tenant_b, "name": "Tenant B", "slug": f"b-{tenant_b.hex}"},
            )
            await s.execute(
                text(
                    "INSERT INTO projects (id, tenant_id, name, status)"
                    " VALUES (:id, :tid, 'B project', 'active')"
                ),
                {"id": project_b, "tid": tenant_b},
            )
            await s.execute(
                text(
                    "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority,"
                    " assigned_agent_id)"
                    " VALUES (:id, :tid, :pid, 'B task', 'ready', 'medium', :aid)"
                ),
                {
                    "id": cross_task,
                    "tid": tenant_b,
                    "pid": project_b,
                    "aid": ids["human_agent"],  # tenant A's human agent
                },
            )
        await redis.delete(_DISPATCH_QUEUE)
        await redis.delete(_NOTIFY_QUEUE)

        # Tenant B's task references tenant A's human agent. The tenant-scoped
        # resolution does NOT see it (Agent.tenant_id == task.tenant_id == B),
        # so it is NOT routed as a human task → falls through to the AI route →
        # no AI candidate in tenant B → no dispatch, no assignment, no notify.
        await _dispatcher(sm).handle(
            _ready_event(tenant_id=tenant_b, project_id=project_b, task_id=cross_task)
        )

        async with sm() as s:
            cross = (await s.execute(select(Task).where(Task.id == cross_task))).scalar_one()
            cross_rows = (
                (
                    await s.execute(
                        select(HumanTaskAssignment).where(HumanTaskAssignment.task_id == cross_task)
                    )
                )
                .scalars()
                .all()
            )
        assert cross.status == "ready"  # untouched — no human route, no AI agent
        assert cross_rows == []
        assert await redis.llen(_DISPATCH_QUEUE) == 0
        assert await redis.llen(_NOTIFY_QUEUE) == 0

        # The genuine tenant-A human task DOES route through the human path.
        await _dispatcher(sm).handle(
            _ready_event(tenant_id=ids["tenant"], project_id=ids["project"], task_id=ids["ht"])
        )
        async with sm() as s:
            ht = (await s.execute(select(Task).where(Task.id == ids["ht"]))).scalar_one()
            ht_assignment = (
                await s.execute(
                    select(HumanTaskAssignment).where(HumanTaskAssignment.task_id == ids["ht"])
                )
            ).scalar_one()
        assert ht.status == "assigned_to_human"
        assert ht_assignment.tenant_id == ids["tenant"]
        assert ht_assignment.assigned_to_user_id == ids["user"]
        assert await redis.llen(_NOTIFY_QUEUE) == 1
    finally:
        await redis.delete(_DISPATCH_QUEUE)
        await redis.delete(_NOTIFY_QUEUE)
        await redis.aclose()
        await engine.dispose()


# ===========================================================================
# Migration reversibility: 0069 up -> down -> up restores cleanly.
# ===========================================================================
def test_migration_0069_is_reversible(alembic_config: object) -> None:
    """0069 down to 0068 (the table gone), back up (the table back) — proving
    the human_task_assignments migration is fully reversible. The plan-wide
    reversibility proof target is 0040; here we exercise the new head."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, "0068_human_work_sessions")  # type: ignore[arg-type]
    command.upgrade(alembic_config, "0069_human_task_assignments")  # type: ignore[arg-type]
