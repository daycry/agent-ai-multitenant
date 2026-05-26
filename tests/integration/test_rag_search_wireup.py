"""End-to-end wire-up of the `rag_search` tool (Plan 04.5 task_04_5_04).

1. Direct HTTP: ``POST /internal/agent/rag-search`` — endpoint contract
   (uses the Plan 04 Fase D RAG corpus seeder).
2. Tool adapter: :class:`RagTools` makes the same HTTP call via
   :class:`InternalAgentAPI`, wrapped via :class:`ToolRegistry`.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from agent_runtime.internal_api import InternalAgentAPI
from agent_runtime.rag_tools import RagTools, register_rag_tools
from agent_runtime.tools import ToolRegistry
from alembic import command
from httpx import ASGITransport, AsyncClient

from ._rag_helpers import seed_rag_corpus

pytestmark = pytest.mark.integration


async def _attach_agent(dsn: str, *, tenant_id: UUID, project_id: UUID) -> UUID:
    """Create an agent bound to the seeded project so the rag-search
    endpoint resolves a non-null project_id."""
    agent_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, project_id, name, role, system_prompt, memory_scope, scope)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, 'project_local')",
            agent_id,
            tenant_id,
            project_id,
            "Rag Agent",
            "backend_dev",
            "You are a rag test agent.",
            "team_shared",
        )
    finally:
        await conn.close()
    return agent_id


async def _attach_global_agent(dsn: str, *, tenant_id: UUID) -> UUID:
    """Create an agent NOT bound to any project (scope=global_tenant_template)."""
    agent_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, project_id, name, role, system_prompt, memory_scope, scope)"
            " VALUES ($1, $2, NULL, $3, $4, $5, $6, 'global_tenant_template')",
            agent_id,
            tenant_id,
            "Global Agent",
            "reviewer",
            "You are a global agent.",
            "global",
        )
    finally:
        await conn.close()
    return agent_id


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
async def test_rag_search_returns_relevant_chunks(configured_app, migrations_pg_dsn: str) -> None:
    """Query a seeded KB and assert the agent gets back ranked hits."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await seed_rag_corpus(migrations_pg_dsn)
    agent_id = await _attach_agent(
        migrations_pg_dsn, tenant_id=seeded["tenant_id"], project_id=seeded["project_id"]
    )
    token = mint_agent_token(agent_id=agent_id, tenant_id=seeded["tenant_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/internal/agent/rag-search",
            json={"query": "asyncpg", "limit": 5},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    hits: list[dict[str, Any]] = resp.json()["hits"]
    assert len(hits) >= 1
    contents = [h["content"].lower() for h in hits]
    assert any("asyncpg" in c for c in contents)
    # Shape contract — each hit carries the score channels + KB pointers.
    for hit in hits:
        assert "chunk_id" in hit
        assert "document_id" in hit
        assert "kb_id" in hit
        assert hit["rrf_score"] > 0


@pytest.mark.asyncio
async def test_rag_search_for_agent_without_project_returns_empty(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A global/builtin agent has no project_id; the endpoint returns
    an empty hits list rather than 4xx-ing the tool call."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await seed_rag_corpus(migrations_pg_dsn)
    agent_id = await _attach_global_agent(migrations_pg_dsn, tenant_id=seeded["tenant_id"])
    token = mint_agent_token(agent_id=agent_id, tenant_id=seeded["tenant_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/internal/agent/rag-search",
            json={"query": "asyncpg"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"hits": []}


@pytest.mark.asyncio
async def test_rag_search_does_not_leak_ungranted_kb(
    configured_app, migrations_pg_dsn: str
) -> None:
    """An agent in a project without KB grants sees no hits."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await seed_rag_corpus(migrations_pg_dsn)
    # Bind the agent to the OTHER project (no kb_projects grant).
    agent_id = await _attach_agent(
        migrations_pg_dsn,
        tenant_id=seeded["tenant_id"],
        project_id=seeded["other_project_id"],
    )
    token = mint_agent_token(agent_id=agent_id, tenant_id=seeded["tenant_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/internal/agent/rag-search",
            json={"query": "asyncpg"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"hits": []}


# ---------------------------------------------------------------------------
# Tool adapter — MockTransport
# ---------------------------------------------------------------------------
def _api_with_mock_transport(token: str, handler) -> InternalAgentAPI:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return InternalAgentAPI(base_url="http://stub", bearer_token=token, client=client)


def test_tool_adapter_rag_search_calls_endpoint_with_right_shape() -> None:
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["last"] = request
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "chunk_id": "33333333-3333-3333-3333-333333333333",
                        "document_id": "44444444-4444-4444-4444-444444444444",
                        "kb_id": "55555555-5555-5555-5555-555555555555",
                        "content": "asyncpg is the driver",
                        "ordinal": 0,
                        "bbox": None,
                        "bm25_rank": 1,
                        "vector_rank": None,
                        "rrf_score": 0.0164,
                        "rerank_score": None,
                    }
                ]
            },
        )

    api = _api_with_mock_transport("token-rag", handler)
    try:
        tools = RagTools(api)
        result = tools.rag_search({"query": "driver", "limit": 3, "recall_k": 15})
    finally:
        api.close()

    assert result.ok is True, result
    assert result.output["count"] == 1
    assert result.output["hits"][0]["content"] == "asyncpg is the driver"

    req = seen["last"]
    assert req.method == "POST"
    assert req.url.path == "/internal/agent/rag-search"
    assert req.headers["authorization"] == "Bearer token-rag"
    import json as _json

    body = _json.loads(req.content)
    assert body == {"query": "driver", "limit": 3, "recall_k": 15}


def test_tool_adapter_rag_search_validates_inputs() -> None:
    api = InternalAgentAPI(base_url="http://stub", bearer_token="tok")
    try:
        tools = RagTools(api)
        bad = tools.rag_search({})
        assert bad.ok is False
        assert "query" in (bad.error or "")

        bad_limit = tools.rag_search({"query": "x", "limit": "many"})
        assert bad_limit.ok is False
        assert "limit" in (bad_limit.error or "") or "int" in (bad_limit.error or "")
    finally:
        api.close()


def test_tool_adapter_rag_search_propagates_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text='{"detail":"backend exploded"}')

    api = _api_with_mock_transport("tok", handler)
    try:
        tools = RagTools(api)
        result = tools.rag_search({"query": "x"})
    finally:
        api.close()

    assert result.ok is False
    assert result.output == {"status_code": 500}


def test_register_rag_tools_adds_rag_search() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"hits": []}))
    api = InternalAgentAPI(
        base_url="http://stub",
        bearer_token="tok",
        client=httpx.Client(transport=transport),
    )
    try:
        registry = ToolRegistry()
        register_rag_tools(registry, api)
        assert "rag_search" in registry.names()
        # Smoke call — proves the registered function is callable.
        out = registry.call("rag_search", {"query": "x"})
        assert out.ok is True
        assert out.output == {"hits": [], "count": 0}
    finally:
        api.close()
