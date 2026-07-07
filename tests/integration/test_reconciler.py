"""Integration test — convergence reconciler (audit C3 / P0.6,
``workers.reconcile_pipeline_state``).

Exercises the three DB passes of ``_reconcile_pipeline_state_async`` end to end on
the throwaway PG stack, with an in-memory fake Redis capturing the re-emitted events:

  (a) a task stuck ``in_progress`` whose last execution is terminal (and settled past
      the age threshold) is transitioned off ``in_progress`` (dag_01 policy) and its
      ``task.status_changed`` re-emitted; a task whose run JUST finished is left alone.
  (b) an ``in_review`` task with an AI reviewer, no live/recent review run, sitting
      past the threshold, gets its ``in_review`` event re-announced.
  (c) an ``in_progress`` plan whose tasks are ALL terminal flips to
      ``pending_human_validation``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import Plan, Task, TaskStatus
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


class _FakeRedis:
    """Captures ``xadd`` calls so the test asserts the re-emitted task events."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def xadd(self, stream: str, fields: dict[str, Any], **_kw: Any) -> None:
        self.events.append({"stream": stream, **fields})

    async def aclose(self) -> None:  # pragma: no cover - injected, never closed here
        ...


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str):
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import get_settings, reset_settings_cache

    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "reviewer": uuid4(),
        # (a) stuck task: in_progress, last execution terminal + old.
        "task_stuck": uuid4(),
        "exec_stuck": uuid4(),
        # (a) fresh task: in_progress, last execution terminal but just now → untouched.
        "task_fresh": uuid4(),
        "exec_fresh": uuid4(),
        # (b) orphan review: in_review + AI reviewer + no live/recent run.
        "task_review": uuid4(),
        # (c) complete plan: in_progress, every task done.
        "plan_done": uuid4(),
        "task_plan_a": uuid4(),
        "task_plan_b": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, plans, agents, projects, organizations"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T rec', 't-reconciler')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        # An AI reviewer agent for case (b). `system_prompt` is NOT NULL.
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, project_id, name, agent_type, scope, role, system_prompt)"
            " VALUES ($1, $2, $3, 'Rev', 'ai', 'project_local', 'reviewer', 'You review tasks.')",
            ids["reviewer"],
            ids["tenant"],
            ids["project"],
        )
        # (a) stuck + fresh in_progress tasks (started long ago so both pass the
        # task-age pre-filter; the execution recency is what differs).
        for tid in (ids["task_stuck"], ids["task_fresh"]):
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority,"
                " started_at) VALUES ($1, $2, $3, 'task', 'in_progress', 'medium',"
                " now() - interval '1 hour')",
                tid,
                ids["tenant"],
                ids["project"],
            )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, started_at, completed_at)"
            " VALUES ($1, $2, $3, 'failed', now() - interval '1 hour',"
            " now() - interval '30 minutes')",
            ids["exec_stuck"],
            ids["tenant"],
            ids["task_stuck"],
        )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, started_at, completed_at)"
            " VALUES ($1, $2, $3, 'done', now() - interval '2 minutes', now())",
            ids["exec_fresh"],
            ids["tenant"],
            ids["task_fresh"],
        )
        # (b) in_review task with the AI reviewer, sitting > threshold (updated_at old),
        # no executions at all → review dispatch was lost.
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority,"
            " reviewer_agent_id, updated_at) VALUES ($1, $2, $3, 'rev-task', 'in_review',"
            " 'medium', $4, now() - interval '20 minutes')",
            ids["task_review"],
            ids["tenant"],
            ids["project"],
            ids["reviewer"],
        )
        # (c) in_progress plan with two done tasks → should close.
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status)"
            " VALUES ($1, $2, $3, 'Done plan', 'in_progress')",
            ids["plan_done"],
            ids["tenant"],
            ids["project"],
        )
        for tid in (ids["task_plan_a"], ids["task_plan_b"]):
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority)"
                " VALUES ($1, $2, $3, $4, 'plan-task', 'done', 'medium')",
                tid,
                ids["tenant"],
                ids["project"],
                ids["plan_done"],
            )
        return ids
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_reconcile_pipeline_state(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    from workers.maintenance import _reconcile_pipeline_state_async

    ids = await _seed(migrations_pg_dsn)
    redis = _FakeRedis()

    result = await _reconcile_pipeline_state_async(
        workers_settings,  # type: ignore[arg-type]
        redis=redis,
        now=datetime.now(UTC),
        stuck_task_min_age=timedelta(minutes=5),
        review_min_age=timedelta(minutes=5),
    )

    assert result == {"stuck_tasks": 1, "orphan_reviews": 1, "completed_plans": 1}

    engine = create_async_engine(workers_settings.database_url)  # type: ignore[attr-defined]
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            task_stuck = await session.get(Task, ids["task_stuck"])
            task_fresh = await session.get(Task, ids["task_fresh"])
            plan_done = await session.get(Plan, ids["plan_done"])

        # (a) the stuck task (terminal failed run) is moved off in_progress → blocked;
        # the fresh task (run just finished) is left for the worker's post-processing.
        assert task_stuck is not None and task_stuck.status == TaskStatus.BLOCKED.value
        assert task_fresh is not None and task_fresh.status == TaskStatus.IN_PROGRESS.value
        # (c) the plan with all-done tasks closes to pending_human_validation.
        assert plan_done is not None and plan_done.status == "pending_human_validation"
    finally:
        await engine.dispose()

    # The re-emitted events landed on the task event stream: the stuck task's
    # in_progress→blocked and the orphan review's re-announce (in_review→in_review).
    new_statuses = [
        json.loads(e["payload"]).get("new_status")
        for e in redis.events
        if e.get("type") == "task.status_changed"
    ]
    assert TaskStatus.BLOCKED.value in new_statuses
    assert new_statuses.count(TaskStatus.IN_REVIEW.value) == 1


async def _seed_stuck_dag_plan(dsn: str) -> dict[str, UUID]:
    """A→B: A blocked (falló), B backlog dep de A. B nunca puede avanzar (la
    promoción DAG exige deps `done`) → el plan debe escalar a `blocked` (prod-06 A1)."""
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "plan": uuid4(),
        "task_a": uuid4(),
        "task_b": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE task_dependencies, executions, tasks, plans, agents, projects,"
            " organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T a1', 't-a1')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status)"
            " VALUES ($1, $2, $3, 'Stuck DAG', 'in_progress')",
            ids["plan"],
            ids["tenant"],
            ids["project"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority)"
            " VALUES ($1, $2, $3, $4, 'A', 'blocked', 'medium')",
            ids["task_a"],
            ids["tenant"],
            ids["project"],
            ids["plan"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority)"
            " VALUES ($1, $2, $3, $4, 'B', 'backlog', 'medium')",
            ids["task_b"],
            ids["tenant"],
            ids["project"],
            ids["plan"],
        )
        await conn.execute(
            "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES ($1, $2)",
            ids["task_b"],
            ids["task_a"],
        )
        return ids
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_reconciler_escalates_plan_stuck_behind_blocked_dependency(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """prod-06 A1: un plan A→B con A blocked y B backlog(dep A) NO se queda
    in_progress para siempre — el reconciler lo escala a blocked."""
    from workers.maintenance import _reconcile_pipeline_state_async

    ids = await _seed_stuck_dag_plan(migrations_pg_dsn)

    await _reconcile_pipeline_state_async(
        workers_settings,  # type: ignore[arg-type]
        redis=_FakeRedis(),
        now=datetime.now(UTC),
        stuck_task_min_age=timedelta(minutes=5),
        review_min_age=timedelta(minutes=5),
    )

    engine = create_async_engine(workers_settings.database_url)  # type: ignore[attr-defined]
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            plan = await session.get(Plan, ids["plan"])
        assert plan is not None and plan.status == "blocked"
    finally:
        await engine.dispose()


async def _seed_stuck_review(dsn: str) -> dict[str, UUID]:
    """Una tarea in_review con reviewer IA, sin ejecución, updated_at hace 2 h
    (más allá del cap de 1 h del reconciler). El cap D3 nunca avanzaría (no hay
    ejecución de review) → el reconciler debe escalar a blocked (M5)."""
    ids = {"tenant": uuid4(), "project": uuid4(), "reviewer": uuid4(), "task": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE task_audit_events, executions, tasks, plans, agents, projects,"
            " organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T m5', 't-m5-review')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, project_id, name, agent_type, scope, role, system_prompt)"
            " VALUES ($1, $2, $3, 'Rev', 'ai', 'project_local', 'reviewer', 'You review.')",
            ids["reviewer"],
            ids["tenant"],
            ids["project"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority,"
            " reviewer_agent_id, updated_at) VALUES ($1, $2, $3, 'rev', 'in_review',"
            " 'medium', $4, now() - interval '2 hours')",
            ids["task"],
            ids["tenant"],
            ids["project"],
            ids["reviewer"],
        )
        return ids
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_reconciler_escalates_review_stuck_past_cap(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """M5: una review huérfana atascada más allá del cap del reconciler (1 h) NO se
    re-anuncia para siempre — se escala a blocked con evento de auditoría, y el
    task.status_changed publicado lleva new_status=blocked (no in_review)."""
    from api_server.db.models import TaskAuditEvent
    from sqlalchemy import select
    from workers.maintenance import _reconcile_orphan_reviews

    ids = await _seed_stuck_review(migrations_pg_dsn)
    redis = _FakeRedis()
    engine = create_async_engine(workers_settings.database_url)  # type: ignore[attr-defined]
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        escalated = await _reconcile_orphan_reviews(
            sessionmaker,
            redis,
            now=datetime.now(UTC),
            min_age=timedelta(minutes=5),
            max_stuck=timedelta(hours=1),
        )
        assert escalated == 1

        async with sessionmaker() as session:
            task = await session.get(Task, ids["task"])
            events = list(
                (
                    await session.execute(
                        select(TaskAuditEvent).where(TaskAuditEvent.task_id == ids["task"])
                    )
                )
                .scalars()
                .all()
            )
        assert task is not None and task.status == TaskStatus.BLOCKED.value
        # Evento de auditoría del escalado del reconciler.
        assert any(
            e.actor == "reconciler" and e.payload.get("reason") == "review_stuck_reconcile_cap"
            for e in events
        )
        # El evento publicado es in_review→blocked, no un re-anuncio.
        new_statuses = [
            json.loads(e["payload"]).get("new_status")
            for e in redis.events
            if e.get("type") == "task.status_changed"
        ]
        assert new_statuses == [TaskStatus.BLOCKED.value]
    finally:
        await engine.dispose()
