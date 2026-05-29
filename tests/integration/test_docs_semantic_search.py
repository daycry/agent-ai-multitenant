"""Integration tests for the docs-viewer SEMANTIC search (Plan 07 task_07_14).

Drives ``GET /projects/{id}/docs/semantic-search`` end-to-end through the real
app + Postgres (pgvector) schema. The internal-docs KB chunks are seeded via
``sync_project_docs`` + the deterministic ``HashEmbedder`` (no Ollama), and the
endpoint's query embedder is overridden with the SAME ``HashEmbedder`` so the
ranking is fully reproducible offline:

  * a query whose text equals a chunk's content embeds to the identical vector
    (``HashEmbedder`` is content-addressable) → cosine similarity 1.0 → that
    doc is ranked first with score ~1.0;
  * a project with no synced docs (no embeddings) → empty hit list;
  * cross-tenant isolation: a member of tenant B never gets a hit from tenant
    A's docs, and addressing A's project id as B is a 404
    (``@pytest.mark.cross_tenant``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

# Two docs whose single chunk is a known, stable string. Because
# ``chunk_markdown`` keeps a heading attached to its (here empty) body and
# strips the result, each file collapses to exactly one chunk equal to its
# heading line. Querying with that exact string makes the HashEmbedder produce
# the identical vector → cosine similarity 1.0 for that chunk.
_DOC_ALPHA_RELPATH = "01-overview/alpha.md"
_DOC_BETA_RELPATH = "03-guides/beta.md"
_CHUNK_ALPHA = "# Topic Alpha"
_CHUNK_BETA = "# Topic Beta"


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " memory_entries, plans, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


async def _seed_single_tenant(dsn: str) -> dict[str, UUID]:
    """One tenant with a member + a non-member user, and one project."""
    tenant_id = uuid4()
    member_id = uuid4()
    outsider_id = uuid4()
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant SS",
            "tenant-ss",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-ss",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1, 'member@ss.test', 'h'), ($2, 'outsider@ss.test', 'h')",
            member_id,
            outsider_id,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_user')",
            uuid4(),
            tenant_id,
            member_id,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Project SS",
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "member_id": member_id,
        "outsider_id": outsider_id,
        "project_id": project_id,
    }


async def _seed_two_tenants(dsn: str) -> dict[str, dict[str, UUID]]:
    """Two tenants, each with a member + a project. Cross-tenant denial."""
    a_tenant, a_user, a_project = uuid4(), uuid4(), uuid4()
    b_tenant, b_user, b_project = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug)"
            " VALUES ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            a_tenant,
            "Tenant A",
            "tenant-a-ss",
            b_tenant,
            "Tenant B",
            "tenant-b-ss",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-ss-xt",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1, 'a@ss.xt', 'h'), ($2, 'b@ss.xt', 'h')",
            a_user,
            b_user,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_user'), ($4, $5, $6, 'tenant_user')",
            uuid4(),
            a_tenant,
            a_user,
            uuid4(),
            b_tenant,
            b_user,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3), ($4, $5, $6)",
            a_project,
            a_tenant,
            "Project A",
            b_project,
            b_tenant,
            "Project B",
        )
    finally:
        await conn.close()
    return {
        "a": {"tenant_id": a_tenant, "user_id": a_user, "project_id": a_project},
        "b": {"tenant_id": b_tenant, "user_id": b_user, "project_id": b_project},
    }


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _write_docs_tree(root: Path) -> None:
    """Lay down a tiny canonical ``docs/`` tree under ``root``.

    Each ``.md`` collapses to a single chunk equal to its heading line so a
    query equal to that line embeds (HashEmbedder) to the identical vector.
    """
    docs = root / "docs"
    (docs / "01-overview").mkdir(parents=True)
    (docs / "03-guides").mkdir(parents=True)
    (docs / _DOC_ALPHA_RELPATH).write_text(f"{_CHUNK_ALPHA}\n", encoding="utf-8")
    (docs / _DOC_BETA_RELPATH).write_text(f"{_CHUNK_BETA}\n", encoding="utf-8")


async def _sync_internal_docs(
    admin_database_url: str, *, project_id: UUID, tenant_id: UUID, docs_root: Path
) -> None:
    """Populate the project's internal-docs KB chunks (with real embeddings
    from the deterministic fake) so the vector path has rows to rank."""
    from api_server.docs_structure.kb_sync import sync_project_docs
    from api_server.ingestion.embeddings import HashEmbedder
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(admin_database_url)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await sync_project_docs(
            session,
            project_id=project_id,
            tenant_id=tenant_id,
            docs_root=docs_root,
            embedder=HashEmbedder(),
        )
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# App fixture — overrides the query embedder with the deterministic fake
# ---------------------------------------------------------------------------
@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    tmp_path: Path,
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

    from api_server.ingestion.embeddings import HashEmbedder
    from api_server.main import create_app
    from api_server.routers.docs_viewer import get_query_embedder

    app = create_app()

    # Override the query embedder with the SAME deterministic fake used to seed
    # the chunks → no Ollama, fully reproducible cosine ranking.
    async def _fake_embedder():
        yield HashEmbedder()

    app.dependency_overrides[get_query_embedder] = _fake_embedder
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


# ===========================================================================
# Happy path — relevant doc ranked first with score ~1.0
# ===========================================================================
@pytest.mark.asyncio
async def test_semantic_search_ranks_relevant_doc_first(
    configured_app, migrations_pg_dsn: str, admin_database_url: str, tmp_path: Path
) -> None:
    app = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    proj_root = tmp_path / "proj"
    _write_docs_tree(proj_root)
    await _sync_internal_docs(
        admin_database_url,
        project_id=seeded["project_id"],
        tenant_id=seeded["tenant_id"],
        docs_root=proj_root,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Query == Alpha's chunk content → identical vector → cosine 1.0.
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/semantic-search",
            params={"q": _CHUNK_ALPHA},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["query"] == _CHUNK_ALPHA
        hits = body["hits"]
        assert len(hits) >= 1
        # Ranked 1..n in vector order.
        assert [h["rank"] for h in hits] == list(range(1, len(hits) + 1))

        top = hits[0]
        assert top["relpath"] == _DOC_ALPHA_RELPATH
        assert top["chunk_id"]
        assert top["document_id"]
        assert top["snippet"]
        # Identical-vector match → score ~1.0; never below the runner-up.
        assert top["score"] == pytest.approx(1.0, abs=1e-3)
        if len(hits) > 1:
            assert top["score"] >= hits[1]["score"]

        # Switching the query to Beta's content flips which doc ranks first.
        beta = await client.get(
            f"/projects/{seeded['project_id']}/docs/semantic-search",
            params={"q": _CHUNK_BETA},
            headers=headers,
        )
        assert beta.status_code == 200, beta.text
        assert beta.json()["hits"][0]["relpath"] == _DOC_BETA_RELPATH


# ===========================================================================
# Empty: a project with no synced docs (no embeddings) returns no hits
# ===========================================================================
@pytest.mark.asyncio
async def test_semantic_search_no_embeddings_returns_empty(
    configured_app, migrations_pg_dsn: str
) -> None:
    app = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    # No _sync_internal_docs → the project has no internal-docs chunks at all.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/semantic-search",
            params={"q": _CHUNK_ALPHA},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["hits"] == []


# ===========================================================================
# RBAC — non-member of the tenant is denied (403)
# ===========================================================================
@pytest.mark.asyncio
async def test_semantic_search_non_member_denied(configured_app, migrations_pg_dsn: str) -> None:
    app = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_single_tenant(migrations_pg_dsn)
    # The outsider has NO membership in the tenant → require_tenant_member 403.
    token = await _mint_token(seeded["outsider_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{seeded['project_id']}/docs/semantic-search",
            params={"q": _CHUNK_ALPHA},
            headers=headers,
        )
        assert resp.status_code == 403, resp.text


# ===========================================================================
# RBAC — cross-tenant isolation (@cross_tenant)
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_semantic_search_cross_tenant_isolation(
    configured_app, migrations_pg_dsn: str, admin_database_url: str, tmp_path: Path
) -> None:
    """A member of tenant B cannot semantic-search tenant A's project docs, and
    searching B's own (empty) project never surfaces A's matching chunks."""
    app = configured_app
    await _truncate(migrations_pg_dsn)
    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    token_a = await _mint_token(a["user_id"], a["tenant_id"])
    token_b = await _mint_token(b["user_id"], b["tenant_id"])

    # Tenant A's project gets synced internal docs; tenant B's does not.
    root_a = tmp_path / "a"
    _write_docs_tree(root_a)
    await _sync_internal_docs(
        admin_database_url,
        project_id=a["project_id"],
        tenant_id=a["tenant_id"],
        docs_root=root_a,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Sanity: tenant A's member CAN semantic-search its own docs.
        own = await client.get(
            f"/projects/{a['project_id']}/docs/semantic-search",
            params={"q": _CHUNK_ALPHA},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert own.status_code == 200, own.text
        assert own.json()["hits"][0]["relpath"] == _DOC_ALPHA_RELPATH

        # Tenant B addressing tenant A's project id → RLS hides it → 404.
        foreign = await client.get(
            f"/projects/{a['project_id']}/docs/semantic-search",
            params={"q": _CHUNK_ALPHA},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert foreign.status_code == 404, foreign.text

        # Searching B's OWN project (no synced docs) returns no hits from A,
        # even though A's chunk is an exact-vector match — vector_chunks' KB
        # visibility filter scopes it to B's grants.
        b_search = await client.get(
            f"/projects/{b['project_id']}/docs/semantic-search",
            params={"q": _CHUNK_ALPHA},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert b_search.status_code == 200, b_search.text
        assert b_search.json()["hits"] == []
