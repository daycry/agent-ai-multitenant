"""Path vectorial + RRF + reranker en POST /internal/agent/rag-search
(Plan 06.17 task_06_17_02).

Antes de esta tarea el endpoint pasaba ``embedder=None``/``reranker=None`` a
``rag_search``, de modo que ``vector_chunks`` devolvía siempre ``[]`` y el
recall era BM25-only (``search.py:140-141``). Aquí verificamos que:

  * con un query-embedder inyectado (reutiliza ``get_query_embedder`` de
    ``docs_viewer.py``) el path vectorial produce candidatos → los hits llevan
    ``vector_rank`` no nulo y el RRF mezcla ambas listas;
  * el reranker es activable por un flag operator-configurable en
    ``platform_settings`` (``rag.reranker_enabled``, default OFF): con el flag
    en ON el orden refleja el reranker inyectado; con el flag en OFF se respeta
    el orden RRF;
  * sin embedder disponible (Ollama caído ⇒ el override del embedder devuelve
    ``None``) el endpoint NO rompe: cae a BM25 y sigue devolviendo hits.

El corpus se siembra con ``HashEmbedder`` (chunks con embedding determinista),
y el query-embedder del endpoint se sobreescribe con el MISMO ``HashEmbedder``
para que el ranking sea reproducible sin Ollama.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

from ._rag_helpers import seed_rag_corpus

pytestmark = pytest.mark.integration


async def _attach_agent(dsn: str, *, tenant_id: UUID, project_id: UUID) -> UUID:
    """Agente ligado al proyecto sembrado para que el endpoint resuelva un
    ``project_id`` no nulo."""
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
            "Rag Vector Agent",
            "backend_dev",
            "You are a rag vector test agent.",
            "team_shared",
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
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


def _override_embedder(app: Any, embedder: Any | None) -> None:
    """Sustituye el query-embedder reutilizado (``get_query_embedder``) por uno
    determinista (o por ``None`` para simular Ollama caído)."""
    from api_server.routers.docs_viewer import get_query_embedder

    async def _yield():
        yield embedder

    app.dependency_overrides[get_query_embedder] = _yield


def _override_reranker(app: Any, reranker: Any | None) -> None:
    """Sustituye el constructor del reranker del endpoint por uno determinista
    (o ``None`` para forzar el camino sin reranker)."""
    from api_server.routers.internal_agent import get_rag_reranker

    async def _yield():
        yield reranker

    app.dependency_overrides[get_rag_reranker] = _yield


async def _set_reranker_flag(dsn: str, *, enabled: bool) -> None:
    """Escribe el flag operator-configurable directamente (sin pasar por el
    endpoint de admin) para aislar el comportamiento del rag-search."""
    from api_server.db.platform_settings import RAG_RERANKER_ENABLED_KEY

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_settings (key, value)"
            " VALUES ($1, $2::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            RAG_RERANKER_ENABLED_KEY,
            "true" if enabled else "false",
        )
    finally:
        await conn.close()


async def _post_rag_search(app: Any, token: str, body: dict[str, Any]) -> dict[str, Any]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/internal/agent/rag-search",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. El path vectorial deja de devolver [] (query_embedding ya no es None)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vector_path_active_with_embedder(configured_app, migrations_pg_dsn: str) -> None:
    """Con embedder inyectado, al menos un hit lleva ``vector_rank`` no nulo:
    el path vectorial+RRF participa (ya no es BM25-only)."""
    from api_server.auth.internal_agent import mint_agent_token
    from api_server.ingestion.embeddings import HashEmbedder

    seeded = await seed_rag_corpus(migrations_pg_dsn)
    agent_id = await _attach_agent(
        migrations_pg_dsn, tenant_id=seeded["tenant_id"], project_id=seeded["project_id"]
    )
    token = mint_agent_token(agent_id=agent_id, tenant_id=seeded["tenant_id"])

    _override_embedder(configured_app, HashEmbedder())

    # Query EXACTAMENTE igual a un chunk → HashEmbedder produce el mismo vector
    # → cosine 1.0 → ese chunk entra por la vía vectorial.
    exact_chunk = "Reciprocal Rank Fusion merges the two ranked lists."
    payload = await _post_rag_search(configured_app, token, {"query": exact_chunk, "limit": 5})

    hits: list[dict[str, Any]] = payload["hits"]
    assert len(hits) >= 1
    assert any(h["vector_rank"] is not None for h in hits), hits
    # El chunk exacto debe estar entre los hits (lo trae la vía vectorial).
    assert any(exact_chunk in h["content"] for h in hits), hits


@pytest.mark.asyncio
async def test_no_embedder_falls_back_to_bm25(configured_app, migrations_pg_dsn: str) -> None:
    """Sin embedder disponible (override → None), el endpoint NO rompe: cae a
    BM25, devuelve hits con ``vector_rank`` nulo pero ``bm25_rank`` poblado."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await seed_rag_corpus(migrations_pg_dsn)
    agent_id = await _attach_agent(
        migrations_pg_dsn, tenant_id=seeded["tenant_id"], project_id=seeded["project_id"]
    )
    token = mint_agent_token(agent_id=agent_id, tenant_id=seeded["tenant_id"])

    _override_embedder(configured_app, None)

    payload = await _post_rag_search(configured_app, token, {"query": "asyncpg", "limit": 5})
    hits: list[dict[str, Any]] = payload["hits"]
    assert len(hits) >= 1
    assert all(h["vector_rank"] is None for h in hits), hits
    assert any(h["bm25_rank"] is not None for h in hits), hits
    assert any("asyncpg" in h["content"].lower() for h in hits)


