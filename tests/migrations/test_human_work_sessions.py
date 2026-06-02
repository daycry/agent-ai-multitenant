"""Migration coverage for ``human_work_sessions`` (Plan 16 task_16_03).

Migration 0068 creates the ``human_work_sessions`` table — the Execution-
equivalent audit trail for ``agent_type='human'`` tasks (Plan 16 Decisiones
Clave: ``HumanWorkSession`` replaces ``Execution`` for human tasks). A session
records a human's work on a task: who (``user_id``), when (``start_at`` /
``end_at``), how long (``hours_logged``), notes (``comments``), and the
deliverables attached (``output_files_attached``). These tests assert the end
state the plan requires:

  - the table and every column the task names exist;
  - the FKs to ``tasks`` (CASCADE) and ``users`` (SET NULL) exist;
  - RLS is enabled (+ FORCE) on the table — same shape as ``executions``;
  - ``hours_logged`` accepts a Numeric value and rejects a negative one;
  - ``output_files_attached`` accepts (and round-trips) a JSONB list;
  - a session is tenant-isolated under RLS (@pytest.mark.cross_tenant): a
    session seeded for tenant A is invisible to a session pinned to tenant B,
    and an app_user pinned to tenant B cannot INSERT a tenant-A session;
  - the migration is reversible (head -> 0067 -> head) and the table /
    policy come and go with it.

The cross-tenant test connects as ``app_user`` (NOBYPASSRLS) and pins the
``app.tenant_id`` GUC the way production does, so it exercises the real RLS
policy — not the BYPASSRLS migrations/admin role the other tests seed with.
"""

from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest
from alembic import command
from uuid6 import uuid7

pytestmark = pytest.mark.integration

# app_user is NOBYPASSRLS — same defaults as tests/integration/conftest.py.
_PG_HOST = os.environ.get("TEST_PG_HOST", "localhost")
_PG_PORT = int(os.environ.get("TEST_PG_PORT", "15432"))
_PG_APP_USER = os.environ.get("TEST_PG_APP_USER", "app_user")
_PG_APP_PASSWORD = os.environ.get("TEST_PG_APP_PASSWORD", "changeme-app-dev-only")
_PG_TEST_DB = os.environ.get("TEST_PG_DB_NAME", "agentic_platform_test")


def _app_dsn() -> str:
    return f"postgresql://{_PG_APP_USER}:{_PG_APP_PASSWORD}@{_PG_HOST}:{_PG_PORT}/{_PG_TEST_DB}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _fetchval(dsn: str, sql: str, *args: object) -> object:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


async def _execute(dsn: str, sql: str, *args: object) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


async def _fetch(dsn: str, sql: str, *args: object) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


_COLS_SQL = """
    SELECT column_name
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = $1
"""


def _columns(dsn: str, table: str) -> set[str]:
    rows = asyncio.run(_fetch(dsn, _COLS_SQL, table))
    return {str(r["column_name"]) for r in rows}


def _table_exists(dsn: str, table: str) -> bool:
    val = asyncio.run(_fetchval(dsn, "SELECT to_regclass($1)", f"public.{table}"))
    return val is not None


def _fk_targets(dsn: str, table: str) -> set[str]:
    """Return the set of '<col>->><reftable>' FK edges of ``table``."""
    rows = asyncio.run(
        _fetch(
            dsn,
            """
            SELECT kcu.column_name AS col, ccu.table_name AS ref
              FROM information_schema.table_constraints tc
              JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
              JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name
             WHERE tc.constraint_type = 'FOREIGN KEY'
               AND tc.table_name = $1
            """,
            table,
        )
    )
    return {f"{r['col']}->>{r['ref']}" for r in rows}


def _rls_enabled(dsn: str, table: str) -> bool:
    val = asyncio.run(
        _fetchval(
            dsn,
            "SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass",
            table,
        )
    )
    return bool(val)


def _rls_forced(dsn: str, table: str) -> bool:
    val = asyncio.run(
        _fetchval(
            dsn,
            "SELECT relforcerowsecurity FROM pg_class WHERE oid = $1::regclass",
            table,
        )
    )
    return bool(val)


def _policy_exists(dsn: str, table: str, policy: str) -> bool:
    val = asyncio.run(
        _fetchval(
            dsn,
            "SELECT count(*) FROM pg_policies WHERE tablename = $1 AND policyname = $2",
            table,
            policy,
        )
    )
    return bool(val)


