"""Integration tests for fn_compute_task_ready (task_02_04).

The migration 0009 trigger auto-promotes a task from `backlog` to
`ready` once every task it depends on has reached `done`. These tests
drive real rows through a real Postgres (the trigger is PL/pgSQL — no
way to unit-test it) and assert the DAG transitions.

All writes go through the BYPASSRLS migrations role; the trigger
itself is tenant-agnostic (it joins task_dependencies, all same-tenant).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


async def _seed_project(conn: asyncpg.Connection) -> dict[str, UUID]:
    tenant = uuid4()
    project = uuid4()
    await conn.execute(
        "TRUNCATE task_dependencies, tasks, projects, organizations RESTART IDENTITY CASCADE"
    )
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
        tenant,
        "Tenant DAG",
        "tenant-dag",
    )
    await conn.execute(
        "INSERT INTO projects (id, tenant_id, name, status, is_template)"
        " VALUES ($1, $2, $3, 'active', false)",
        project,
        tenant,
        "DAG project",
    )
    return {"tenant": tenant, "project": project}


async def _add_task(
    conn: asyncpg.Connection,
    ids: dict[str, UUID],
    title: str,
    status: str = "backlog",
) -> UUID:
    task_id = uuid4()
    await conn.execute(
        "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority)"
        " VALUES ($1, $2, $3, $4, $5, 'medium')",
        task_id,
        ids["tenant"],
        ids["project"],
        title,
        status,
    )
    return task_id


async def _add_dependency(conn: asyncpg.Connection, task_id: UUID, depends_on: UUID) -> None:
    await conn.execute(
        "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES ($1, $2)",
        task_id,
        depends_on,
    )


async def _status(conn: asyncpg.Connection, task_id: UUID) -> str:
    row = await conn.fetchrow("SELECT status FROM tasks WHERE id = $1", task_id)
    assert row is not None
    return str(row["status"])


async def _set_status(conn: asyncpg.Connection, task_id: UUID, status: str) -> None:
    await conn.execute("UPDATE tasks SET status = $2 WHERE id = $1", task_id, status)


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    """Bring the throwaway DB to head so migration 0009 is applied."""
    command.upgrade(alembic_config, "head")


@pytest.mark.asyncio
async def test_task_becomes_ready_when_last_dependency_completes(
    _migrated: None, migrations_pg_dsn: str
) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        ids = await _seed_project(conn)
        a = await _add_task(conn, ids, "A")
        b = await _add_task(conn, ids, "B")
        c = await _add_task(conn, ids, "C")  # depends on A and B
        await _add_dependency(conn, c, a)
        await _add_dependency(conn, c, b)

        # First dependency done — C still blocked by B.
        await _set_status(conn, a, "done")
        assert await _status(conn, c) == "backlog"

        # Last dependency done — the trigger promotes C.
        await _set_status(conn, b, "done")
        assert await _status(conn, c) == "ready"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_chain_promotes_one_hop_at_a_time(_migrated: None, migrations_pg_dsn: str) -> None:
    """A -> B -> C: completing A readies B; completing B readies C."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        ids = await _seed_project(conn)
        a = await _add_task(conn, ids, "A")
        b = await _add_task(conn, ids, "B")
        c = await _add_task(conn, ids, "C")
        await _add_dependency(conn, b, a)
        await _add_dependency(conn, c, b)

        await _set_status(conn, a, "done")
        assert await _status(conn, b) == "ready"
        assert await _status(conn, c) == "backlog"  # B not done yet

        await _set_status(conn, b, "done")
        assert await _status(conn, c) == "ready"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_non_backlog_dependent_is_left_alone(_migrated: None, migrations_pg_dsn: str) -> None:
    """The trigger only promotes backlog -> ready. A dependent that a
    human already moved to in_progress is not yanked back."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        ids = await _seed_project(conn)
        a = await _add_task(conn, ids, "A")
        b = await _add_task(conn, ids, "B", status="in_progress")
        await _add_dependency(conn, b, a)

        await _set_status(conn, a, "done")
        assert await _status(conn, b) == "in_progress"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_independent_task_is_untouched(_migrated: None, migrations_pg_dsn: str) -> None:
    """Completing a task does not ready an unrelated task."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        ids = await _seed_project(conn)
        a = await _add_task(conn, ids, "A")
        lonely = await _add_task(conn, ids, "Lonely")  # no dependency edges

        await _set_status(conn, a, "done")
        assert await _status(conn, lonely) == "backlog"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_partial_completion_keeps_dependent_blocked(
    _migrated: None, migrations_pg_dsn: str
) -> None:
    """A diamond: D depends on A, B, C. D stays backlog until all three
    are done, then flips on the last one."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        ids = await _seed_project(conn)
        a = await _add_task(conn, ids, "A")
        b = await _add_task(conn, ids, "B")
        c = await _add_task(conn, ids, "C")
        d = await _add_task(conn, ids, "D")
        for dep in (a, b, c):
            await _add_dependency(conn, d, dep)

        await _set_status(conn, a, "done")
        await _set_status(conn, b, "done")
        assert await _status(conn, d) == "backlog"

        await _set_status(conn, c, "done")
        assert await _status(conn, d) == "ready"
    finally:
        await conn.close()


def test_migration_0009_is_reversible(alembic_config: object) -> None:
    """downgrade to 0008 then back up to head must both succeed.

    Sync on purpose — alembic's async env runs its own `asyncio.run`,
    so driving `command.*` from inside an async test's loop would
    raise. A broken downgrade/upgrade SQL surfaces as an alembic error.
    """
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0008_approval_policy_templates")
    command.upgrade(alembic_config, "head")