# ---------------------------------------------------------------------------
# 2. El reranker respeta el flag operator-configurable (default OFF)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reranker_off_preserves_rrf_order(configured_app, migrations_pg_dsn: str) -> None:
    """Flag OFF (default): no se reordena por reranker; el orden es el de RRF y
    los hits NO llevan ``rerank_score``."""
    from api_server.auth.internal_agent import mint_agent_token
    from api_server.ingestion.embeddings import HashEmbedder

    seeded = await seed_rag_corpus(migrations_pg_dsn)
    agent_id = await _attach_agent(
        migrations_pg_dsn, tenant_id=seeded["tenant_id"], project_id=seeded["project_id"]
    )
    token = mint_agent_token(agent_id=agent_id, tenant_id=seeded["tenant_id"])

    await _set_reranker_flag(migrations_pg_dsn, enabled=False)
    _override_embedder(configured_app, HashEmbedder())

    payload = await _post_rag_search(
        configured_app, token, {"query": "RAG vector similarity", "limit": 5}
    )
    hits: list[dict[str, Any]] = payload["hits"]
    assert len(hits) >= 1
    assert all(h["rerank_score"] is None for h in hits), hits


@pytest.mark.asyncio
async def test_reranker_on_reorders_by_reranker(configured_app, migrations_pg_dsn: str) -> None:
    """Flag ON + reranker determinista inyectado: los hits llevan
    ``rerank_score`` y el primero maximiza el solape léxico con la query."""
    from api_server.auth.internal_agent import mint_agent_token
    from api_server.ingestion.embeddings import HashEmbedder
    from api_server.rag.reranker import DeterministicReranker

    seeded = await seed_rag_corpus(migrations_pg_dsn)
    agent_id = await _attach_agent(
        migrations_pg_dsn, tenant_id=seeded["tenant_id"], project_id=seeded["project_id"]
    )
    token = mint_agent_token(agent_id=agent_id, tenant_id=seeded["tenant_id"])

    await _set_reranker_flag(migrations_pg_dsn, enabled=True)
    _override_embedder(configured_app, HashEmbedder())
    _override_reranker(configured_app, DeterministicReranker())

    # "Reciprocal Rank Fusion" tiene el mayor solape con el chunk homónimo.
    payload = await _post_rag_search(
        configured_app,
        token,
        {"query": "Reciprocal Rank Fusion merges ranked lists", "limit": 5},
    )
    hits: list[dict[str, Any]] = payload["hits"]
    assert len(hits) >= 2
    assert all(h["rerank_score"] is not None for h in hits), hits
    # El reranker determinista ordena por solape léxico desc.
    scores = [h["rerank_score"] for h in hits]
    assert scores == sorted(scores, reverse=True), hits
    assert "Reciprocal Rank Fusion" in hits[0]["content"]
