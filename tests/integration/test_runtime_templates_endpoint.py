"""Integration tests for GET /runtime-templates + default_runtime_template
validation (Plan 06.18 task_06_18_08, ADR 0051).

The endpoint projects ``shared_test_runtimes.CATALOG`` (id, ES+EN label,
dep_cache_mount, network_policy) the same way ``GET /mcp-catalog`` projects
the MCP server catalog — the backend is the single source of truth so the
frontend stops triple-hardcoding the 12-vs-14 divergent arrays.

The catalog is project-agnostic and identical across tenants (same as
mcp-catalog), so there are no tenant-scoped rows to leak; the cross-tenant
surface is the auth gate (401 unauthenticated) and the consistency check
that two different tenants observe the exact same catalog.

`default_runtime_template` on ProjectCreate/UpdateRequest now rejects any
id outside the catalog with 422, reusing the guard that already lived only
in dep_cache.py.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from shared_test_runtimes import CATALOG
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE team_members, teams, projects, agents,"
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
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
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
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
    }


# ---------------------------------------------------------------------------
# Fixtures (same pattern as test_projects_endpoints.py)
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
        app.dependency_overrides.clear()
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


def _minimal_project(**overrides) -> dict:
    base = {"name": "Demo API"}
    base.update(overrides)
    return base


# ===========================================================================
# GET /runtime-templates
# ===========================================================================
@pytest.mark.asyncio
async def test_runtime_templates_unauthenticated_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/runtime-templates")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_runtime_templates_projects_full_catalog(
    configured_app, migrations_pg_dsn: str
) -> None:
    """All 14 catalog entries are returned, in declared order, with the
    id, ES+EN label, dep_cache_mount and network_policy fields."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/runtime-templates", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # No 12-vs-14 divergence: the endpoint serves the whole catalog.
    assert len(body) == len(CATALOG) == 14
    # Declared (insertion) order is preserved so the UI groups by language
    # without a second sort.
    assert [row["id"] for row in body] == list(CATALOG)

    by_id = {row["id"]: row for row in body}

    py = by_id["python-pytest"]
    assert py["dep_cache_mount"] == "/home/agent/.cache/pip"
    assert py["network_policy"] == "none"
    # ES + EN labels are served from the backend (not hardcoded in the front).
    assert py["label"]["es"]
    assert py["label"]["en"]

    # generic-shell has no dep-cache (None) — must round-trip as null, not be
    # dropped (the frontend dep-cache array wrongly omitted it = 12 vs 14).
    shell = by_id["generic-shell"]
    assert shell["dep_cache_mount"] is None

    # generic-http declares a restricted network policy (everything else none).
    http = by_id["generic-http"]
    assert http["network_policy"] == "restricted"


@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_runtime_templates_identical_across_tenants(
    configured_app, migrations_pg_dsn: str
) -> None:
    """The catalog is platform-wide, not tenant-scoped: two tenants get the
    exact same payload and neither can see anything tenant-specific (there is
    nothing tenant-specific to leak)."""
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["user_b"], seeded["tenant_b"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp_a = await client.get(
            "/runtime-templates", headers={"Authorization": f"Bearer {token_a}"}
        )
        resp_b = await client.get(
            "/runtime-templates", headers={"Authorization": f"Bearer {token_b}"}
        )

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert resp_a.json() == resp_b.json()


# ===========================================================================
# default_runtime_template field validation (ProjectCreate / Update)
# ===========================================================================
@pytest.mark.asyncio
async def test_create_project_rejects_unknown_runtime_template(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/projects",
            json=_minimal_project(default_runtime_template="totally-made-up"),
            headers=headers,
        )
    assert resp.status_code == 422, resp.text
    assert "default_runtime_template" in resp.text


@pytest.mark.asyncio
async def test_create_project_accepts_catalog_runtime_template(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/projects",
            json=_minimal_project(default_runtime_template="python-pytest"),
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["default_runtime_template"] == "python-pytest"


@pytest.mark.asyncio
async def test_create_project_allows_null_runtime_template(
    configured_app, migrations_pg_dsn: str
) -> None:
    """None = no default runtime (run_* fall back to per-tool defaults).
    Must stay accepted (backward-compatible)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/projects",
            json=_minimal_project(),  # default_runtime_template omitted
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["default_runtime_template"] is None


@pytest.mark.asyncio
async def test_update_project_rejects_unknown_runtime_template(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/projects",
            json=_minimal_project(name="P"),
            headers=headers,
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]

        bad = await client.put(
            f"/projects/{project_id}",
            json={"default_runtime_template": "nope-not-real"},
            headers=headers,
        )
        assert bad.status_code == 422, bad.text
        assert "default_runtime_template" in bad.text

        good = await client.put(
            f"/projects/{project_id}",
            json={"default_runtime_template": "node-jest"},
            headers=headers,
        )
        assert good.status_code == 200, good.text
        assert good.json()["default_runtime_template"] == "node-jest"