def _seed_task(dsn: str, *, tenant_id: UUID, project_id: UUID, task_id: UUID) -> None:
    """Seed a project + task for a tenant (BYPASSRLS role)."""
    asyncio.run(
        _execute(
            dsn,
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'P')",
            project_id,
            tenant_id,
        )
    )
    asyncio.run(
        _execute(
            dsn,
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority)"
            " VALUES ($1, $2, $3, 'human task', 'in_progress', 'medium')",
            task_id,
            tenant_id,
            project_id,
        )
    )


def _seed_user(dsn: str, *, user_id: UUID, email: str) -> None:
    asyncio.run(
        _execute(
            dsn,
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'h')",
            user_id,
            email,
        )
    )


async def _grant_app_user() -> None:
    """Retro-grant DML to app_user on the freshly-created table (the ALTER
    DEFAULT PRIVILEGES only apply to tables created after they were set;
    0068 runs before grant in the test's own upgrade)."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_table_and_columns_exist(alembic_config: object, admin_pg_dsn: str) -> None:
    """After upgrade head the table exists with every column the task names."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    assert _table_exists(admin_pg_dsn, "human_work_sessions")
    cols = _columns(admin_pg_dsn, "human_work_sessions")
    expected = {
        "id",
        "tenant_id",
        "task_id",
        "user_id",
        "start_at",
        "end_at",
        "hours_logged",
        "comments",
        "output_files_attached",
        "created_at",
        "updated_at",
    }
    missing = expected - cols
    assert not missing, f"human_work_sessions missing columns: {missing}"


def test_foreign_keys_exist(alembic_config: object, admin_pg_dsn: str) -> None:
    """FKs to tasks (task_id) and users (user_id) exist."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    edges = _fk_targets(admin_pg_dsn, "human_work_sessions")
    assert "task_id->>tasks" in edges
    assert "user_id->>users" in edges


def test_rls_enabled_and_policy_present(alembic_config: object, admin_pg_dsn: str) -> None:
    """RLS is ENABLED + FORCED and the tenant-isolation policy exists —
    the same shape the executions table uses (the Execution this replaces)."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    assert _rls_enabled(admin_pg_dsn, "human_work_sessions")
    assert _rls_forced(admin_pg_dsn, "human_work_sessions")
    assert _policy_exists(
        admin_pg_dsn, "human_work_sessions", "human_work_sessions_tenant_isolation"
    )


def test_hours_logged_and_attachments_shape(
    alembic_config: object, admin_pg_dsn: str, migrations_pg_dsn: str
) -> None:
    """A session round-trips a Numeric hours_logged and a JSONB attachment
    list; a row without either takes the defaults (NULL hours, [] files)."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    tenant_id, project_id, task_id = uuid7(), uuid7(), uuid7()
    user_id = uuid7()
    _seed_task(migrations_pg_dsn, tenant_id=tenant_id, project_id=project_id, task_id=task_id)
    _seed_user(migrations_pg_dsn, user_id=user_id, email="worker@hws.test")

    # Session WITH hours + attachments.
    full_id = uuid7()
    attachments = [
        {"type": "file", "name": "report.pdf"},
        {"type": "url", "href": "https://example.test/x"},
    ]
    asyncio.run(
        _execute(
            migrations_pg_dsn,
            "INSERT INTO human_work_sessions"
            " (id, tenant_id, task_id, user_id, hours_logged, comments, output_files_attached)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
            full_id,
            tenant_id,
            task_id,
            user_id,
            Decimal("3.50"),
            "Reviewed the contract.",
            json.dumps(attachments),
        )
    )
    got_hours = asyncio.run(
        _fetchval(
            admin_pg_dsn,
            "SELECT hours_logged FROM human_work_sessions WHERE id = $1",
            full_id,
        )
    )
    assert got_hours == Decimal("3.50")
    got_files = asyncio.run(
        _fetchval(
            admin_pg_dsn,
            "SELECT output_files_attached FROM human_work_sessions WHERE id = $1",
            full_id,
        )
    )
    assert json.loads(str(got_files)) == attachments

    # Minimal session: no hours, no attachments -> NULL + [] defaults.
    bare_id = uuid7()
    asyncio.run(
        _execute(
            migrations_pg_dsn,
            "INSERT INTO human_work_sessions (id, tenant_id, task_id, user_id)"
            " VALUES ($1, $2, $3, $4)",
            bare_id,
            tenant_id,
            task_id,
            user_id,
        )
    )
    bare_hours = asyncio.run(
        _fetchval(
            admin_pg_dsn,
            "SELECT hours_logged FROM human_work_sessions WHERE id = $1",
            bare_id,
        )
    )
    assert bare_hours is None
    bare_files = asyncio.run(
        _fetchval(
            admin_pg_dsn,
            "SELECT output_files_attached FROM human_work_sessions WHERE id = $1",
            bare_id,
        )
    )
    assert json.loads(str(bare_files)) == []


def test_hours_logged_rejects_negative(alembic_config: object, migrations_pg_dsn: str) -> None:
    """The CHECK rejects a negative hours_logged value."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    tenant_id, project_id, task_id = uuid7(), uuid7(), uuid7()
    _seed_task(migrations_pg_dsn, tenant_id=tenant_id, project_id=project_id, task_id=task_id)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        asyncio.run(
            _execute(
                migrations_pg_dsn,
                "INSERT INTO human_work_sessions (id, tenant_id, task_id, hours_logged)"
                " VALUES ($1, $2, $3, $4)",
                uuid7(),
                tenant_id,
                task_id,
                Decimal("-1.00"),
            )
        )


