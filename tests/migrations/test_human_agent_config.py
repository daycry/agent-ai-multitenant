"""Migration coverage for ``human_agent_config`` (Plan 16 task_16_02).

Migration 0067 creates the ``human_agent_config`` side table holding the
human-specific fields of an ``agent_type='human'`` Agent (Plan 16 Decisiones
Clave: ``agent_type`` extends the EXISTING Agent rather than a separate
entity). These tests assert the end state required by the plan:

  - the table and every column the task names exist;
  - the FKs to ``agents`` and ``users`` exist;
  - ``acceptance_timeout_hours`` defaults to 24 (Decisiones Clave);
  - RLS is enabled (+ FORCE) on the table — same shape as ``agents``;
  - ``assignment_mode`` is DB-constrained to ``specific_user`` (MVP only);
  - a config row is tenant-isolated under RLS (@pytest.mark.cross_tenant):
    a session pinned to tenant A sees only tenant A's row;
  - the migration is reversible (head -> 0066 -> head) and the table /
    policy come and go with it.

The cross-tenant test connects as ``app_user`` (NOBYPASSRLS) and pins the
``app.tenant_id`` GUC the way production does, so it exercises the real RLS
policy — not the BYPASSRLS migrations/admin role the other tests seed with.
"""

from __future__ import annotations

import asyncio
import os
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


def _column_default(dsn: str, table: str, column: str) -> str | None:
    val = asyncio.run(
        _fetchval(
            dsn,
            """
            SELECT column_default
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = $1
               AND column_name = $2
            """,
            table,
            column,
        )
    )
    return None if val is None else str(val)


def _columns(dsn: str, table: str) -> set[str]:
    rows = asyncio.run(_fetch(dsn, _COLS_SQL, table))
    return {str(r["column_name"]) for r in rows}


_COLS_SQL = """
    SELECT column_name
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = $1
"""


async def _fetch(dsn: str, sql: str, *args: object) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


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


def _seed_human_agent(
    dsn: str,
    *,
    tenant_id: UUID,
    agent_id: UUID,
    config_id: UUID,
    assignment_mode: str = "specific_user",
) -> None:
    """Seed an agent_type=human agent + its config (BYPASSRLS role)."""
    asyncio.run(
        _execute(
            dsn,
            "INSERT INTO agents"
            " (id, tenant_id, name, role, system_prompt, scope, agent_type)"
            " VALUES ($1, $2, 'human-agent', 'reviewer', 'You review.',"
            "         'global_tenant_template', 'human')",
            agent_id,
            tenant_id,
        )
    )
    asyncio.run(
        _execute(
            dsn,
            "INSERT INTO human_agent_config"
            " (id, tenant_id, agent_id, assignment_mode)"
            " VALUES ($1, $2, $3, $4)",
            config_id,
            tenant_id,
            agent_id,
            assignment_mode,
        )
    )


async def _grant_app_user() -> None:
    """Retro-grant DML to app_user on the freshly-created table (the ALTER
    DEFAULT PRIVILEGES only apply to tables created after they were set;
    0067 runs before grant in the test's own upgrade)."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_table_and_columns_exist(alembic_config: object, admin_pg_dsn: str) -> None:
    """After upgrade head the table exists with every column the task names."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    assert _table_exists(admin_pg_dsn, "human_agent_config")
    cols = _columns(admin_pg_dsn, "human_agent_config")
    expected = {
        "id",
        "tenant_id",
        "agent_id",
        "assignment_mode",
        "assigned_user_id",
        "hourly_rate",
        "hourly_rate_currency",
        "notification_channels",
        "acceptance_timeout_hours",
        "escalation_target_user_id",
        "expected_response_time_hours",
        "expected_execution_time_hours",
        "created_at",
        "updated_at",
    }
    missing = expected - cols
    assert not missing, f"human_agent_config missing columns: {missing}"


def test_foreign_keys_exist(alembic_config: object, admin_pg_dsn: str) -> None:
    """FKs to agents (agent_id) and users (assigned/escalation) exist."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    edges = _fk_targets(admin_pg_dsn, "human_agent_config")
    assert "agent_id->>agents" in edges
    assert "assigned_user_id->>users" in edges
    assert "escalation_target_user_id->>users" in edges


def test_acceptance_timeout_defaults_to_24(alembic_config: object, admin_pg_dsn: str) -> None:
    """acceptance_timeout_hours defaults to 24 (Plan 16 Decisiones Clave)."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    default = _column_default(admin_pg_dsn, "human_agent_config", "acceptance_timeout_hours")
    assert default is not None
    assert "24" in default, f"acceptance_timeout_hours default is not 24: {default!r}"


def test_acceptance_timeout_applied_on_insert(
    alembic_config: object, admin_pg_dsn: str, migrations_pg_dsn: str
) -> None:
    """A row inserted without acceptance_timeout_hours takes the 24 default."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    tenant_id, agent_id, config_id = uuid7(), uuid7(), uuid7()
    _seed_human_agent(
        migrations_pg_dsn, tenant_id=tenant_id, agent_id=agent_id, config_id=config_id
    )
    got = asyncio.run(
        _fetchval(
            admin_pg_dsn,
            "SELECT acceptance_timeout_hours FROM human_agent_config WHERE id = $1",
            config_id,
        )
    )
    assert got == 24


def test_rls_enabled_and_policy_present(alembic_config: object, admin_pg_dsn: str) -> None:
    """RLS is ENABLED + FORCED and the tenant-isolation policy exists —
    the same shape the agents table uses (migration 0002)."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    assert _rls_enabled(admin_pg_dsn, "human_agent_config")
    assert _rls_forced(admin_pg_dsn, "human_agent_config")
    assert _policy_exists(admin_pg_dsn, "human_agent_config", "human_agent_config_tenant_isolation")


