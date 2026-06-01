"""Integration tests for `/agents/{id}/tools` (Plan 06.15 task_06_15_01).

Drives the two endpoints end-to-end against the real DB:

  - GET /agents/{id}/tools   tenant_member  (list assignments)
  - PUT /agents/{id}/tools   tenant_admin   (declarative replace)

Plus the scope rules from the plan's Decisiones Clave:

  * set / replace persists; GET reflects it.
  * an empty PUT clears all rows (backward-compatible "no restriction").
  * tenant_admin required for the write (tenant_user -> 403).
  * cannot assign another tenant's custom tool (RLS hides it -> 422).
  * built-in tools are assignable to any agent.
  * MCP tool requires the agent's project to declare that MCP server
    (otherwise 422).
  * global_builtin agent rejects the write (403, fork first).
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed: one tenant + a foreign tenant, admin + plain user, a project with an
# MCP server, a project_local agent + a global_builtin agent, two built-in
# tools, a custom tool in the tenant, a custom tool in the foreign tenant,
# and an MCP tool whose implementation_ref matches the project's server.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant = uuid4()
    foreign_tenant = uuid4()
    admin_user = uuid4()
    plain_user = uuid4()
    project = uuid4()
    local_agent = uuid4()
    builtin_agent = uuid4()
    builtin_tool_a = uuid4()
    builtin_tool_b = uuid4()
    custom_tool = uuid4()
    foreign_custom_tool = uuid4()
    mcp_tool = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agent_tools, tools, agents, projects,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant,
            "Acme",
            "acme-tools",
            foreign_tenant,
            "Beta",
            "beta-tools",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, 'admin@acme.test', 'h'),"
            " ($2, 'user@acme.test', 'h')",
            admin_user,
            plain_user,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'),"
            " ($4, $5, $6, 'tenant_user')",
            uuid4(),
            tenant,
            admin_user,
            uuid4(),
            tenant,
            plain_user,
        )
        # Project with one declared MCP server named "docling".
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, mcp_servers) VALUES"
            " ($1, $2, 'Webapp', CAST($3 AS jsonb))",
            project,
            tenant,
            json.dumps(
                [
                    {
                        "name": "docling",
                        "transport": "stdio",
                        "command": "docling-mcp",
                    }
                ]
            ),
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt, project_id)"
            " VALUES ($1, $2, 'backend-dev', 'backend_dev',"
            "         'project_local', 'ai', 'You are a backend dev.', $3),"
            "        ($4, $5, 'builtin-pm', 'project_manager',"
            "         'global_builtin', 'ai', 'You are a PM.', NULL)",
            local_agent,
            tenant,
            project,
            builtin_agent,
            tenant,
        )
        # Built-in tools (is_builtin=true, owned by the platform tenant in
        # production; tenant column is irrelevant for the read-through, but
        # we use the caller tenant here to keep the seed simple — RLS sees
        # is_builtin rows regardless via the tools_builtin_read policy).
        await conn.execute(
            "INSERT INTO tools"
            " (id, tenant_id, name, description, category,"
            "  implementation_type, security_level, is_builtin)"
            " VALUES"
            " ($1, $2, 'read_file', 'read', 'file', 'builtin', 'safe', true),"
            " ($3, $4, 'git_status', 'git', 'git', 'builtin', 'safe', true)",
            builtin_tool_a,
            tenant,
            builtin_tool_b,
            tenant,
        )
        # Custom tool owned by the caller's tenant.
        await conn.execute(
            "INSERT INTO tools"
            " (id, tenant_id, name, description, category,"
            "  implementation_type, security_level, is_builtin)"
            " VALUES ($1, $2, 'acme_deploy', 'deploy', 'custom',"
            "         'http_endpoint', 'sandboxed', false)",
            custom_tool,
            tenant,
        )
        # Custom tool owned by the FOREIGN tenant (must be unassignable).
        await conn.execute(
            "INSERT INTO tools"
            " (id, tenant_id, name, description, category,"
            "  implementation_type, security_level, is_builtin)"
            " VALUES ($1, $2, 'beta_secret', 'secret', 'custom',"
            "         'python_function', 'privileged', false)",
            foreign_custom_tool,
            foreign_tenant,
        )
        # MCP tool whose implementation_ref namespaces under "docling".
        await conn.execute(
            "INSERT INTO tools"
            " (id, tenant_id, name, description, category,"
            "  implementation_type, implementation_ref, security_level, is_builtin)"
            " VALUES ($1, $2, 'docling_convert', 'convert', 'mcp',"
            "         'mcp_tool', 'docling.convert', 'sandboxed', false)",
            mcp_tool,
            tenant,
        )
    finally:
        await conn.close()
    return {
        "tenant": tenant,
        "foreign_tenant": foreign_tenant,
        "admin_user": admin_user,
        "plain_user": plain_user,
        "project": project,
        "local_agent": local_agent,
        "builtin_agent": builtin_agent,
        "builtin_tool_a": builtin_tool_a,
        "builtin_tool_b": builtin_tool_b,
        "custom_tool": custom_tool,
        "foreign_custom_tool": foreign_custom_tool,
        "mcp_tool": mcp_tool,
    }


# ---------------------------------------------------------------------------
# Fixture (same shape as the agent↔KB grants test)
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


async def _mint(user_id: UUID, tenant_id: UUID | None) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Happy path: set → list → replace → clear
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_set_replace_clear_roundtrip(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Initially empty.
        r = await client.get(f"/agents/{seed['local_agent']}/tools", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json() == []

        # Set two built-in tools, one with a config_override.
        r = await client.put(
            f"/agents/{seed['local_agent']}/tools",
            headers=headers,
            json={
                "tools": [
                    {"tool_id": str(seed["builtin_tool_a"])},
                    {
                        "tool_id": str(seed["builtin_tool_b"]),
                        "config_override": {"verbose": True},
                    },
                ]
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert {row["tool_id"] for row in body} == {
            str(seed["builtin_tool_a"]),
            str(seed["builtin_tool_b"]),
        }
        by_id = {row["tool_id"]: row for row in body}
        assert by_id[str(seed["builtin_tool_b"])]["config_override"] == {"verbose": True}
        assert by_id[str(seed["builtin_tool_a"])]["is_builtin"] is True

        # GET reflects it.
        r = await client.get(f"/agents/{seed['local_agent']}/tools", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) == 2

        # Replace with a single custom tool — old rows gone.
        r = await client.put(
            f"/agents/{seed['local_agent']}/tools",
            headers=headers,
            json={"tools": [{"tool_id": str(seed["custom_tool"])}]},
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["tool_id"] == str(seed["custom_tool"])
        assert rows[0]["is_builtin"] is False
        assert rows[0]["implementation_type"] == "http_endpoint"

        # Clear (empty list) → backward-compatible no-restriction state.
        r = await client.put(
            f"/agents/{seed['local_agent']}/tools",
            headers=headers,
            json={"tools": []},
        )
        assert r.status_code == 200, r.text
        assert r.json() == []

        r = await client.get(f"/agents/{seed['local_agent']}/tools", headers=headers)
        assert r.json() == []


# ---------------------------------------------------------------------------
# Built-in tools assignable to any (project_local) agent.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_builtin_tool_assignable(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.put(
            f"/agents/{seed['local_agent']}/tools",
            headers={"Authorization": f"Bearer {token}"},
            json={"tools": [{"tool_id": str(seed["builtin_tool_a"])}]},
        )
        assert r.status_code == 200, r.text
        assert r.json()[0]["tool_id"] == str(seed["builtin_tool_a"])


# ---------------------------------------------------------------------------
# MCP tool: assignable when the project declares the server.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mcp_tool_assignable_when_project_has_server(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.put(
            f"/agents/{seed['local_agent']}/tools",
            headers={"Authorization": f"Bearer {token}"},
            json={"tools": [{"tool_id": str(seed["mcp_tool"])}]},
        )
        assert r.status_code == 200, r.text
        assert r.json()[0]["tool_id"] == str(seed["mcp_tool"])


# ---------------------------------------------------------------------------
# MCP tool rejected when the project does NOT declare the server.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mcp_tool_rejected_without_project_server(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed(migrations_pg_dsn)
    # Remove the MCP server from the project so the tool no longer matches.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE projects SET mcp_servers = '[]'::jsonb WHERE id = $1",
            seed["project"],
        )
    finally:
        await conn.close()

    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.put(
            f"/agents/{seed['local_agent']}/tools",
            headers={"Authorization": f"Bearer {token}"},
            json={"tools": [{"tool_id": str(seed["mcp_tool"])}]},
        )
        assert r.status_code == 422, r.text
        assert "docling" in r.json()["detail"]


# ---------------------------------------------------------------------------
# tenant_user cannot write (PUT) → 403.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tenant_user_cannot_set(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["plain_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.put(
            f"/agents/{seed['local_agent']}/tools",
            headers={"Authorization": f"Bearer {token}"},
            json={"tools": [{"tool_id": str(seed["builtin_tool_a"])}]},
        )
        assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# tenant_user CAN list (read endpoint is tenant_member).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tenant_user_can_list(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["plain_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/agents/{seed['local_agent']}/tools",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# global_builtin agent rejects the write (fork first) → 403.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_set_on_builtin_agent_is_403(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.put(
            f"/agents/{seed['builtin_agent']}/tools",
            headers={"Authorization": f"Bearer {token}"},
            json={"tools": [{"tool_id": str(seed["builtin_tool_a"])}]},
        )
        assert r.status_code == 403, r.text
        assert "global_builtin" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Duplicate tool_id in the payload → 422 (schema validation).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_tool_id_is_422(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.put(
            f"/agents/{seed['local_agent']}/tools",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "tools": [
                    {"tool_id": str(seed["builtin_tool_a"])},
                    {"tool_id": str(seed["builtin_tool_a"])},
                ]
            },
        )
        assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Cross-tenant custom tool cannot be assigned (RLS hides it → 422).
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cannot_assign_foreign_tenant_custom_tool(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.put(
            f"/agents/{seed['local_agent']}/tools",
            headers={"Authorization": f"Bearer {token}"},
            json={"tools": [{"tool_id": str(seed["foreign_custom_tool"])}]},
        )
        assert r.status_code == 422, r.text
        assert str(seed["foreign_custom_tool"]) in r.json()["detail"]

        # And nothing was persisted (transactional set must not partial-apply).
        r = await client.get(
            f"/agents/{seed['local_agent']}/tools", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.json() == []
