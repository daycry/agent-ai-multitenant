"""End-to-end wire-up of `document_convert` + `promote_to_kb` tools
(Plan 04.5 task_04_5_05).

Two layers:

  1. Direct HTTP — drives ``/internal/agent/document-convert`` and
     ``/internal/agent/promote-to-kb`` against a seeded KB + Document.
  2. Tool adapter — :class:`DoclingTools` over a ``MockTransport``
     verifies URL + headers + body shape and the error mapping.

v1 of ``document_convert`` reads chunks already stored in the chunks
table (a fast DB lookup). A full re-parse-from-MinIO mode lands when
chat-file-upload arrives in Plan 07.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from agent_runtime.docling_tools import DoclingTools, register_docling_tools
from agent_runtime.internal_api import InternalAgentAPI
from agent_runtime.tools import ToolRegistry
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    """Seed tenant + project + 2 KBs (both granted) + a Document with
    3 chunks in the source KB. Returns the ids."""
    tenant_id = uuid4()
    project_id = uuid4()
    other_project_id = uuid4()
    source_kb_id = uuid4()
    target_kb_id = uuid4()
    document_id = uuid4()
    agent_id = uuid4()
    chunk_ids = [uuid4(), uuid4(), uuid4()]

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " memory_entries, plans, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Docling Wire",
            "tenant-docling-wire",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-docling-wire",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3), ($4, $5, $6)",
            project_id,
            tenant_id,
            "Project Docling",
            other_project_id,
            tenant_id,
            "Project Ungranted",
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, project_id, name, role, system_prompt, memory_scope, scope)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, 'project_local')",
            agent_id,
            tenant_id,
            project_id,
            "Docling Agent",
            "backend_dev",
            "You are a docling test agent.",
            "team_shared",
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, $3), ($4, $5, $6)",
            source_kb_id,
            tenant_id,
            "Source KB",
            target_kb_id,
            tenant_id,
            "Target KB",
        )
        await conn.execute(
            "INSERT INTO kb_projects (kb_id, project_id, tenant_id)"
            " VALUES ($1, $2, $3), ($4, $5, $6)",
            source_kb_id,
            project_id,
            tenant_id,
            target_kb_id,
            project_id,
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, page_count, status)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'indexed')",
            document_id,
            tenant_id,
            source_kb_id,
            "Architecture Notes",
            "arch.md",
            "text/markdown",
            f"kb/{tenant_id}/{source_kb_id}/{document_id}/arch.md",
            512,
            0,
        )
        chunk_contents = [
            "Multi-tenancy from day one — every table carries tenant_id.",
            "Workers never run user code; sandbox containers do.",
            "Plans are the unit of change; PRs are auto-generated per plan.",
        ]
        for cid, ordinal, content in zip(chunk_ids, range(3), chunk_contents, strict=True):
            await conn.execute(
                "INSERT INTO chunks"
                " (id, tenant_id, document_id, ordinal, content)"
                " VALUES ($1, $2, $3, $4, $5)",
                cid,
                tenant_id,
                document_id,
                ordinal,
                content,
            )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "other_project_id": other_project_id,
        "source_kb_id": source_kb_id,
        "target_kb_id": target_kb_id,
        "document_id": document_id,
        "agent_id": agent_id,
        "chunk_ids": chunk_ids,
    }


async def _attach_agent_for_project(
    dsn: str, *, tenant_id: UUID, project_id: UUID, scope: str = "team_shared"
) -> UUID:
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
            "Docling Agent Alt",
            "backend_dev",
            "you are a test.",
            scope,
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
# Endpoint contract — direct HTTP
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_document_convert_returns_chunks(configured_app, migrations_pg_dsn: str) -> None:
    """The agent fetches the structured chunks of a Document its
    project owns."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed(migrations_pg_dsn)
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/internal/agent/document-convert",
            json={"document_id": str(seeded["document_id"])},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert UUID(body["document_id"]) == seeded["document_id"]
    assert UUID(body["kb_id"]) == seeded["source_kb_id"]
    assert body["title"] == "Architecture Notes"
    assert body["source_mime_type"] == "text/markdown"
    chunks = body["chunks"]
    assert len(chunks) == 3
    assert [c["ordinal"] for c in chunks] == [0, 1, 2]
    assert "Multi-tenancy" in chunks[0]["content"]


