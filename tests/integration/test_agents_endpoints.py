"""Integration tests for /agents endpoints (task_01_04).

Verifies CRUD with scope filters, cross-tenant isolation via RLS, the
visibility carve-out for `global_builtin` agents (migration 0004), and
the validation rules that mirror the DB CHECK constraint.

The fixture pattern is borrowed from `test_isolation.py`: seed two
tenants directly via the BYPASSRLS migrations role, mint JWTs that bind
each user to one tenant, then drive the API via AsyncClient.
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


# ---------------------------------------------------------------------------
# Seed: two tenants + users + memberships + projects (for project_local
# scope tests) + one global_builtin agent owned by a platform tenant.
# ---------------------------------------------------------------------------
_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    project_a = uuid4()
    builtin_agent = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        # Clean slate -- our migrations 0002 tables get truncated too.
        await conn.execute(
            "TRUNCATE agents, projects, user_org_memberships, organizations,"
            " users RESTART IDENTITY CASCADE"
        )

        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Tenant A",
            "tenant-a",
            tenant_b,
            "Tenant B",
            "tenant-b",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES" " ($1, $2, $3), ($4, $5, $6)",
            user_a,
            "alice@a.test",
            "argon2-placeholder",
            user_b,
            "bob@b.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4), ($5, $6, $7, $8)",
            uuid4(),
            tenant_a,
            user_a,
            "tenant_admin",
            uuid4(),
            tenant_b,
            user_b,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_a,
            tenant_a,
            "Project A",
        )
        # A global_builtin agent owned by the platform tenant. Tenant
        # users must see it via the agents_global_builtin_read policy.
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt,"
            " model_config, scope, project_id)"
            " VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, NULL)",
            builtin_agent,
            _PLATFORM_TENANT_ID,
            "Built-in PM",
            "project_manager",
            "You are a project manager.",
            "{}",
            "global_builtin",
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "project_a": project_a,
        "builtin_agent": builtin_agent,
    }


# ---------------------------------------------------------------------------
# Fixtures
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


def _minimal_payload(**overrides) -> dict:
    base = {
        "name": "Backend Senior",
        "role": "backend_dev",
        "system_prompt": "You are a senior backend engineer.",
        "scope": "global_tenant_template",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unauthenticated_list_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/agents")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# CRUD happy path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_list_get_update_delete_roundtrip(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # CREATE
        create = await client.post(
            "/agents",
            json=_minimal_payload(name="QA Lead", role="qa"),
            headers=headers,
        )
        assert create.status_code == 201, create.text
        body = create.json()
        agent_id = body["id"]
        assert body["name"] == "QA Lead"
        assert body["scope"] == "global_tenant_template"
        assert body["project_id"] is None
        assert UUID(body["tenant_id"]) == seeded["tenant_a"]

        # LIST shows the new agent + the seeded global_builtin (visible
        # to every tenant via agents_global_builtin_read).
        listed = await client.get("/agents", headers=headers)
        assert listed.status_code == 200, listed.text
        names = {a["name"] for a in listed.json()}
        assert {"QA Lead", "Built-in PM"} <= names

        # GET single
        got = await client.get(f"/agents/{agent_id}", headers=headers)
        assert got.status_code == 200
        assert got.json()["name"] == "QA Lead"

        # PUT (partial)
        upd = await client.put(
            f"/agents/{agent_id}",
            json={"name": "QA Lead v2", "max_concurrent_tasks": 4},
            headers=headers,
        )
        assert upd.status_code == 200, upd.text
        assert upd.json()["name"] == "QA Lead v2"
        assert upd.json()["max_concurrent_tasks"] == 4

        # DELETE
        deleted = await client.delete(f"/agents/{agent_id}", headers=headers)
        assert deleted.status_code == 204

        # GET on deleted row -> 404
        gone = await client.get(f"/agents/{agent_id}", headers=headers)
        assert gone.status_code == 404


# ---------------------------------------------------------------------------
# Scope filter + project_id consistency
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_project_local_requires_project_id(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/agents",
            json=_minimal_payload(scope="project_local"),
            headers=headers,
        )
    assert resp.status_code == 422
    assert "project_id" in resp.text


@pytest.mark.asyncio
async def test_create_global_template_must_not_carry_project_id(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/agents",
            json=_minimal_payload(
                scope="global_tenant_template", project_id=str(seeded["project_a"])
            ),
            headers=headers,
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_global_builtin_is_forbidden(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/agents",
            json=_minimal_payload(scope="global_builtin"),
            headers=headers,
        )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_filter_by_scope(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # Create a project_local agent in tenant A.
        await client.post(
            "/agents",
            json=_minimal_payload(
                scope="project_local",
                name="Project Helper",
                role="worker",
                project_id=str(seeded["project_a"]),
            ),
            headers=headers,
        )

        builtin_only = await client.get("/agents?scope=global_builtin", headers=headers)
        local_only = await client.get("/agents?scope=project_local", headers=headers)

    assert builtin_only.status_code == 200
    assert all(a["scope"] == "global_builtin" for a in builtin_only.json())
    assert {a["name"] for a in builtin_only.json()} == {"Built-in PM"}

    assert local_only.status_code == 200
    assert {a["name"] for a in local_only.json()} == {"Project Helper"}


# ---------------------------------------------------------------------------
# Multi-tenant isolation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tenant_b_cannot_see_tenant_a_local_agents(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["user_b"], seeded["tenant_b"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/agents",
            json=_minimal_payload(name="A's secret agent"),
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert created.status_code == 201
        agent_id = created.json()["id"]

        # Tenant B's list MUST omit A's private agent. But still
        # includes the global_builtin.
        listed_b = await client.get("/agents", headers={"Authorization": f"Bearer {token_b}"})
        assert listed_b.status_code == 200
        names = {a["name"] for a in listed_b.json()}
        assert "A's secret agent" not in names
        assert "Built-in PM" in names

        # Direct fetch by id -> 404 from B (don't leak existence).
        fetch_b = await client.get(
            f"/agents/{agent_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert fetch_b.status_code == 404


@pytest.mark.asyncio
async def test_tenant_cannot_mutate_global_builtin(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token_a}"}
    builtin_id = seeded["builtin_agent"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # Tenant A can READ the built-in -- visible via SELECT policy.
        got = await client.get(f"/agents/{builtin_id}", headers=headers)
        assert got.status_code == 200
        assert got.json()["scope"] == "global_builtin"

        # ...but can't update it: the router filters writes by tenant_id
        # so it 404s (avoids leaking that it's read-only).
        upd = await client.put(
            f"/agents/{builtin_id}",
            json={"name": "Hijacked"},
            headers=headers,
        )
        assert upd.status_code == 404

        # ...and can't delete it either.
        dele = await client.delete(f"/agents/{builtin_id}", headers=headers)
        assert dele.status_code == 404
