"""Worker rejects cross-tenant / unknown-task execution requests
(Plan 06.14 task_06_14_02).

Regression for multi-tenancy-rls-1 / multi-tenancy-rls-5: the worker
runs as a BYPASSRLS role, so a Celery payload that pairs one tenant with
another tenant's `task_id` would otherwise create an execution that
attributes the foreign task's data to the claimed tenant. `conduct_execution`
now validates task↔tenant ownership at the boundary, before creating the
`executions` row or launching any container — so these tests need no
Docker (the guard raises first).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import Project, Task
from api_server.db.execution_repo import list_executions_for_task
from api_server.db.models import Organization
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.config import Settings
from workers.execution import (
    CrossTenantExecutionError,
    ExecutionRequest,
    conduct_execution,
)

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_task(sm: async_sessionmaker) -> dict[str, UUID]:
    """Insert a tenant / project / task (tenant A); return their ids."""
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, projects, organizations"
                " RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Worker tenant", slug="worker-tenant-boundary"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Worker project",
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
                title="Owned by tenant A",
                status="in_progress",
                priority="medium",
            )
        )
    return ids


def _request(*, tenant_id: UUID, task_id: UUID) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(tenant_id),
        task_id=str(task_id),
        agent_id=None,
        task={"id": str(task_id), "title": "x", "description": "y"},
        model={"kind": "scripted", "decisions": [{"kind": "finish", "output": "x"}]},
    )


@pytest.mark.asyncio
async def test_conduct_execution_rejects_cross_tenant_task(
    _migrated: None, admin_database_url: str, test_redis_url: str
) -> None:
    """A request claiming tenant B but pointing at tenant A's task is
    rejected, and no execution row is created."""
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task(sm)
        foreign_tenant = uuid4()  # tenant B — does not own task A

        with pytest.raises(CrossTenantExecutionError):
            await conduct_execution(
                _request(tenant_id=foreign_tenant, task_id=ids["task"]),
                settings=Settings(),
                sessionmaker=sm,
                redis=redis,
            )

        async with sm() as s:
            executions = await list_executions_for_task(s, ids["task"])
        assert executions == []  # nothing was attributed to the foreign tenant
    finally:
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_conduct_execution_rejects_unknown_task(
    _migrated: None, admin_database_url: str, test_redis_url: str
) -> None:
    """A request for a task_id that does not exist is rejected (the guard
    treats a missing row the same as a tenant mismatch)."""
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task(sm)

        with pytest.raises(CrossTenantExecutionError):
            await conduct_execution(
                _request(tenant_id=ids["tenant"], task_id=uuid4()),
                settings=Settings(),
                sessionmaker=sm,
                redis=redis,
            )
    finally:
        await redis.aclose()
        await engine.dispose()
