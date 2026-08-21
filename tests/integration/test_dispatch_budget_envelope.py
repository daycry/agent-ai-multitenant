"""Integration test — prod-06 task_prod06_budget_02 (workers-10).

The dispatcher threads a RESOLVED per-run budget envelope into the worker
payload (``ExecutionRequest.budgets``) instead of the old unconditional
``None``: the project's ``execution_budgets`` override merged over the platform
default, clamped to the runtime ceiling. This drives ``_route_ai`` end-to-end
against the real DB and inspects the request it builds.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.budgets import EXECUTION_BUDGET_CEILING
from api_server.db.domain import Agent, Project, Task
from api_server.db.models import Organization
from api_server.db.platform_settings import EXECUTION_DEFAULT_BUDGETS_KEY, set_platform_setting
from orchestrator.config import Settings as OrchestratorSettings
from orchestrator.dispatch import TaskDispatcher
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.celery_app import build_celery_app
from workers.config import Settings as WorkerSettings

from ._redis_url import TEST_REDIS_URL  # con credencial; ver _redis_url.py

pytestmark = pytest.mark.integration

_SCRIPTED_FINISH = {"kind": "scripted", "decisions": [{"kind": "finish", "output": "ok"}]}


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(
    sm: async_sessionmaker,
    *,
    project_budgets: dict[str, Any] | None,
) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "agent": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, agents, projects,"
                " organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="T", slug="t-bud02"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="P",
                status="active",
                is_template=False,
                worker_config={"assignment_policy": "load_balanced"},
                execution_budgets=project_budgets,
            )
        )
        await s.flush()
        s.add(
            Agent(
                id=ids["agent"],
                tenant_id=ids["tenant"],
                name="W",
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
    celery_app = build_celery_app(
        WorkerSettings(broker_url=TEST_REDIS_URL, result_backend=TEST_REDIS_URL)
    )
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=celery_app,
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL),
    )


async def _route(sm: async_sessionmaker, ids: dict[str, UUID]) -> dict[str, Any]:
    dispatcher = _dispatcher(sm)
    async with sm() as s, s.begin():
        task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        result = await dispatcher._route_ai(s, task)
    assert result is not None
    return result.request


@pytest.mark.asyncio
async def test_no_override_threads_none(_migrated: None, admin_database_url: str) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, project_budgets=None)
        request = await _route(sm, ids)
        # No project override + no platform default → runtime keeps its own defaults.
        assert request["budgets"] is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_project_override_is_threaded_and_clamped(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(
            sm,
            project_budgets={"max_tokens": 30_000, "max_cost_usd": 999.0},
        )
        request = await _route(sm, ids)
        # max_tokens passes through (under ceiling); max_cost_usd clamps to ceiling.
        assert request["budgets"] == {
            "max_tokens": 30_000,
            "max_cost_usd": EXECUTION_BUDGET_CEILING["max_cost_usd"],
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_project_override_wins_over_platform_default(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, project_budgets={"max_cost_usd": 1.0})
        # Platform default sets a different envelope; the project override wins
        # key-by-key, the platform value fills the rest.
        async with sm() as s, s.begin():
            org_admin = await _system_admin(s)
            await set_platform_setting(
                s,
                EXECUTION_DEFAULT_BUDGETS_KEY,
                {"max_cost_usd": 4.0, "max_iterations": 10},
                actor=org_admin,
            )
        request = await _route(sm, ids)
        assert request["budgets"] == {"max_iterations": 10, "max_cost_usd": 1.0}
    finally:
        await engine.dispose()


async def _system_admin(session: Any) -> Any:
    """Create a throwaway System Admin to author the platform setting."""
    from api_server.db.models import User

    admin = User(
        id=uuid4(),
        email=f"admin-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        is_system_admin=True,
    )
    session.add(admin)
    await session.flush()
    return admin
