"""Integration test — prod-06 task_prod06_dag_02 (``promote_ready_tasks``).

Drives real rows through Postgres (the eligibility query mirrors the DB trigger
and the advisory lock is PL/pgSQL-side):

  - a ROOT task (no deps) is promoted ``backlog`` -> ``ready`` and announced —
    the gap that left a freshly-started plan stuck with nothing dispatchable;
  - a DEPENDENT waits in ``backlog`` until its dependency is ``done``, then is
    promoted/announced (even when the DB trigger flipped it first — the
    "undispatched ready" return still picks it up);
  - an already-dispatched task (an ``executions`` row exists) is NEVER
    re-announced — the operation is idempotent.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.dag_promotion import promote_ready_tasks
from api_server.db.domain import Task, TaskStatus
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "plan": uuid4(),
        "root": uuid4(),
        "dep": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, task_dependencies, tasks, plans, projects, organizations"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T dag02', 't-dag02')",
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
        for tid, title in [(ids["root"], "root"), (ids["dep"], "dep")]:
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority)"
                " VALUES ($1, $2, $3, $4, $5, 'backlog', 'medium')",
                tid,
                ids["tenant"],
                ids["project"],
                ids["plan"],
                title,
            )
        # dep depends on root.
        await conn.execute(
            "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES ($1, $2)",
            ids["dep"],
            ids["root"],
        )
        return ids
    finally:
        await conn.close()


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.mark.asyncio
async def test_promote_ready_tasks(
    _migrated: None, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    engine = create_async_engine(admin_database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # --- Case 1: root promoted backlog->ready + announced; dep still waits ---
        async with sessionmaker() as session, session.begin():
            announced = await promote_ready_tasks(session, ids["plan"])
        async with sessionmaker() as session:
            root = await session.get(Task, ids["root"])
            dep = await session.get(Task, ids["dep"])
        assert root is not None and root.status == TaskStatus.READY.value
        assert dep is not None and dep.status == TaskStatus.BACKLOG.value
        assert {t.id for t in announced} == {ids["root"]}

        # --- Case 2: root done -> dep becomes eligible and is announced ---
        # (The DB trigger may flip dep to ready on this UPDATE; the promotor's
        #  "undispatched ready" return announces it regardless of who flipped it.)
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute("UPDATE tasks SET status = 'done' WHERE id = $1", ids["root"])
        finally:
            await conn.close()
        async with sessionmaker() as session, session.begin():
            announced2 = await promote_ready_tasks(session, ids["plan"])
        async with sessionmaker() as session:
            dep = await session.get(Task, ids["dep"])
        assert dep is not None and dep.status == TaskStatus.READY.value
        assert ids["dep"] in {t.id for t in announced2}

        # --- Case 3: dep dispatched (executions row) -> never re-announced ---
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute(
                "INSERT INTO executions (id, tenant_id, task_id, status)"
                " VALUES ($1, $2, $3, 'running')",
                uuid4(),
                ids["tenant"],
                ids["dep"],
            )
        finally:
            await conn.close()
        async with sessionmaker() as session, session.begin():
            announced3 = await promote_ready_tasks(session, ids["plan"])
        assert ids["dep"] not in {t.id for t in announced3}
    finally:
        await engine.dispose()
