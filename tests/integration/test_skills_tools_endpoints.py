"""Integration tests for /skills and /tools endpoints (task_01_05).

Catalog + custom is implemented via the `is_builtin` column + the
SELECT-only `<table>_builtin_read` policies added in migration 0005.
Tenant users CRUD their own rows and read built-ins; writes to a
built-in are rejected as 404 (no info leak).
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
# Seed: two tenants + one built-in skill + one built-in tool, both
# owned by the platform tenant so RLS visibility goes through the
# SELECT-only policy rather than via tenant_id match.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    builtin_skill = uuid4()
    builtin_tool = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE skills, tools, agents, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
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
            "INSERT INTO skills (id, tenant_id, name, category, prompt_fragment,"
            " is_builtin) VALUES ($1, $2, $3, $4, $5, true)",
            builtin_skill,
            _PLATFORM_TENANT_ID,
            "Catalog: Code Review",
            "review",
            "Review code for correctness, style, and security.",
        )
        await conn.execute(
            "INSERT INTO tools (id, tenant_id, name, category, implementation_type,"
            " is_builtin) VALUES ($1, $2, $3, $4, $5, true)",
            builtin_tool,
            _PLATFORM_TENANT_ID,
            "Catalog: HTTP Fetch",
            "network",
            "builtin",
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "builtin_skill": builtin_skill,
        "builtin_tool": builtin_tool,
    }


# ---------------------------------------------------------------------------
# Fixtures (identical shape to test_agents_endpoints.py)
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


# ===========================================================================
# Skills
# ===========================================================================
@pytest.mark.asyncio
async def test_skills_unauthenticated_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/skills")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_skills_crud_roundtrip(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        create = await client.post(
            "/skills",
            json={
                "name": "Custom: SQL Tuning",
                "category": "data",
                "prompt_fragment": "You can tune slow PostgreSQL queries.",
                "required_tools": [],
            },
            headers=headers,
        )
        assert create.status_code == 201, create.text
        body = create.json()
        assert body["is_builtin"] is False
        assert UUID(body["tenant_id"]) == seeded["tenant_a"]
        skill_id = body["id"]

        # LIST: tenant's custom skill + the global built-in.
        listed = await client.get("/skills", headers=headers)
        assert listed.status_code == 200
        names = {s["name"] for s in listed.json()}
        assert {"Custom: SQL Tuning", "Catalog: Code Review"} <= names

        # FILTER by is_builtin=true.
        builtins = await client.get("/skills?is_builtin=true", headers=headers)
        assert {s["name"] for s in builtins.json()} == {"Catalog: Code Review"}
        assert all(s["is_builtin"] for s in builtins.json())

        # PUT
        upd = await client.put(
            f"/skills/{skill_id}",
            json={"category": "performance"},
            headers=headers,
        )
        assert upd.status_code == 200
        assert upd.json()["category"] == "performance"

        # DELETE
        dele = await client.delete(f"/skills/{skill_id}", headers=headers)
        assert dele.status_code == 204
        gone = await client.get(f"/skills/{skill_id}", headers=headers)
        assert gone.status_code == 404


@pytest.mark.asyncio
async def test_skills_builtin_is_readable_but_not_writable(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    builtin_id = seeded["builtin_skill"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        got = await client.get(f"/skills/{builtin_id}", headers=headers)
        assert got.status_code == 200
        assert got.json()["is_builtin"] is True

        upd = await client.put(
            f"/skills/{builtin_id}",
            json={"category": "hijacked"},
            headers=headers,
        )
        assert upd.status_code == 404

        dele = await client.delete(f"/skills/{builtin_id}", headers=headers)
        assert dele.status_code == 404


@pytest.mark.asyncio
async def test_skills_tenant_isolation(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["user_b"], seeded["tenant_b"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/skills",
            json={
                "name": "A's secret skill",
                "category": "internal",
                "prompt_fragment": "Confidential.",
            },
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert created.status_code == 201
        secret_id = created.json()["id"]

        listed_b = await client.get("/skills", headers={"Authorization": f"Bearer {token_b}"})
        names = {s["name"] for s in listed_b.json()}
        assert "A's secret skill" not in names
        assert "Catalog: Code Review" in names

        fetch = await client.get(
            f"/skills/{secret_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert fetch.status_code == 404


# ===========================================================================
# Tools
# ===========================================================================
@pytest.mark.asyncio
async def test_tools_crud_roundtrip(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        create = await client.post(
            "/tools",
            json={
                "name": "Custom: Internal API",
                "category": "integration",
                "implementation_type": "http_endpoint",
                "implementation_ref": "https://internal/api",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "timeout_seconds": 30,
            },
            headers=headers,
        )
        assert create.status_code == 201, create.text
        body = create.json()
        assert body["is_builtin"] is False
        assert body["security_level"] == "safe"  # default
        tool_id = body["id"]

        listed = await client.get("/tools", headers=headers)
        names = {t["name"] for t in listed.json()}
        assert {"Custom: Internal API", "Catalog: HTTP Fetch"} <= names

        # FILTER by category.
        filtered = await client.get("/tools?category=network", headers=headers)
        assert {t["name"] for t in filtered.json()} == {"Catalog: HTTP Fetch"}

        # PUT
        upd = await client.put(
            f"/tools/{tool_id}",
            json={"timeout_seconds": 90, "security_level": "sandboxed"},
            headers=headers,
        )
        assert upd.status_code == 200
        assert upd.json()["timeout_seconds"] == 90
        assert upd.json()["security_level"] == "sandboxed"

        # DELETE
        dele = await client.delete(f"/tools/{tool_id}", headers=headers)
        assert dele.status_code == 204


@pytest.mark.asyncio
async def test_tools_builtin_is_readable_but_not_writable(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    builtin_id = seeded["builtin_tool"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        got = await client.get(f"/tools/{builtin_id}", headers=headers)
        assert got.status_code == 200
        assert got.json()["is_builtin"] is True

        upd = await client.put(
            f"/tools/{builtin_id}",
            json={"timeout_seconds": 5},
            headers=headers,
        )
        assert upd.status_code == 404


@pytest.mark.asyncio
async def test_tools_timeout_must_be_positive(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/tools",
            json={
                "name": "Bad Tool",
                "category": "data",
                "implementation_type": "builtin",
                "timeout_seconds": 0,
            },
            headers=headers,
        )
    assert resp.status_code == 422