@pytest.mark.cross_tenant
def test_session_is_tenant_isolated(alembic_config: object, migrations_pg_dsn: str) -> None:
    """A session seeded for tenant A is invisible to a session pinned to
    tenant B under RLS (and visible to tenant A). Exercises the real policy
    via the NOBYPASSRLS app_user role."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    asyncio.run(_grant_app_user())

    tenant_a, tenant_b = uuid7(), uuid7()
    project_id, task_id, session_id = uuid7(), uuid7(), uuid7()
    _seed_task(migrations_pg_dsn, tenant_id=tenant_a, project_id=project_id, task_id=task_id)
    asyncio.run(
        _execute(
            migrations_pg_dsn,
            "INSERT INTO human_work_sessions (id, tenant_id, task_id) VALUES ($1, $2, $3)",
            session_id,
            tenant_a,
            task_id,
        )
    )

    async def _count_visible(tenant_id: UUID) -> int:
        conn = await asyncpg.connect(_app_dsn())
        try:
            # Pin the session tenant the way the app middleware does.
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_id))
            val = await conn.fetchval(
                "SELECT count(*) FROM human_work_sessions WHERE id = $1", session_id
            )
            return int(val)
        finally:
            await conn.close()

    assert asyncio.run(_count_visible(tenant_a)) == 1, "tenant A must see its own session"
    assert asyncio.run(_count_visible(tenant_b)) == 0, "tenant B must NOT see tenant A's session"


@pytest.mark.cross_tenant
def test_rls_blocks_cross_tenant_insert(alembic_config: object, migrations_pg_dsn: str) -> None:
    """An app_user session pinned to tenant B cannot INSERT a session row for
    tenant A — the FOR ALL policy's WITH CHECK (inherited from USING) rejects
    it. Mirrors the executions tenant-isolation guarantee."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    asyncio.run(_grant_app_user())

    tenant_a, tenant_b = uuid7(), uuid7()
    project_id, task_id = uuid7(), uuid7()
    # Seed the task for tenant A with the BYPASSRLS role.
    _seed_task(migrations_pg_dsn, tenant_id=tenant_a, project_id=project_id, task_id=task_id)

    async def _insert_as_tenant_b() -> None:
        conn = await asyncpg.connect(_app_dsn())
        try:
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_b))
            await conn.execute(
                "INSERT INTO human_work_sessions (id, tenant_id, task_id) VALUES ($1, $2, $3)",
                uuid7(),
                tenant_a,  # tenant A row while pinned to tenant B -> rejected
                task_id,
            )
        finally:
            await conn.close()

    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        asyncio.run(_insert_as_tenant_b())


def test_migration_is_reversible(alembic_config: object, admin_pg_dsn: str) -> None:
    """head -> 0067 -> head: the table + policy are dropped on downgrade and
    recreated on the second upgrade (idempotent, fully reversible)."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert _table_exists(admin_pg_dsn, "human_work_sessions")
    assert _policy_exists(
        admin_pg_dsn, "human_work_sessions", "human_work_sessions_tenant_isolation"
    )

    command.downgrade(alembic_config, "0067_human_agent_config")  # type: ignore[arg-type]
    assert not _table_exists(admin_pg_dsn, "human_work_sessions")

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert _table_exists(admin_pg_dsn, "human_work_sessions")
    assert _policy_exists(
        admin_pg_dsn, "human_work_sessions", "human_work_sessions_tenant_isolation"
    )
