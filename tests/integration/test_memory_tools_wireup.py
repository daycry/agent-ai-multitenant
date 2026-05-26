"""End-to-end wire-up of `memory_recall` + `memory_store` tools
(Plan 04.5 task_04_5_03).

The test seeds a tenant + team + project + agent, mints an agent
token, then drives both halves of the round-trip through the live
FastAPI app:

  1. Direct HTTP calls to ``POST /internal/agent/memory-store`` and
     ``POST /internal/agent/memory-recall`` — endpoint contract.
  2. The :class:`MemoryTools` adapter calling the same endpoints via
     :class:`InternalAgentAPI`, with httpx pointed at the ASGI app
     under test — proves the tool surface returns the right
     ``ToolResult``.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from agent_runtime.internal_api import InternalAgentAPI
from agent_runtime.memory_tools import MemoryTools, register_memory_tools
from agent_runtime.tools import ToolRegistry
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
async def _seed(dsn: str, *, memory_scope: str = "team_shared") -> dict[str, UUID]:
    """Seed tenant + team + project + agent. Returns the ids."""
    tenant_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    agent_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, executions, tasks, plans, conversations,"
            " projects, agents, teams, user_org_memberships, organizations,"
            " users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Mem Wire",
            "tenant-mem-wire",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-mem-wire",
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, $3)",
            team_id,
            tenant_id,
            "Team Wire",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, team_id) VALUES ($1, $2, $3, $4)",
            project_id,
            tenant_id,
            "Project Wire",
            team_id,
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, project_id, name, role, system_prompt, memory_scope, scope)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, 'project_local')",
            agent_id,
            tenant_id,
            project_id,
            "Wire Agent",
            "backend_dev",
            "You are a wire test agent.",
            memory_scope,
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "team_id": team_id,
        "project_id": project_id,
        "agent_id": agent_id,
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


# ---------------------------------------------------------------------------
# Endpoint contract (direct HTTP)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_memory_store_persists_and_recall_finds_it(
    configured_app, migrations_pg_dsn: str
) -> None:
    """The happy path. Store a memory, recall it by keyword."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed(migrations_pg_dsn, memory_scope="team_shared")
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        store = await client.post(
            "/internal/agent/memory-store",
            json={
                "content": "asyncpg is the project's only DB driver.",
                "type": "semantic",
                "tags": ["asyncpg"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert store.status_code == 201, store.text
        body = store.json()
        memory_id = UUID(body["memory_id"])
        assert body["scope"] == "team_shared"
        assert body["type"] == "semantic"

        recall_resp = await client.post(
            "/internal/agent/memory-recall",
            json={"query": "asyncpg driver", "limit": 5},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert recall_resp.status_code == 200, recall_resp.text
    hits = recall_resp.json()["hits"]
    assert len(hits) >= 1
    assert any(UUID(h["memory_id"]) == memory_id for h in hits)
    top = next(h for h in hits if UUID(h["memory_id"]) == memory_id)
    assert top["scope"] == "team_shared"
    assert top["type"] == "semantic"
    assert "asyncpg" in top["content"].lower()
    assert top["rrf_score"] > 0


@pytest.mark.asyncio
async def test_memory_store_rejects_scope_above_agent_ceiling(
    configured_app, migrations_pg_dsn: str
) -> None:
    """An agent with memory_scope=team_shared cannot persist a `global`
    memory — that would let it broadcast to every tenant agent."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed(migrations_pg_dsn, memory_scope="team_shared")
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/internal/agent/memory-store",
            json={"content": "global fact", "scope": "global"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403, resp.text
    assert "memory_scope" in resp.text


@pytest.mark.asyncio
async def test_memory_recall_filters_by_default_scope_ladder(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A team_shared agent should see its own team's memory, plus any
    project_shared / global rows, but NOT another team's team_shared
    rows. The endpoint resolves owner pointers from the agent itself
    — the client cannot widen the scope by passing alien team_ids."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed(migrations_pg_dsn, memory_scope="team_shared")

    # Seed two memories: one for this agent's team, one for a different
    # team. The recall must only see the first.
    other_team_id = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, $3)",
            other_team_id,
            seeded["tenant_id"],
            "Team Other",
        )
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, team_id, agent_id, metadata)"
            " VALUES ($1, $2, 'team_shared', 'semantic', $3, $4, $5, '{}'::jsonb)",
            uuid4(),
            seeded["tenant_id"],
            "asyncpg is THIS team's driver",
            seeded["team_id"],
            seeded["agent_id"],
        )
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, team_id, metadata)"
            " VALUES ($1, $2, 'team_shared', 'semantic', $3, $4, '{}'::jsonb)",
            uuid4(),
            seeded["tenant_id"],
            "asyncpg is the OTHER team's driver",
            other_team_id,
        )
    finally:
        await conn.close()

    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/internal/agent/memory-recall",
            json={"query": "asyncpg driver", "limit": 5},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    hits = resp.json()["hits"]
    # The 'THIS team' row must be present, the 'OTHER team' row must not.
    contents = [h["content"] for h in hits]
    assert any("THIS team" in c for c in contents), hits
    assert not any("OTHER team" in c for c in contents), hits


# ---------------------------------------------------------------------------
# Tool adapter (MemoryTools wrapping InternalAgentAPI)
# ---------------------------------------------------------------------------
def _api_with_mock_transport(token: str, handler: callable) -> InternalAgentAPI:
    """Build an InternalAgentAPI whose httpx.Client routes via a
    `MockTransport`. The `handler` receives an `httpx.Request` and
    returns an `httpx.Response` — the test asserts the request shape
    and supplies the canned response.

    Why a mock and not the live ASGI app: sync ``httpx.Client`` can't
    drive an ``ASGITransport`` (that's async-only). The endpoint
    correctness is covered separately by the direct-HTTP tests above;
    here we focus on the adapter wiring (URL, headers, body shape,
    response parsing).
    """
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return InternalAgentAPI(base_url="http://api-server-stub", bearer_token=token, client=client)


def test_tool_adapter_memory_store_calls_endpoint_with_right_shape() -> None:
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["last"] = request
        return httpx.Response(
            201,
            json={
                "memory_id": "11111111-1111-1111-1111-111111111111",
                "scope": "team_shared",
                "type": "semantic",
            },
        )

    api = _api_with_mock_transport("token-abc", handler)
    try:
        tools = MemoryTools(api)
        result = tools.memory_store(
            {
                "content": "sqlalchemy is pinned >=2",
                "type": "semantic",
                "tags": ["sqlalchemy"],
            }
        )
    finally:
        api.close()

    assert result.ok is True, result
    assert result.output["memory_id"] == "11111111-1111-1111-1111-111111111111"
    assert result.output["scope"] == "team_shared"

    req = seen["last"]
    assert req.method == "POST"
    assert req.url.path == "/internal/agent/memory-store"
    assert req.headers["authorization"] == "Bearer token-abc"
    import json as _json

    body = _json.loads(req.content)
    assert body == {
        "content": "sqlalchemy is pinned >=2",
        "type": "semantic",
        "tags": ["sqlalchemy"],
    }


def test_tool_adapter_memory_recall_calls_endpoint_with_right_shape() -> None:
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["last"] = request
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "memory_id": "22222222-2222-2222-2222-222222222222",
                        "content": "asyncpg only",
                        "scope": "team_shared",
                        "type": "semantic",
                        "bm25_rank": 1,
                        "vector_rank": None,
                        "rrf_score": 0.0164,
                    }
                ]
            },
        )

    api = _api_with_mock_transport("token-xyz", handler)
    try:
        tools = MemoryTools(api)
        result = tools.memory_recall({"query": "driver", "scopes": ["team_shared"], "limit": 3})
    finally:
        api.close()

    assert result.ok is True, result
    assert result.output["count"] == 1
    assert result.output["hits"][0]["content"] == "asyncpg only"

    req = seen["last"]
    assert req.method == "POST"
    assert req.url.path == "/internal/agent/memory-recall"
    assert req.headers["authorization"] == "Bearer token-xyz"
    import json as _json

    body = _json.loads(req.content)
    assert body == {"query": "driver", "scopes": ["team_shared"], "limit": 3}


def test_tool_adapter_propagates_http_error_to_toolresult() -> None:
    """A 403 from the api-server folds into a structured ToolResult."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, text='{"detail":"agent memory_scope is team_shared; cannot store..."}'
        )

    api = _api_with_mock_transport("tok", handler)
    try:
        tools = MemoryTools(api)
        result = tools.memory_store({"content": "x", "scope": "global"})
    finally:
        api.close()

    assert result.ok is False
    assert result.output == {"status_code": 403}
    assert "403" in (result.error or "")


def test_register_memory_tools_replaces_placeholders() -> None:
    """register_memory_tools puts callable memory_recall + memory_store
    in the registry, regardless of any prior registration."""
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"hits": []}))
    api = InternalAgentAPI(
        base_url="http://stub",
        bearer_token="tok",
        client=httpx.Client(transport=transport),
    )
    try:
        registry = ToolRegistry()
        registry.register("memory_recall", lambda args: None)  # stale placeholder
        register_memory_tools(registry, api)
        assert "memory_recall" in registry.names()
        assert "memory_store" in registry.names()
        # Sanity-call to prove it isn't the lambda anymore.
        out = registry.call("memory_recall", {"query": "x"})
        assert out.ok is True
    finally:
        api.close()


def test_tool_adapter_validates_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bad arguments produce a structured ToolResult — no HTTP call."""
    api = InternalAgentAPI(base_url="http://test", bearer_token="fake")
    tools = MemoryTools(api)
    try:
        bad_recall = tools.memory_recall({})
        assert bad_recall.ok is False
        assert "query" in (bad_recall.error or "")

        bad_recall_scopes = tools.memory_recall({"query": "x", "scopes": "not-a-list"})
        assert bad_recall_scopes.ok is False
        assert "scopes" in (bad_recall_scopes.error or "")

        bad_store = tools.memory_store({})
        assert bad_store.ok is False
        assert "content" in (bad_store.error or "")

        bad_store_type = tools.memory_store({"content": "x", "type": "bogus"})
        assert bad_store_type.ok is False
        assert "type" in (bad_store_type.error or "")
    finally:
        api.close()


def test_internal_api_from_env_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token in env → InternalAPIConfigError. The worker must
    inject AGENTIC_INTERNAL_TOKEN before launching the sandbox."""
    from agent_runtime.internal_api import InternalAPIConfigError

    monkeypatch.delenv("AGENTIC_INTERNAL_TOKEN", raising=False)
    with pytest.raises(InternalAPIConfigError):
        InternalAgentAPI.from_env({})


def test_internal_api_from_env_builds_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token present + no AGENTIC_API_URL → default base URL."""
    api = InternalAgentAPI.from_env({"AGENTIC_INTERNAL_TOKEN": "tok"})
    try:
        assert api.bearer_token == "tok"
        assert api.base_url.startswith("http")
    finally:
        api.close()
