"""End-to-end budget auto-pause WRITER→READER loop — prod-06 task_prod06_budget_01.

The dispatch START path already READ ``paused_by_budget`` (``budget_pause_block``),
but NOTHING wrote it in production (db-1): ``refresh_budget_pause_flags`` +
``maybe_alert_budgets`` had only tests as callers. This drives the productive
WRITERS this plan wires:

  - ``sweep_tenant_budgets`` — the shared seam (refresh + alert);
  - ``workers.refresh_budgets`` (its async core ``_refresh_budgets_async``) — the
    periodic beat that iterates tenants;

and proves the loop closes: after the beat runs on an over-budget tenant, the
auto-pause flag is set, an alert fires, and a NEW dispatch is REFUSED.

Note: placed under tests/integration (not tests/e2e) because it needs the
Postgres+Redis fixtures of the integration conftest; tests/e2e is reserved for
the gated full-install Docker suite.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import api_server.db.domain  # noqa: F401 - resolve ORM FKs the spend query joins
import asyncpg
import pytest
from alembic import command
from api_server.budgets import sweep_tenant_budgets
from orchestrator.config import Settings as OrchestratorSettings
from orchestrator.dispatch import TaskDispatcher
from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.celery_app import build_celery_app
from workers.config import Settings as WorkerSettings

pytestmark = pytest.mark.integration

TEST_REDIS_URL = "redis://localhost:6379/15"
_SCRIPTED_FINISH = {"kind": "scripted", "decisions": [{"kind": "finish", "output": "done"}]}


class _FakeAlertDispatcher:
    """Captures dispatched alert events without a real broker."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def dispatch(self, event: dict[str, object]) -> bool:
        self.events.append(event)
        return True


@pytest.fixture()
def schema_at_head(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str) -> Any:
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import reset_settings_cache

    reset_settings_cache()
    from workers.config import get_settings

    yield get_settings()
    reset_settings_cache()


async def _seed_over_budget_tenant(dsn: str) -> dict[str, UUID]:
    """A $100/month tenant that has already spent $120 (120% → over 100%), plus a
    fresh ``ready`` task waiting to start."""
    ids = {"tenant": uuid4(), "project": uuid4(), "agent": uuid4()}
    spent_task = uuid4()
    ids["ready_task"] = uuid4()
    now = datetime.now(UTC)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE budget_alert_states, executions, task_dependencies, tasks,"
            " plans, agents, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug, tenant_budget_amount,"
            " tenant_budget_currency, tenant_budget_period)"
            " VALUES ($1, 'Over', 'over-budget', 100, 'USD', 'monthly')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, worker_config)"
            " VALUES ($1, $2, 'P', 'active', '{\"assignment_policy\": \"load_balanced\"}'::jsonb)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, agent_type,"
            " scope, project_id, model_config)"
            " VALUES ($1, $2, 'W', 'backend-dev', 'x', 'ai', 'project_local', $3, $4::jsonb)",
            ids["agent"],
            ids["tenant"],
            ids["project"],
            json.dumps(_SCRIPTED_FINISH),
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority)"
            " VALUES ($1, $2, $3, 'spent', 'done', 'medium')",
            spent_task,
            ids["tenant"],
            ids["project"],
        )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, steps_log,"
            " total_tokens, total_cost_usd, started_at, completed_at, created_at)"
            " VALUES ($1, $2, $3, 'done', '[]'::jsonb, 0, 120, $4, $4, $4)",
            uuid4(),
            ids["tenant"],
            spent_task,
            now,
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority)"
            " VALUES ($1, $2, $3, 'next', 'ready', 'medium')",
            ids["ready_task"],
            ids["tenant"],
            ids["project"],
        )
        return ids
    finally:
        await conn.close()


async def _org_paused(dsn: str, tenant: UUID) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        return bool(
            await conn.fetchval(
                "SELECT tenant_paused_by_budget FROM organizations WHERE id = $1", tenant
            )
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_beat_pauses_over_budget_then_dispatch_is_refused(
    schema_at_head: None,
    migrations_pg_dsn: str,
    admin_database_url: str,
    workers_settings: Any,
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    ids = await _seed_over_budget_tenant(migrations_pg_dsn)

    # 1) The beat (productive writer) runs on the BYPASSRLS worker engine.
    from workers.maintenance import _refresh_budgets_async

    fake = _FakeAlertDispatcher()
    result = await _refresh_budgets_async(workers_settings, dispatcher=fake)

    assert result["tenants"] >= 1
    assert result["newly_paused"] >= 1
    # The over-budget tenant is now flagged paused, and an alert fired.
    assert await _org_paused(migrations_pg_dsn, ids["tenant"]) is True
    assert len(fake.events) >= 1

    # 2) The dispatch READER now refuses to START a new run for the paused tenant.
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        dispatcher = TaskDispatcher(
            sessionmaker=sm,
            celery_app=build_celery_app(WorkerSettings(broker_url=TEST_REDIS_URL)),
            settings=OrchestratorSettings(redis_url=TEST_REDIS_URL),
        )
        await dispatcher.handle(
            TaskEvent(
                stream_id="1-0",
                type=EVENT_TASK_STATUS_CHANGED,
                tenant_id=str(ids["tenant"]),
                project_id=str(ids["project"]),
                task_id=str(ids["ready_task"]),
                occurred_at="2026-06-25T00:00:00+00:00",
                payload={"old_status": "backlog", "new_status": "ready"},
            )
        )
        async with sm() as s:
            from api_server.db.domain import Task

            task = (await s.execute(select(Task).where(Task.id == ids["ready_task"]))).scalar_one()
        # Refused by the budget pause: still ready, no agent assigned.
        assert task.status == "ready"
        assert task.assigned_agent_id is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sweep_seam_clears_pause_when_budget_raised(
    schema_at_head: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """The seam re-derives from CURRENT consumption: raise the cap above spend and
    the next sweep auto-clears the pause (no extra state)."""
    ids = await _seed_over_budget_tenant(migrations_pg_dsn)
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        # First sweep pauses (120% of $100).
        async with sm() as s, s.begin():
            r1 = await sweep_tenant_budgets(s, tenant_id=ids["tenant"])
        assert len(r1.refresh.newly_paused) >= 1
        assert await _org_paused(migrations_pg_dsn, ids["tenant"]) is True

        # Raise the cap to $1000 (spend $120 → 12% → under 100%).
        async with sm() as s, s.begin():
            await s.execute(
                text("UPDATE organizations SET tenant_budget_amount = 1000 WHERE id = :t"),
                {"t": ids["tenant"]},
            )
        async with sm() as s, s.begin():
            r2 = await sweep_tenant_budgets(s, tenant_id=ids["tenant"])
        assert len(r2.refresh.newly_cleared) >= 1
        assert await _org_paused(migrations_pg_dsn, ids["tenant"]) is False
    finally:
        await engine.dispose()
