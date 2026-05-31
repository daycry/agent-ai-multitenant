"""Integration tests for list-endpoint pagination (task_06_14_13).

Audit finding `api-routers-validation-3`: the GET list endpoints
returned every matching row unbounded. The fix adds a uniform
`limit`/`offset` pair (validated `ge`/`le`) plus a deterministic
`order_by` on `/agents`, `/agents/{id}/knowledge-bases`,
`/projects/{id}/tasks`, `/skills`, `/tools` and
`/projects/{id}/plans`.

Coverage:
  - happy path: `?limit=N` returns <= N rows.
  - `?offset` skips leading rows and the two pages don't overlap;
    the union covers the whole set with no gaps/dupes (stable order).
  - default page size is backward-compatible (no `limit` -> up to 100).
  - `?limit` above MAX_PAGE_SIZE -> 422; `?limit=0`, negative, and a
    negative `?offset` -> 422 (clean validation, not a silent clamp).
  - cross-tenant: pagination over tenant A's agents never leaks tenant
    B's private rows regardless of `limit`/`offset` (RLS still applies).

The seeding harness mirrors `test_agents_endpoints.py`: two tenants
seeded via the BYPASSRLS migrations role, JWTs bound to one tenant each.
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

# How many tenant-local agents we seed for tenant A. Chosen above
# DEFAULT_PAGE_SIZE would be wasteful for the test DB, so we keep it
# small and assert against an explicit `?limit` < this number instead.
_SEED_AGENTS_A = 12


async def _seed(dsn: str) -> dict[str, object]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    builtin_agent = uuid4()
    # A private agent for tenant B — must never appear in A's pages.
    secret_b = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agents, projects, user_org_memberships, organizations,"
            " users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Tenant A",
            "tenant-a-page",
            tenant_b,
            "Tenant B",
            "tenant-b-page",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-page",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            user_a,
            "alice@page.test",
            "argon2-placeholder",
            user_b,
            "bob@page.test",
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
        # Seed many tenant-A template agents with monotonically
        # increasing created_at so the deterministic order is testable.
        agent_ids: list[UUID] = []
        for i in range(_SEED_AGENTS_A):
            aid = uuid4()
            agent_ids.append(aid)
            await conn.execute(
                "INSERT INTO agents (id, tenant_id, name, role, system_prompt,"
                " model_config, scope, project_id, created_at)"
                " VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, NULL,"
                " now() + ($8 || ' seconds')::interval)",
                aid,
                tenant_a,
                f"Agent A {i:02d}",
                "backend_dev",
                "prompt",
                "{}",
                "global_tenant_template",
                str(i),
            )
        # One global_builtin (visible to every tenant) + one private
        # tenant-B agent (must stay invisible to A).
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
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt,"
            " model_config, scope, project_id)"
            " VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, NULL)",
            secret_b,
            tenant_b,
            "B Secret Agent",
            "backend_dev",
            "prompt",
            "{}",
            "global_tenant_template",
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "builtin_agent": builtin_agent,
        "secret_b": secret_b,
        "agent_ids": agent_ids,
    }


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


# ---------------------------------------------------------------------------
# Module-constant defaults
# ---------------------------------------------------------------------------
def test_pagination_constants_sane() -> None:
    from api_server.routers._pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

    assert 1 <= DEFAULT_PAGE_SIZE <= MAX_PAGE_SIZE
    assert MAX_PAGE_SIZE == 500


# ---------------------------------------------------------------------------
# Happy path: ?limit caps the page
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_limit_caps_returned_rows(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # Total visible to A = 12 templates + 1 global_builtin = 13.
        full = await client.get("/agents", headers=headers)
        assert full.status_code == 200, full.text
        assert len(full.json()) == _SEED_AGENTS_A + 1

        limited = await client.get("/agents?limit=5", headers=headers)
        assert limited.status_code == 200, limited.text
        assert len(limited.json()) == 5


# ---------------------------------------------------------------------------
# ?offset skips leading rows; pages don't overlap and cover everything
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_offset_pages_are_disjoint_and_complete(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    total = _SEED_AGENTS_A + 1  # + global_builtin

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        page1 = await client.get("/agents?limit=5&offset=0", headers=headers)
        page2 = await client.get("/agents?limit=5&offset=5", headers=headers)
        page3 = await client.get("/agents?limit=5&offset=10", headers=headers)

    assert page1.status_code == page2.status_code == page3.status_code == 200
    ids1 = [a["id"] for a in page1.json()]
    ids2 = [a["id"] for a in page2.json()]
    ids3 = [a["id"] for a in page3.json()]
    assert len(ids1) == 5
    assert len(ids2) == 5
    assert len(ids3) == total - 10  # remainder

    # No overlap across pages.
    assert set(ids1).isdisjoint(ids2)
    assert set(ids1).isdisjoint(ids3)
    assert set(ids2).isdisjoint(ids3)
    # Union covers the whole result set exactly (stable order, no dupes).
    assert len(set(ids1 + ids2 + ids3)) == total


# ---------------------------------------------------------------------------
# offset past the end returns an empty page
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_offset_past_end_is_empty(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/agents?limit=5&offset=9999", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Default page size: no `limit` -> backward-compatible (everything fits
# under DEFAULT_PAGE_SIZE here, so the full set comes back).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_default_is_backward_compatible(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    from api_server.routers._pagination import DEFAULT_PAGE_SIZE

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/agents", headers=headers)
    assert resp.status_code == 200
    # We seeded fewer than the default page size, so the default returns
    # all of them — existing callers see no behaviour change.
    assert len(resp.json()) == _SEED_AGENTS_A + 1
    assert len(resp.json()) <= DEFAULT_PAGE_SIZE


# ---------------------------------------------------------------------------
# Validation: out-of-range limit/offset -> 422
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "limit=501",  # above MAX_PAGE_SIZE
        "limit=0",  # below ge=1
        "limit=-1",  # negative
        "offset=-1",  # below ge=0
        "limit=abc",  # non-int
    ],
)
async def test_out_of_range_params_are_422(
    configured_app, migrations_pg_dsn: str, query: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get(f"/agents?{query}", headers=headers)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_limit_at_max_is_accepted(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # Exactly MAX_PAGE_SIZE must pass (boundary), 501 must fail.
        ok = await client.get("/agents?limit=500", headers=headers)
        bad = await client.get("/agents?limit=501", headers=headers)
    assert ok.status_code == 200, ok.text
    assert bad.status_code == 422


# ---------------------------------------------------------------------------
# Cross-tenant: paging A's agents never leaks B's private rows.
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_pagination_does_not_leak_cross_tenant(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["user_b"], seeded["tenant_b"])
    secret_b_id = str(seeded["secret_b"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # Walk every page of tenant A with a tiny limit; B's private
        # agent must never surface no matter the offset.
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for off in range(0, _SEED_AGENTS_A + 5, 3):
            page = await client.get(
                f"/agents?limit=3&offset={off}",
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert page.status_code == 200, page.text
            for a in page.json():
                seen_ids.add(a["id"])
                seen_names.add(a["name"])

        assert secret_b_id not in seen_ids
        assert "B Secret Agent" not in seen_names
        # Sanity: A saw its own agents + the global built-in.
        assert "Agent A 00" in seen_names
        assert "Built-in PM" in seen_names

        # Tenant B, paginating its own list, sees its secret agent but
        # never tenant A's templates (only its own + the built-in).
        page_b = await client.get(
            "/agents?limit=50",
            headers={"Authorization": f"Bearer {token_b}"},
        )
    assert page_b.status_code == 200
    names_b = {a["name"] for a in page_b.json()}
    assert "B Secret Agent" in names_b
    assert "Built-in PM" in names_b
    assert not any(n.startswith("Agent A ") for n in names_b)
