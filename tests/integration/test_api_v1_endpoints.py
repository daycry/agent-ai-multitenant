"""Integration tests for the public v1 REST endpoints (Plan 13 task_13_05).

The ``/api/v1`` surface is a thin, scope-checked facade over the existing
domain (projects / plans / tasks / conversations / kbs), authenticated by
the Fase A ``X-API-Token`` HEADER (NOT the JWT/session auth) and run under
the token tenant's RLS session. This suite proves:

  * a READ-scope token can list + get its tenant's projects / plans /
    tasks / conversations / kbs;
  * a WRITE-scope token can create where allowed (projects / plans /
    tasks / conversations / kbs);
  * a READ-only token is 403 on writes (valid token, missing capability);
  * no token / a bad token -> 401;
  * over the per-token budget -> 429 (the Fase A limiter applies);
  * cross-tenant (@pytest.mark.cross_tenant): a tenant-A token never sees
    tenant-B's resources and a tenant-A token gets a 404 (never 200/403
    data leak) on a tenant-B resource id.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the fixtures create a throwaway DB and flush Redis DB 15.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.auth.api_tokens import generate_api_token
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

# Small budget so the over-rate path is observable without a long window.
_RATE_BUDGET = 5


# ---------------------------------------------------------------------------
# DB seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _seed_tenant(dsn: str, *, slug: str) -> UUID:
    tenant = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant,
            slug.title(),
            slug,
        )
    finally:
        await conn.close()
    return tenant


async def _seed_token(
    dsn: str,
    *,
    tenant_id: UUID,
    name: str,
    scopes: list[str],
    rate_limit: int = 1000,
) -> tuple[UUID, str]:
    """Seed an ``api_tokens`` row, return ``(token_id, clear_token)``.

    Only the SHA-256 digest is persisted (exactly as the mint endpoint).
    """
    minted = generate_api_token()
    token_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO api_tokens "
            "(id, tenant_id, token_hash, prefix, name, scopes, expires_at, "
            " rate_limit, ip_allowlist, revoked_at) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9::jsonb, $10)",
            token_id,
            tenant_id,
            minted.token_hash,
            minted.prefix,
            name,
            json.dumps(scopes),
            None,
            rate_limit,
            json.dumps([]),
            None,
        )
    finally:
        await conn.close()
    return token_id, minted.token


async def _seed_project(dsn: str, *, tenant_id: UUID, name: str) -> UUID:
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status) VALUES ($1, $2, $3, 'active')",
            project_id,
            tenant_id,
            name,
        )
    finally:
        await conn.close()
    return project_id


async def _seed_plan(dsn: str, *, tenant_id: UUID, project_id: UUID, title: str) -> UUID:
    plan_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, specification) "
            "VALUES ($1, $2, $3, $4, 'draft', '{}'::jsonb)",
            plan_id,
            tenant_id,
            project_id,
            title,
        )
    finally:
        await conn.close()
    return plan_id


async def _seed_task(dsn: str, *, tenant_id: UUID, project_id: UUID, title: str) -> UUID:
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority) "
            "VALUES ($1, $2, $3, $4, 'backlog', 'medium')",
            task_id,
            tenant_id,
            project_id,
            title,
        )
    finally:
        await conn.close()
    return task_id


async def _seed_conversation(dsn: str, *, tenant_id: UUID, project_id: UUID, title: str) -> UUID:
    conv_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO conversations (id, tenant_id, project_id, title, current_mode) "
            "VALUES ($1, $2, $3, $4, 'planning')",
            conv_id,
            tenant_id,
            project_id,
            title,
        )
    finally:
        await conn.close()
    return conv_id


async def _seed_kb(dsn: str, *, tenant_id: UUID, name: str) -> UUID:
    kb_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name, embedding_model_id) "
            "VALUES ($1, $2, $3, 'nomic-embed-text-v1.5')",
            kb_id,
            tenant_id,
            name,
        )
    finally:
        await conn.close()
    return kb_id


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE knowledge_bases, conversations, tasks, plans, projects, "
            "api_tokens, user_org_memberships, organizations, users "
            "RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# App fixture: real api-server with the v1 router mounted
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
    monkeypatch.setenv("API_SERVER_SSO_ENCRYPTION_KEY", "test-sso-encryption-key")
    monkeypatch.setenv("API_SERVER_SSO_REDIRECT_BASE_URL", "http://testserver")
    monkeypatch.delenv("API_SERVER_VAULT_TOKEN", raising=False)

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


def _hdr(token: str) -> dict[str, str]:
    return {"X-API-Token": token}


# ===========================================================================
# Read-scope token: list + get every resource of its tenant
# ===========================================================================
@pytest.mark.asyncio
async def test_read_scope_lists_and_gets_all_resources(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _tid, token = await _seed_token(
        migrations_pg_dsn, tenant_id=tenant, name="reader", scopes=["read"]
    )
    project_id = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    plan_id = await _seed_plan(
        migrations_pg_dsn, tenant_id=tenant, project_id=project_id, title="plan-a"
    )
    task_id = await _seed_task(
        migrations_pg_dsn, tenant_id=tenant, project_id=project_id, title="task-a"
    )
    conv_id = await _seed_conversation(
        migrations_pg_dsn, tenant_id=tenant, project_id=project_id, title="conv-a"
    )
    kb_id = await _seed_kb(migrations_pg_dsn, tenant_id=tenant, name="kb-a")

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Projects
        resp = await client.get("/api/v1/projects", headers=_hdr(token))
        assert resp.status_code == 200, resp.text
        assert [p["id"] for p in resp.json()] == [str(project_id)]
        resp = await client.get(f"/api/v1/projects/{project_id}", headers=_hdr(token))
        assert resp.status_code == 200
        assert resp.json()["name"] == "proj-a"

        # Plans
        resp = await client.get(f"/api/v1/projects/{project_id}/plans", headers=_hdr(token))
        assert resp.status_code == 200
        assert [p["id"] for p in resp.json()] == [str(plan_id)]
        resp = await client.get(f"/api/v1/plans/{plan_id}", headers=_hdr(token))
        assert resp.status_code == 200
        assert resp.json()["title"] == "plan-a"

        # Tasks
        resp = await client.get(f"/api/v1/projects/{project_id}/tasks", headers=_hdr(token))
        assert resp.status_code == 200
        assert [t["id"] for t in resp.json()] == [str(task_id)]
        resp = await client.get(
            f"/api/v1/projects/{project_id}/tasks/{task_id}", headers=_hdr(token)
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "task-a"

        # Conversations
        resp = await client.get(f"/api/v1/projects/{project_id}/conversations", headers=_hdr(token))
        assert resp.status_code == 200
        assert [c["id"] for c in resp.json()] == [str(conv_id)]
        resp = await client.get(f"/api/v1/conversations/{conv_id}", headers=_hdr(token))
        assert resp.status_code == 200
        assert resp.json()["title"] == "conv-a"

        # KBs
        resp = await client.get("/api/v1/kbs", headers=_hdr(token))
        assert resp.status_code == 200
        assert [k["id"] for k in resp.json()] == [str(kb_id)]
        resp = await client.get(f"/api/v1/kbs/{kb_id}", headers=_hdr(token))
        assert resp.status_code == 200
        assert resp.json()["name"] == "kb-a"


# ===========================================================================
# Write-scope token: create where allowed
# ===========================================================================
@pytest.mark.asyncio
async def test_write_scope_can_create(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _tid, token = await _seed_token(
        migrations_pg_dsn, tenant_id=tenant, name="writer", scopes=["read", "write"]
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Create a project.
        resp = await client.post("/api/v1/projects", headers=_hdr(token), json={"name": "p-new"})
        assert resp.status_code == 201, resp.text
        project_id = resp.json()["id"]
        assert resp.json()["tenant_id"] == str(tenant)

        # Create a plan under it.
        resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            headers=_hdr(token),
            json={"title": "pl-new"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["project_id"] == project_id

        # Create a task under it.
        resp = await client.post(
            f"/api/v1/projects/{project_id}/tasks",
            headers=_hdr(token),
            json={"title": "tk-new"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "backlog"

        # Create a conversation under it.
        resp = await client.post(
            f"/api/v1/projects/{project_id}/conversations",
            headers=_hdr(token),
            json={"title": "cv-new"},
        )
        assert resp.status_code == 201, resp.text

        # Create a KB.
        resp = await client.post("/api/v1/kbs", headers=_hdr(token), json={"name": "kb-new"})
        assert resp.status_code == 201, resp.text
        assert resp.json()["name"] == "kb-new"


# ===========================================================================
# Read-only token is 403 on writes
# ===========================================================================
@pytest.mark.asyncio
async def test_read_only_token_403_on_writes(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _tid, token = await _seed_token(
        migrations_pg_dsn, tenant_id=tenant, name="reader", scopes=["read"]
    )
    project_id = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # A read token may read.
        ok = await client.get("/api/v1/projects", headers=_hdr(token))
        assert ok.status_code == 200, ok.text

        # ...but every write is 403 (valid token, missing 'write' scope).
        for method, url, body in (
            ("post", "/api/v1/projects", {"name": "x"}),
            ("post", f"/api/v1/projects/{project_id}/plans", {"title": "x"}),
            ("post", f"/api/v1/projects/{project_id}/tasks", {"title": "x"}),
            ("post", f"/api/v1/projects/{project_id}/conversations", {"title": "x"}),
            ("post", "/api/v1/kbs", {"name": "x"}),
        ):
            resp = await client.request(method, url, headers=_hdr(token), json=body)
            assert resp.status_code == 403, f"{url}: {resp.status_code} {resp.text}"


# ===========================================================================
# No token / bad token -> 401
# ===========================================================================
@pytest.mark.asyncio
async def test_missing_token_401(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/v1/projects")
        assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_bad_token_401(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/v1/projects", headers=_hdr("aapt_deadbeef_nope"))
        assert resp.status_code == 401, resp.text


# ===========================================================================
# Over the per-token budget -> 429 (the Fase A limiter applies)
# ===========================================================================
@pytest.mark.asyncio
async def test_over_rate_returns_429(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _tid, token = await _seed_token(
        migrations_pg_dsn,
        tenant_id=tenant,
        name="rl",
        scopes=["read"],
        rate_limit=_RATE_BUDGET,
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Spend the whole budget.
        for _ in range(_RATE_BUDGET):
            ok = await client.get("/api/v1/projects", headers=_hdr(token))
            assert ok.status_code == 200, ok.text
            assert ok.headers["X-RateLimit-Limit"] == str(_RATE_BUDGET)
        # The next is over budget.
        blocked = await client.get("/api/v1/projects", headers=_hdr(token))
        assert blocked.status_code == 429, blocked.text
        assert blocked.headers["X-RateLimit-Remaining"] == "0"
        assert int(blocked.headers["Retry-After"]) >= 1


# ===========================================================================
# Cross-tenant: a tenant-A token never sees tenant-B's resources
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_a_token_never_sees_tenant_b(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    _tida, token_a = await _seed_token(
        migrations_pg_dsn, tenant_id=tenant_a, name="alpha-tok", scopes=["read", "write"]
    )

    proj_a = await _seed_project(migrations_pg_dsn, tenant_id=tenant_a, name="a-proj")
    proj_b = await _seed_project(migrations_pg_dsn, tenant_id=tenant_b, name="b-proj")
    plan_b = await _seed_plan(
        migrations_pg_dsn, tenant_id=tenant_b, project_id=proj_b, title="b-plan"
    )
    conv_b = await _seed_conversation(
        migrations_pg_dsn, tenant_id=tenant_b, project_id=proj_b, title="b-conv"
    )
    kb_b = await _seed_kb(migrations_pg_dsn, tenant_id=tenant_b, name="b-kb")

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # The list endpoints return ONLY tenant A's rows.
        projects = await client.get("/api/v1/projects", headers=_hdr(token_a))
        assert projects.status_code == 200
        ids = {p["id"] for p in projects.json()}
        assert ids == {str(proj_a)}
        assert str(proj_b) not in ids

        kbs = await client.get("/api/v1/kbs", headers=_hdr(token_a))
        assert kbs.status_code == 200
        assert str(kb_b) not in {k["id"] for k in kbs.json()}

        # Direct access to tenant-B resource ids is a clean 404 (RLS hides
        # the row), never a 200/403 data leak.
        assert (
            await client.get(f"/api/v1/projects/{proj_b}", headers=_hdr(token_a))
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/plans/{plan_b}", headers=_hdr(token_a))
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/conversations/{conv_b}", headers=_hdr(token_a))
        ).status_code == 404
        assert (await client.get(f"/api/v1/kbs/{kb_b}", headers=_hdr(token_a))).status_code == 404
        # Listing plans/tasks/conversations under tenant-B's project id is
        # 404 (the project itself is invisible under tenant-A's RLS).
        assert (
            await client.get(f"/api/v1/projects/{proj_b}/plans", headers=_hdr(token_a))
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/projects/{proj_b}/tasks", headers=_hdr(token_a))
        ).status_code == 404
