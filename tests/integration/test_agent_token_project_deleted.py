"""Soft-deleting a project revokes its agents' tokens (Plan 06.14
task_06_14_09, audit gid auth-rbac-casbin-5).

The agent-token auth (`get_agent_principal` -> `_agent_exists`) used to
validate only that the `Agent` row was live. But agent tokens carry a
24h TTL, far longer than a sandbox container's life, and a
`project_local` agent should lose access the instant its project is
soft-deleted — otherwise a stale token can keep calling
`/internal/agent/*` against a project that no longer exists.

This module exercises the broadened check end-to-end through the
`/internal/agent/_health` smoke endpoint:

  - active project + live agent      -> 200
  - project soft-deleted             -> 403 (the fix)
  - agent soft-deleted (regression)  -> 403
  - both alive after re-activation   -> 200
  - global agent (project_id IS NULL) -> 200 (nothing to validate)
  - cross-tenant: deleting tenant A's project must not revoke tenant
    B's agent, and a token whose tenant doesn't match the agent's is
    still denied.

Reuses the harness shape from `test_internal_agent_auth.py`.

Pre-condition: postgres test container is up (same fixtures as the
other integration tests).
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _truncate(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "TRUNCATE memory_entries, executions, tasks, plans, conversations,"
        " projects, agents, teams, user_org_memberships, organizations,"
        " users RESTART IDENTITY CASCADE"
    )


async def _insert_org(conn: asyncpg.Connection, tenant_id: UUID, slug: str) -> None:
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
        tenant_id,
        slug.title(),
        slug,
    )


async def _insert_project(conn: asyncpg.Connection, project_id: UUID, tenant_id: UUID) -> None:
    await conn.execute(
        "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
        project_id,
        tenant_id,
        "Internal Agent Project",
    )


async def _insert_project_agent(
    conn: asyncpg.Connection, agent_id: UUID, tenant_id: UUID, project_id: UUID
) -> None:
    await conn.execute(
        "INSERT INTO agents"
        " (id, tenant_id, project_id, name, role, system_prompt, memory_scope, scope)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, 'project_local')",
        agent_id,
        tenant_id,
        project_id,
        "Test Agent",
        "backend_dev",
        "You are a test agent.",
        "private",
    )


async def _insert_global_agent(conn: asyncpg.Connection, agent_id: UUID, tenant_id: UUID) -> None:
    """A `global_tenant_template` agent has no project_id (CHECK
    constraint forbids project_id on non-project_local scopes)."""
    await conn.execute(
        "INSERT INTO agents"
        " (id, tenant_id, project_id, name, role, system_prompt, memory_scope, scope)"
        " VALUES ($1, $2, NULL, $3, $4, $5, $6, 'global_tenant_template')",
        agent_id,
        tenant_id,
        "Global Template Agent",
        "backend_dev",
        "You are a global template agent.",
        "private",
    )


async def _seed_project_agent(dsn: str) -> dict[str, UUID]:
    """Seed an org + project + project_local agent. Returns the ids."""
    tenant_id = uuid4()
    project_id = uuid4()
    agent_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await _truncate(conn)
        await _insert_org(conn, _PLATFORM_TENANT_ID, "platform-internal")
        await _insert_org(conn, tenant_id, "tenant-internal")
        await _insert_project(conn, project_id, tenant_id)
        await _insert_project_agent(conn, agent_id, tenant_id, project_id)
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "project_id": project_id, "agent_id": agent_id}


async def _seed_global_agent(dsn: str) -> dict[str, UUID]:
    """Seed an org + a global (project-less) agent."""
    tenant_id = uuid4()
    agent_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await _truncate(conn)
        await _insert_org(conn, _PLATFORM_TENANT_ID, "platform-internal")
        await _insert_org(conn, tenant_id, "tenant-internal")
        await _insert_global_agent(conn, agent_id, tenant_id)
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "agent_id": agent_id}


async def _seed_two_tenants(dsn: str) -> dict[str, dict[str, UUID]]:
    """Two tenants, each with its own project + project_local agent.

    Used for the cross-tenant test: deleting A's project must not
    revoke B's agent.
    """
    a = {"tenant_id": uuid4(), "project_id": uuid4(), "agent_id": uuid4()}
    b = {"tenant_id": uuid4(), "project_id": uuid4(), "agent_id": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await _truncate(conn)
        await _insert_org(conn, _PLATFORM_TENANT_ID, "platform-internal")
        await _insert_org(conn, a["tenant_id"], "tenant-a")
        await _insert_org(conn, b["tenant_id"], "tenant-b")
        await _insert_project(conn, a["project_id"], a["tenant_id"])
        await _insert_project(conn, b["project_id"], b["tenant_id"])
        await _insert_project_agent(conn, a["agent_id"], a["tenant_id"], a["project_id"])
        await _insert_project_agent(conn, b["agent_id"], b["tenant_id"], b["project_id"])
    finally:
        await conn.close()
    return {"a": a, "b": b}


async def _soft_delete_project(dsn: str, project_id: UUID) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE projects SET deleted_at = now() WHERE id = $1", project_id)
    finally:
        await conn.close()


async def _restore_project(dsn: str, project_id: UUID) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE projects SET deleted_at = NULL WHERE id = $1", project_id)
    finally:
        await conn.close()


async def _soft_delete_agent(dsn: str, agent_id: UUID) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE agents SET deleted_at = now() WHERE id = $1", agent_id)
    finally:
        await conn.close()


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """Same scaffolding pattern as `test_internal_agent_auth.py`."""
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


async def _health(app, token: str):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.get(
            "/internal/agent/_health",
            headers={"Authorization": f"Bearer {token}"},
        )


# ---------------------------------------------------------------------------
# Happy path: live project -> allowed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_active_project_agent_allowed(configured_app, migrations_pg_dsn: str) -> None:
    """A `project_local` agent whose project is alive succeeds."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_project_agent(migrations_pg_dsn)
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])

    resp = await _health(configured_app, token)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "status": "ok",
        "agent_id": str(seeded["agent_id"]),
        "tenant_id": str(seeded["tenant_id"]),
    }


