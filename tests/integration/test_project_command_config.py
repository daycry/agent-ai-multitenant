"""Integration tests for the polyglot project command config (Plan 06.16 task_06_16_01).

Plan 06.16 makes the engine's existing polyglot machinery usable by an
operator: two per-project config fields land first (this task), wired in
by later tasks (``shell_exec`` allowlist + ``run_*`` runtime resolution).

  * ``allowed_commands`` — the **deny-by-default** allowlist of program
    basenames the ``shell_exec`` builtin may run. ``TEXT[]`` NOT NULL
    DEFAULT ``'{}'`` so every project starts deny-all (empty list runs
    nothing).
  * ``default_runtime_template`` — the stack's runtime template id the
    ``run_*`` tools resolve against. ``TEXT`` nullable; NULL = keep each
    tool's current default (backward-compatible).

These tests drive both fields end-to-end against the real Postgres
through the ``/projects`` API (so RLS + the schemas + the router are all
exercised), and prove migration 0072 is reversible (head -> 0071 ->
head). Fixture wiring mirrors ``test_projects_endpoints.py``.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed: one tenant + admin (tenant_admin can create/update projects).
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    user_a = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE team_members, teams, projects, agents,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "tenant-a-cmdcfg",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-cmdcfg",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_a,
            "alice@cmdcfg.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_a,
            user_a,
        )
    finally:
        await conn.close()

    return {"tenant_a": tenant_a, "user_a": user_a}


# ---------------------------------------------------------------------------
# Fixtures (same pattern as test_projects_endpoints.configured_app).
# ---------------------------------------------------------------------------
@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")

    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _minimal_payload(**overrides) -> dict:
    base = {"name": "PHP project"}
    base.update(overrides)
    return base


# ===========================================================================
# Default is an empty list (deny-all) + NULL runtime
# ===========================================================================
@pytest.mark.asyncio
async def test_defaults_are_empty_allowlist_and_null_runtime(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post("/projects", json=_minimal_payload(), headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Deny-by-default: an unconfigured project starts with an EMPTY allowlist.
    assert body["allowed_commands"] == []
    # No stack pinned yet -> per-tool default runtime (backward-compatible).
    assert body["default_runtime_template"] is None


# ===========================================================================
# Create with allowed_commands + runtime persists and round-trips
# ===========================================================================
@pytest.mark.asyncio
async def test_create_with_allowed_commands_persists(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    php_cmds = ["php", "composer", "vendor/bin/phpunit", "pest"]

    async with _client(configured_app) as client:
        create = await client.post(
            "/projects",
            json=_minimal_payload(
                allowed_commands=php_cmds,
                default_runtime_template="php-phpunit",
            ),
            headers=headers,
        )
        assert create.status_code == 201, create.text
        body = create.json()
        assert body["allowed_commands"] == php_cmds
        assert body["default_runtime_template"] == "php-phpunit"
        project_id = body["id"]

        # Round-trips on a fresh GET (persisted, not just echoed).
        fetched = await client.get(f"/projects/{project_id}", headers=headers)
        assert fetched.status_code == 200
        fbody = fetched.json()
        assert fbody["allowed_commands"] == php_cmds
        assert fbody["default_runtime_template"] == "php-phpunit"


@pytest.mark.asyncio
async def test_create_normalises_allowed_commands(configured_app, migrations_pg_dsn: str) -> None:
    """Blank entries are dropped, surrounding whitespace stripped, dupes
    removed (order preserved) — the chips/presets UI never sends a tidy
    list, so the schema tidies it."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/projects",
            json=_minimal_payload(
                allowed_commands=["  php  ", "composer", "", "php", "npm"],
            ),
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["allowed_commands"] == ["php", "composer", "npm"]


# ===========================================================================
# Update changes the allowlist + runtime (and can clear them)
# ===========================================================================
@pytest.mark.asyncio
async def test_update_changes_allowed_commands_and_runtime(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        create = await client.post(
            "/projects",
            json=_minimal_payload(
                allowed_commands=["php", "composer"],
                default_runtime_template="php-phpunit",
            ),
            headers=headers,
        )
        assert create.status_code == 201, create.text
        project_id = create.json()["id"]

        # Switch the stack to Node.
        upd = await client.put(
            f"/projects/{project_id}",
            json={
                "allowed_commands": ["npm", "npx", "node"],
                "default_runtime_template": "node-jest",
            },
            headers=headers,
        )
        assert upd.status_code == 200, upd.text
        ubody = upd.json()
        assert ubody["allowed_commands"] == ["npm", "npx", "node"]
        assert ubody["default_runtime_template"] == "node-jest"

        # An untouched PUT (only name) leaves both fields alone.
        kept = await client.put(
            f"/projects/{project_id}",
            json={"description": "still node"},
            headers=headers,
        )
        assert kept.status_code == 200, kept.text
        assert kept.json()["allowed_commands"] == ["npm", "npx", "node"]
        assert kept.json()["default_runtime_template"] == "node-jest"

        # Clearing: empty allowlist -> deny-all; null runtime -> per-tool default.
        cleared = await client.put(
            f"/projects/{project_id}",
            json={"allowed_commands": [], "default_runtime_template": None},
            headers=headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["allowed_commands"] == []
        assert cleared.json()["default_runtime_template"] is None


# ===========================================================================
# Migration reversibility (head -> 0071 -> head)
# ===========================================================================
async def _fetchval(dsn: str, sql: str, *args: object) -> object:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


def test_migration_reversible(alembic_config, admin_pg_dsn: str) -> None:
    cols_sql = (
        "SELECT count(*) FROM information_schema.columns"
        " WHERE table_name = 'projects'"
        " AND column_name IN ('allowed_commands', 'default_runtime_template')"
    )

    def _both_columns() -> bool:
        return int(asyncio.run(_fetchval(admin_pg_dsn, cols_sql))) == 2  # type: ignore[arg-type]

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert _both_columns()

    command.downgrade(alembic_config, "0071_model_prices_provider_id")  # type: ignore[arg-type]
    assert int(asyncio.run(_fetchval(admin_pg_dsn, cols_sql))) == 0  # type: ignore[arg-type]

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert _both_columns()
