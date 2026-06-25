"""Integration test — prod-06 task_prod06_cancel_02.

Cancelling a plan (or soft-deleting a project) must cascade: every NON-terminal
task is moved to ``cancelled`` and its running executions are flagged for
cancellation (the worker kills the containers); terminal tasks are left alone.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import Execution, Task, TaskStatus
from api_server.db.execution_repo import cancel_tasks_and_executions
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "plan": uuid4(),
        "task_backlog": uuid4(),
        "task_running": uuid4(),
        "task_done": uuid4(),
        "exec_running": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, plans, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T cascade', 't-cancel02')",
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
            " VALUES ($1, $2, $3, 'Plan', 'in_progress')",
            ids["plan"],
            ids["tenant"],
            ids["project"],
        )
        for tid, st in [
            (ids["task_backlog"], "backlog"),
            (ids["task_running"], "in_progress"),
            (ids["task_done"], "done"),
        ]:
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority)"
                " VALUES ($1, $2, $3, $4, 'task', $5, 'medium')",
                tid,
                ids["tenant"],
                ids["project"],
                ids["plan"],
                st,
            )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, celery_task_id)"
            " VALUES ($1, $2, $3, 'running', 'job-xyz')",
            ids["exec_running"],
            ids["tenant"],
            ids["task_running"],
        )
        return ids
    finally:
        await conn.close()


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.mark.asyncio
async def test_cancel_plan_cascade(
    _migrated: None, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    engine = create_async_engine(admin_database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session, session.begin():
            cancelled = await cancel_tasks_and_executions(session, plan_id=ids["plan"])
            # Only the running execution of a non-terminal task is returned.
            assert {e.id for e in cancelled} == {ids["exec_running"]}
            assert cancelled[0].celery_task_id == "job-xyz"

        async with sessionmaker() as session:
            backlog = await session.get(Task, ids["task_backlog"])
            running = await session.get(Task, ids["task_running"])
            done = await session.get(Task, ids["task_done"])
            exec_running = await session.get(Execution, ids["exec_running"])

        # Non-terminal tasks cancelled; the done task untouched.
        assert backlog is not None and backlog.status == TaskStatus.CANCELLED.value
        assert running is not None and running.status == TaskStatus.CANCELLED.value
        assert done is not None and done.status == TaskStatus.DONE.value
        # The running execution was flagged for cancellation.
        assert exec_running is not None and exec_running.cancel_requested_at is not None
    finally:
        await engine.dispose()


def test_cancel_tasks_and_executions_requires_exactly_one_scope() -> None:
    import asyncio

    from api_server.db.execution_repo import cancel_tasks_and_executions as _fn

    # Neither id → ValueError (no accidental "cancel everything").
    with pytest.raises(ValueError, match="exactly one"):
        asyncio.run(_fn(None))  # type: ignore[arg-type]
