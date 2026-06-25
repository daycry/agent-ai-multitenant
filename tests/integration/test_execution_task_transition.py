"""Integration test — prod-06 task_prod06_dag_01.

A terminal agent run must ALWAYS move its task off ``in_progress`` (today
nothing does, so a finished task stays ``in_progress`` forever, inflating the
agent's load counter). ``transition_task_after_run`` is the worker-side helper
that ``conduct_execution`` calls after ``finalize_execution``:

  - ``done`` + the task has a reviewer  -> ``in_review``
  - ``done``, no reviewer               -> ``done`` (+ ``completed_at``)
  - ``failed`` / ``aborted`` / other    -> ``blocked`` (motive = the execution row)
  - ``awaiting_human_approval``         -> no-op (the approval branch owns it)
  - task already terminal (guard)       -> no-op

The helper runs under the worker's BYPASSRLS session, so the test drives it
with a ``migrations_user`` AsyncSession against the migrated test DB.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import Task, TaskStatus
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.execution import transition_task_after_run

pytestmark = pytest.mark.integration


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "reviewer": uuid4(),
        "t_review": uuid4(),
        "t_noreview": uuid4(),
        "t_fail": uuid4(),
        "t_terminal": uuid4(),
        "t_approval": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE tasks, agents, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T dag01', 't-dag01')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, scope, agent_type, system_prompt)"
            " VALUES ($1, $2, 'rev', 'reviewer', 'global_tenant_template', 'ai', 'r')",
            ids["reviewer"],
            ids["tenant"],
        )
        rows: list[tuple[UUID, UUID | None]] = [
            (ids["t_review"], ids["reviewer"]),
            (ids["t_noreview"], None),
            (ids["t_fail"], None),
            (ids["t_terminal"], None),
            (ids["t_approval"], None),
        ]
        for tid, reviewer in rows:
            await conn.execute(
                "INSERT INTO tasks"
                " (id, tenant_id, project_id, title, status, priority, reviewer_agent_id)"
                " VALUES ($1, $2, $3, 'task', 'in_progress', 'medium', $4)",
                tid,
                ids["tenant"],
                ids["project"],
                reviewer,
            )
        # t_terminal is already terminal — the guard must leave it alone.
        await conn.execute("UPDATE tasks SET status = 'done' WHERE id = $1", ids["t_terminal"])
        return ids
    finally:
        await conn.close()


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.mark.asyncio
async def test_transition_task_after_run(
    _migrated: None, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    engine = create_async_engine(admin_database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session, session.begin():
            ev_review = await transition_task_after_run(session, ids["t_review"], "done")
            ev_done = await transition_task_after_run(session, ids["t_noreview"], "done")
            ev_fail = await transition_task_after_run(session, ids["t_fail"], "failed")
            ev_terminal = await transition_task_after_run(session, ids["t_terminal"], "done")
            ev_approval = await transition_task_after_run(
                session, ids["t_approval"], "awaiting_human_approval"
            )

        async with sessionmaker() as session:
            t_review = await session.get(Task, ids["t_review"])
            t_done = await session.get(Task, ids["t_noreview"])
            t_fail = await session.get(Task, ids["t_fail"])
            t_terminal = await session.get(Task, ids["t_terminal"])
            t_approval = await session.get(Task, ids["t_approval"])

        # done + reviewer -> in_review
        assert t_review is not None and t_review.status == TaskStatus.IN_REVIEW.value
        assert ev_review is not None and ev_review[1] == TaskStatus.IN_PROGRESS.value
        assert ev_review[2] == TaskStatus.IN_REVIEW.value
        # done, no reviewer -> done + completed_at stamped
        assert t_done is not None and t_done.status == TaskStatus.DONE.value
        assert t_done.completed_at is not None
        assert ev_done is not None and ev_done[2] == TaskStatus.DONE.value
        # failed -> blocked
        assert t_fail is not None and t_fail.status == TaskStatus.BLOCKED.value
        assert ev_fail is not None and ev_fail[2] == TaskStatus.BLOCKED.value
        # already-terminal task is left alone; helper returns None
        assert t_terminal is not None and t_terminal.status == TaskStatus.DONE.value
        assert ev_terminal is None
        # awaiting_human_approval is owned by the approval branch -> no-op
        assert t_approval is not None and t_approval.status == TaskStatus.IN_PROGRESS.value
        assert ev_approval is None
    finally:
        await engine.dispose()