# ---------------------------------------------------------------------------
# The fix: project soft-deleted -> denied
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_soft_deleted_project_revokes_agent_token(
    configured_app, migrations_pg_dsn: str
) -> None:
    """The agent row stays live but its project is soft-deleted -> the
    still-signed token is rejected with 403 (audit auth-rbac-casbin-5)."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_project_agent(migrations_pg_dsn)
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])

    await _soft_delete_project(migrations_pg_dsn, seeded["project_id"])

    resp = await _health(configured_app, token)

    assert resp.status_code == 403, resp.text
    assert "agent not found" in resp.text


# ---------------------------------------------------------------------------
# Regression: agent soft-deleted still denied (project alive)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_soft_deleted_agent_still_denied(configured_app, migrations_pg_dsn: str) -> None:
    """Broadening the join must not regress the original agent check."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_project_agent(migrations_pg_dsn)
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])

    await _soft_delete_agent(migrations_pg_dsn, seeded["agent_id"])

    resp = await _health(configured_app, token)

    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Edge: project restored -> token works again
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_restored_project_reallows_agent(configured_app, migrations_pg_dsn: str) -> None:
    """A soft-delete is reversible; if the project is restored
    (deleted_at -> NULL) the same token works again. Guards against an
    accidental hard denial that survives un-deletion."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_project_agent(migrations_pg_dsn)
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])

    await _soft_delete_project(migrations_pg_dsn, seeded["project_id"])
    denied = await _health(configured_app, token)
    assert denied.status_code == 403, denied.text

    await _restore_project(migrations_pg_dsn, seeded["project_id"])
    allowed = await _health(configured_app, token)
    assert allowed.status_code == 200, allowed.text


# ---------------------------------------------------------------------------
# Edge: global agent (no project_id) is unaffected by the new join
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_global_agent_without_project_allowed(configured_app, migrations_pg_dsn: str) -> None:
    """An agent with `project_id IS NULL` (global_tenant_template) has
    no project to validate; the LEFT JOIN must not exclude it."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_global_agent(migrations_pg_dsn)
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])

    resp = await _health(configured_app, token)

    assert resp.status_code == 200, resp.text
    assert resp.json()["agent_id"] == str(seeded["agent_id"])


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_deleting_tenant_a_project_does_not_revoke_tenant_b(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Soft-deleting tenant A's project must revoke A's agent only;
    tenant B's agent (different project, different tenant) keeps
    working. Confirms the project check is scoped per-tenant and does
    not leak across the join."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]

    token_a = mint_agent_token(agent_id=a["agent_id"], tenant_id=a["tenant_id"])
    token_b = mint_agent_token(agent_id=b["agent_id"], tenant_id=b["tenant_id"])

    await _soft_delete_project(migrations_pg_dsn, a["project_id"])

    resp_a = await _health(configured_app, token_a)
    assert resp_a.status_code == 403, resp_a.text

    resp_b = await _health(configured_app, token_b)
    assert resp_b.status_code == 200, resp_b.text
    assert resp_b.json()["agent_id"] == str(b["agent_id"])


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_token_with_wrong_tenant_still_denied(configured_app, migrations_pg_dsn: str) -> None:
    """A token for a real (live) agent + project but a *different*
    tenant_id is denied — the `(agent.id, agent.tenant_id)` pin plus
    the per-tenant project join block cross-tenant impersonation even
    when nothing is soft-deleted."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_project_agent(migrations_pg_dsn)
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=uuid4())

    resp = await _health(configured_app, token)

    assert resp.status_code == 403, resp.text