def test_assignment_mode_constrained_to_specific_user(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """MVP: the CHECK rejects any assignment_mode other than specific_user."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    tenant_id, agent_id, config_id = uuid7(), uuid7(), uuid7()
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        _seed_human_agent(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            agent_id=agent_id,
            config_id=config_id,
            assignment_mode="role_queue",
        )


def test_assignment_mode_defaults_to_specific_user(
    alembic_config: object, admin_pg_dsn: str, migrations_pg_dsn: str
) -> None:
    """A row inserted without assignment_mode takes the specific_user default."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    tenant_id, agent_id, config_id = uuid7(), uuid7(), uuid7()
    asyncio.run(
        _execute(
            migrations_pg_dsn,
            "INSERT INTO agents"
            " (id, tenant_id, name, role, system_prompt, scope, agent_type)"
            " VALUES ($1, $2, 'human-agent', 'reviewer', 'You review.',"
            "         'global_tenant_template', 'human')",
            agent_id,
            tenant_id,
        )
    )
    asyncio.run(
        _execute(
            migrations_pg_dsn,
            "INSERT INTO human_agent_config (id, tenant_id, agent_id) VALUES ($1, $2, $3)",
            config_id,
            tenant_id,
            agent_id,
        )
    )
    got = asyncio.run(
        _fetchval(
            admin_pg_dsn,
            "SELECT assignment_mode FROM human_agent_config WHERE id = $1",
            config_id,
        )
    )
    assert got == "specific_user"


@pytest.mark.cross_tenant
def test_config_is_tenant_isolated(alembic_config: object, migrations_pg_dsn: str) -> None:
    """A config seeded for tenant A is invisible to a session pinned to
    tenant B under RLS (and visible to tenant A). Exercises the real policy
    via the NOBYPASSRLS app_user role."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    asyncio.run(_grant_app_user())

    tenant_a, tenant_b = uuid7(), uuid7()
    agent_id, config_id = uuid7(), uuid7()
    _seed_human_agent(migrations_pg_dsn, tenant_id=tenant_a, agent_id=agent_id, config_id=config_id)

    async def _count_visible(tenant_id: UUID) -> int:
        conn = await asyncpg.connect(_app_dsn())
        try:
            # Pin the session tenant the way the app middleware does.
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_id))
            val = await conn.fetchval(
                "SELECT count(*) FROM human_agent_config WHERE id = $1", config_id
            )
            return int(val)
        finally:
            await conn.close()

    assert asyncio.run(_count_visible(tenant_a)) == 1, "tenant A must see its own config"
    assert asyncio.run(_count_visible(tenant_b)) == 0, "tenant B must NOT see tenant A's config"


@pytest.mark.cross_tenant
def test_rls_blocks_cross_tenant_insert(alembic_config: object, migrations_pg_dsn: str) -> None:
    """An app_user session pinned to tenant B cannot INSERT a config row for
    tenant A — the FOR ALL policy's WITH CHECK (inherited from USING) rejects
    it. Mirrors the agents tenant-isolation guarantee."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    asyncio.run(_grant_app_user())

    tenant_a, tenant_b = uuid7(), uuid7()
    agent_id = uuid7()
    # Seed the agent for tenant A with the BYPASSRLS role.
    asyncio.run(
        _execute(
            migrations_pg_dsn,
            "INSERT INTO agents"
            " (id, tenant_id, name, role, system_prompt, scope, agent_type)"
            " VALUES ($1, $2, 'human-agent', 'reviewer', 'You review.',"
            "         'global_tenant_template', 'human')",
            agent_id,
            tenant_a,
        )
    )

    async def _insert_as_tenant_b() -> None:
        conn = await asyncpg.connect(_app_dsn())
        try:
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_b))
            await conn.execute(
                "INSERT INTO human_agent_config (id, tenant_id, agent_id) VALUES ($1, $2, $3)",
                uuid7(),
                tenant_a,  # tenant A row while pinned to tenant B -> rejected
                agent_id,
            )
        finally:
            await conn.close()

    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        asyncio.run(_insert_as_tenant_b())


def test_migration_is_reversible(alembic_config: object, admin_pg_dsn: str) -> None:
    """head -> 0066 -> head: the table + policy are dropped on downgrade and
    recreated on the second upgrade (idempotent, fully reversible)."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert _table_exists(admin_pg_dsn, "human_agent_config")
    assert _policy_exists(admin_pg_dsn, "human_agent_config", "human_agent_config_tenant_isolation")

    command.downgrade(alembic_config, "0066_agent_type_check")  # type: ignore[arg-type]
    assert not _table_exists(admin_pg_dsn, "human_agent_config")

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert _table_exists(admin_pg_dsn, "human_agent_config")
    assert _policy_exists(admin_pg_dsn, "human_agent_config", "human_agent_config_tenant_isolation")
