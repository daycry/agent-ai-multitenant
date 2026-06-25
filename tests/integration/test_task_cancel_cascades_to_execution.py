"""Integration test — prod-06 task_prod06_cancel_01.

Cancelling a task in flight must cancel its RUNNING execution(s): seal
``cancel_requested_at`` (the worker polls it to kill the container + finalise as
``cancelled``) and surface ``celery_task_id`` so the caller can revoke the job. A
terminal execution is left untouched; a second call is idempotent.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import Execution
from api_server.db.execution_repo import cancel_running_executions_for_task
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "task": uuid4(),
        "exec_running": uuid4(),
        "exec_done": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T cancel', 't-cancel01')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority)"
            " VALUES ($1, $2, $3, 'task', 'in_progress', 'medium')",
            ids["task"],
            ids["tenant"],
            ids["project"],
        )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, celery_task_id)"
            " VALUES ($1, $2, $3, 'running', 'job-123')",
            ids["exec_running"],
            ids["tenant"],
            ids["task"],
        )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status) VALUES ($1, $2, $3, 'done')",
            ids["exec_done"],
            ids["tenant"],
            ids["task"],
        )
        return ids
    finally:
        await conn.close()


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.mark.asyncio
async def test_cancel_running_executions_for_task(
    _migrated: None, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    engine = create_async_engine(admin_database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session, session.begin():
            cancelled = await cancel_running_executions_for_task(session, ids["task"])
            # Only the running execution is cancelled; it carries its job id.
            assert {e.id for e in cancelled} == {ids["exec_running"]}
            assert cancelled[0].celery_task_id == "job-123"

        async with sessionmaker() as session:
            run = await session.get(Execution, ids["exec_running"])
            done = await session.get(Execution, ids["exec_done"])
        assert run is not None and run.cancel_requested_at is not None
        assert done is not None and done.cancel_requested_at is None

        # Idempotent: a second call does not bump the timestamp or re-list it.
        first_sealed = run.cancel_requested_at
        async with sessionmaker() as session, session.begin():
            again = await cancel_running_executions_for_task(session, ids["task"])
        assert {e.id for e in again} == {ids["exec_running"]}
        async with sessionmaker() as session:
            run2 = await session.get(Execution, ids["exec_running"])
        assert run2 is not None and run2.cancel_requested_at == first_sealed
    finally:
        await engine.dispose()
