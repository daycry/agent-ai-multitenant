"""Integration test — prod-06 task_prod06_dag_02 beat (``workers.promote_ready_plans``).

The beat is the safety net: across ``in_progress`` plans it promotes eligible
``backlog`` tasks to ``ready`` and announces the undispatched ones; a plan that is
NOT ``in_progress`` (e.g. a ``draft``) is left untouched.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import Task, TaskStatus
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str, test_redis_url: str):
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    monkeypatch.setenv("WORKERS_EVENTS_REDIS_URL", test_redis_url)
    from workers.config import get_settings, reset_settings_cache

    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "plan_run": uuid4(),
        "plan_draft": uuid4(),
        "root_run": uuid4(),
        "root_draft": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, task_dependencies, tasks, plans, projects, organizations"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T beat', 't-dag02-beat')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status) VALUES"
            " ($1, $2, $3, 'Running', 'in_progress'), ($4, $2, $3, 'Draft', 'draft')",
            ids["plan_run"],
            ids["tenant"],
            ids["project"],
            ids["plan_draft"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority) VALUES"
            " ($1, $2, $3, $4, 'root-run', 'backlog', 'medium'),"
            " ($5, $2, $3, $6, 'root-draft', 'backlog', 'medium')",
            ids["root_run"],
            ids["tenant"],
            ids["project"],
            ids["plan_run"],
            ids["root_draft"],
            ids["plan_draft"],
        )
        return ids
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_promote_ready_plans_beat(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    from workers.maintenance import _promote_ready_plans_async

    ids = await _seed(migrations_pg_dsn)

    result = await _promote_ready_plans_async(workers_settings)  # type: ignore[arg-type]

    engine = create_async_engine(workers_settings.database_url)  # type: ignore[attr-defined]
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            root_run = await session.get(Task, ids["root_run"])
            root_draft = await session.get(Task, ids["root_draft"])
        # The in_progress plan's root is promoted; the draft plan's root is not.
        assert root_run is not None and root_run.status == TaskStatus.READY.value
        assert root_draft is not None and root_draft.status == TaskStatus.BACKLOG.value
        assert result["plans_touched"] == 1
        assert result["promoted"] == 1
    finally:
        await engine.dispose()