@pytest.mark.asyncio
async def test_document_convert_rejects_agent_without_kb_grant(
    configured_app, migrations_pg_dsn: str
) -> None:
    """An agent in a project without KB access gets 403."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed(migrations_pg_dsn)
    agent_id = await _attach_agent_for_project(
        migrations_pg_dsn,
        tenant_id=seeded["tenant_id"],
        project_id=seeded["other_project_id"],
    )
    token = mint_agent_token(agent_id=agent_id, tenant_id=seeded["tenant_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/internal/agent/document-convert",
            json={"document_id": str(seeded["document_id"])},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403, resp.text
    assert "not granted" in resp.text


@pytest.mark.asyncio
async def test_document_convert_returns_404_for_missing_document(
    configured_app, migrations_pg_dsn: str
) -> None:
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed(migrations_pg_dsn)
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/internal/agent/document-convert",
            json={"document_id": str(uuid4())},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_promote_to_kb_duplicates_document_and_chunks(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Source doc + chunks land under target_kb_id as a new Document."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed(migrations_pg_dsn)
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/internal/agent/promote-to-kb",
            json={
                "document_id": str(seeded["document_id"]),
                "target_kb_id": str(seeded["target_kb_id"]),
                "title": "Architecture Notes (promoted)",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    new_doc_id = UUID(body["document_id"])
    assert new_doc_id != seeded["document_id"]
    assert body["chunks_persisted"] == 3

    # Verify the new Document landed in the target KB with the same
    # source_storage_key (we don't re-upload bytes).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT kb_id, title, source_storage_key, status FROM documents WHERE id = $1",
            new_doc_id,
        )
        chunks = await conn.fetch(
            "SELECT ordinal, content FROM chunks WHERE document_id = $1 ORDER BY ordinal",
            new_doc_id,
        )
    finally:
        await conn.close()

    assert row["kb_id"] == seeded["target_kb_id"]
    assert row["title"] == "Architecture Notes (promoted)"
    assert row["status"] == "indexed"
    # Same storage key as the source — bytes aren't duplicated.
    assert row["source_storage_key"].endswith("/arch.md")
    assert len(chunks) == 3
    assert "Multi-tenancy" in chunks[0]["content"]


@pytest.mark.asyncio
async def test_promote_to_kb_rejects_target_without_grant(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Target KB must be granted to the agent's project too."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed(migrations_pg_dsn)
    # Create a KB the agent's project is NOT granted access to.
    rogue_kb_id = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, $3)",
            rogue_kb_id,
            seeded["tenant_id"],
            "Rogue KB",
        )
    finally:
        await conn.close()

    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/internal/agent/promote-to-kb",
            json={
                "document_id": str(seeded["document_id"]),
                "target_kb_id": str(rogue_kb_id),
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403, resp.text
    assert "target KB" in resp.text


# ---------------------------------------------------------------------------
# Tool adapter — MockTransport
# ---------------------------------------------------------------------------
def _api_with_mock_transport(token: str, handler) -> InternalAgentAPI:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return InternalAgentAPI(base_url="http://stub", bearer_token=token, client=client)


def test_tool_adapter_document_convert_shape() -> None:
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["last"] = request
        return httpx.Response(
            200,
            json={
                "document_id": "11111111-1111-1111-1111-111111111111",
                "kb_id": "22222222-2222-2222-2222-222222222222",
                "title": "Doc",
                "source_filename": "doc.md",
                "source_mime_type": "text/markdown",
                "page_count": 0,
                "chunks": [],
            },
        )

    api = _api_with_mock_transport("tok-doc", handler)
    try:
        tools = DoclingTools(api)
        result = tools.document_convert({"document_id": "11111111-1111-1111-1111-111111111111"})
    finally:
        api.close()

    assert result.ok is True
    req = seen["last"]
    assert req.method == "POST"
    assert req.url.path == "/internal/agent/document-convert"
    assert req.headers["authorization"] == "Bearer tok-doc"
    import json as _json

    body = _json.loads(req.content)
    assert body == {"document_id": "11111111-1111-1111-1111-111111111111"}


def test_tool_adapter_promote_to_kb_shape() -> None:
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["last"] = request
        return httpx.Response(
            201,
            json={
                "document_id": "33333333-3333-3333-3333-333333333333",
                "chunks_persisted": 2,
            },
        )

    api = _api_with_mock_transport("tok-promote", handler)
    try:
        tools = DoclingTools(api)
        result = tools.promote_to_kb(
            {
                "document_id": "44444444-4444-4444-4444-444444444444",
                "target_kb_id": "55555555-5555-5555-5555-555555555555",
                "title": "Promoted Doc",
            }
        )
    finally:
        api.close()

    assert result.ok is True
    assert result.output["chunks_persisted"] == 2

    req = seen["last"]
    assert req.method == "POST"
    assert req.url.path == "/internal/agent/promote-to-kb"
    import json as _json

    body = _json.loads(req.content)
    assert body == {
        "document_id": "44444444-4444-4444-4444-444444444444",
        "target_kb_id": "55555555-5555-5555-5555-555555555555",
        "title": "Promoted Doc",
    }


def test_tool_adapter_promote_to_kb_validates_inputs() -> None:
    api = InternalAgentAPI(base_url="http://stub", bearer_token="tok")
    try:
        tools = DoclingTools(api)
        bad_doc = tools.promote_to_kb({})
        assert bad_doc.ok is False
        assert "document_id" in (bad_doc.error or "")

        bad_kb = tools.promote_to_kb({"document_id": "x"})
        assert bad_kb.ok is False
        assert "target_kb_id" in (bad_kb.error or "")

        bad_title = tools.promote_to_kb({"document_id": "x", "target_kb_id": "y", "title": 12})
        assert bad_title.ok is False
        assert "title" in (bad_title.error or "")
    finally:
        api.close()


def test_tool_adapter_document_convert_propagates_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text='{"detail":"document not found"}')

    api = _api_with_mock_transport("tok", handler)
    try:
        tools = DoclingTools(api)
        result = tools.document_convert({"document_id": "abc"})
    finally:
        api.close()

    assert result.ok is False
    assert result.output == {"status_code": 404}


def test_register_docling_tools_adds_both() -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "document_id": "x",
                "kb_id": "y",
                "title": "z",
                "source_filename": "f",
                "source_mime_type": "t",
                "page_count": 0,
                "chunks": [],
            },
        )
    )
    api = InternalAgentAPI(
        base_url="http://stub",
        bearer_token="tok",
        client=httpx.Client(transport=transport),
    )
    try:
        registry = ToolRegistry()
        register_docling_tools(registry, api)
        assert "document_convert" in registry.names()
        assert "promote_to_kb" in registry.names()
    finally:
        api.close()
