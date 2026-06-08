"""Recall vectorial de memoria + embed en persistencia + re-embed tras merge
(Plan 06.17 task_06_17_03).

Antes de esta tarea ``/internal/agent/memory-recall`` pasaba
``query_embedding=None`` (``internal_agent.py:160``), de modo que
``recall._vector_candidates`` devolvía siempre ``[]`` (``recall.py:181``); y
``memory_entries.embedding`` nacía NULL (``persistence.py``), por lo que
``GET /memories/{id}/similar`` salía vacío (``memories.py:316``). Aquí se
verifica end-to-end contra Postgres real que:

  * ``POST /memory-store`` (interno del agente) embebe el contenido AL CREAR:
    ``has_embedding`` pasa a True (vía la lista del operador) y la fila tiene
    embedding;
  * ``POST /memory-recall`` embebe la query (ya no ``None``) y el path
    vectorial+RRF participa → al menos un hit lleva ``vector_rank`` no nulo;
  * ``GET /memories/{id}/similar`` deja de salir vacío para una memoria creada
    por la UI con embedder (devuelve la hermana cercana);
  * un ``POST /memories/{src}/merge-into`` RE-EMBEBE el destino: su embedding
    cambia (el contenido combinó dos memorias).

El embedder de consulta (``get_query_embedder``, reutilizado de
``docs_viewer.py``) se sobreescribe con un :class:`HashEmbedder` determinista,
el MISMO que embebe el contenido al crear, así que el ranking es reproducible
sin Ollama.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


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
    # El query-embedder (compartido por recall, store y merge) → HashEmbedder
    # determinista, así que store y recall usan EL MISMO embedder y el ranking
    # vectorial es reproducible sin Ollama.
    from api_server.ingestion.embeddings import HashEmbedder
    from api_server.routers.docs_viewer import get_query_embedder

    async def _yield_hash():
        yield HashEmbedder()

    app.dependency_overrides[get_query_embedder] = _yield_hash
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _seed_project_agent(dsn: str) -> dict[str, UUID]:
    """Tenant + project + agente project_shared (para memory-recall del agente)
    + un usuario tenant_member (para los endpoints humanos)."""
    tenant_id = uuid4()
    project_id = uuid4()
    agent_id = uuid4()
    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, projects, agents,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Recall",
            "tenant-recall-vec",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-recall-vec",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@recall-vec.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_member')",
            uuid4(),
            tenant_id,
            user_id,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Recall Vec Project",
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, project_id, name, role, system_prompt, memory_scope, scope)"
            " VALUES ($1, $2, $3, $4, $5, $6, 'project_shared', 'project_local')",
            agent_id,
            tenant_id,
            project_id,
            "Recall Vec Agent",
            "backend_dev",
            "You are a recall vector test agent.",
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "agent_id": agent_id,
        "user_id": user_id,
    }


async def _mint_user_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


async def _post(app: Any, path: str, token: str, body: dict[str, Any]) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})


async def _get(app: Any, path: str, token: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, headers={"Authorization": f"Bearer {token}"})


# ---------------------------------------------------------------------------
# 1. memory-store embebe al crear → has_embedding True; embedding no NULL
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_memory_store_embeds_on_create(configured_app, migrations_pg_dsn: str) -> None:
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_project_agent(migrations_pg_dsn)
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])

    resp = await _post(
        configured_app,
        "/internal/agent/memory-store",
        token,
        {"content": "El proyecto usa asyncpg y pgvector.", "type": "semantic"},
    )
    assert resp.status_code == 201, resp.text
    memory_id = resp.json()["memory_id"]

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        emb = await conn.fetchval(
            "SELECT embedding FROM memory_entries WHERE id = $1", UUID(memory_id)
        )
    finally:
        await conn.close()
    assert emb is not None, "el embedding debería rellenarse al crear (no NULL)"


# ---------------------------------------------------------------------------
# 2. memory-recall embebe la query → el path vectorial participa
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_memory_recall_vector_path_active(configured_app, migrations_pg_dsn: str) -> None:
    """Tras almacenar memorias con embedding, un recall con query idéntica a una
    de ellas hace que el path vectorial devuelva candidatos (``vector_rank`` no
    nulo en al menos un hit)."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_project_agent(migrations_pg_dsn)
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])

    exact = "El RRF mezcla las dos listas ordenadas de recuperación."
    for content in (exact, "Otra memoria sobre endpoints REST internos."):
        store = await _post(
            configured_app,
            "/internal/agent/memory-store",
            token,
            {"content": content, "type": "semantic"},
        )
        assert store.status_code == 201, store.text

    # Query EXACTAMENTE igual a una memoria → HashEmbedder produce el mismo
    # vector → cosine 1.0 → entra por la vía vectorial.
    resp = await _post(
        configured_app,
        "/internal/agent/memory-recall",
        token,
        {"query": exact, "scopes": ["project_shared", "global"], "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    hits = resp.json()["hits"]
    assert len(hits) >= 1
    assert any(h["vector_rank"] is not None for h in hits), hits
    assert any(exact in h["content"] for h in hits), hits


# ---------------------------------------------------------------------------
# 3. /memories/{id}/similar deja de salir vacío
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_similar_not_empty_after_embed_on_create(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Dos memorias 'global' creadas por la UI (con embedder) tienen embedding;
    ``/similar`` de una devuelve la otra (ya no vacío)."""
    seeded = await _seed_project_agent(migrations_pg_dsn)
    token = await _mint_user_token(seeded["user_id"], seeded["tenant_id"])

    # tenant_admin requerido para scope global → usamos project_shared (el user
    # es tenant_member); dos memorias muy parecidas en el mismo proyecto.
    contents = [
        "El equipo prefiere asyncpg sobre psycopg para el acceso async.",
        "El equipo usa asyncpg, no psycopg, en todo el acceso async.",
    ]
    ids: list[str] = []
    for content in contents:
        resp = await _post(
            configured_app,
            "/memories",
            token,
            {
                "content": content,
                "type": "semantic",
                "scope": "project_shared",
                "project_id": str(seeded["project_id"]),
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["has_embedding"] is True, body
        ids.append(body["id"])

    sim = await _get(configured_app, f"/memories/{ids[0]}/similar?threshold=0.0&limit=10", token)
    assert sim.status_code == 200, sim.text
    returned = {item["memory"]["id"] for item in sim.json()}
    assert ids[1] in returned, returned


# ---------------------------------------------------------------------------
# 4. merge re-embebe el destino (su embedding cambia)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_merge_reembeds_target(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed_project_agent(migrations_pg_dsn)
    token = await _mint_user_token(seeded["user_id"], seeded["tenant_id"])

    src_resp = await _post(
        configured_app,
        "/memories",
        token,
        {
            "content": "Fuente: detalle a fusionar sobre el pipeline.",
            "type": "semantic",
            "scope": "project_shared",
            "project_id": str(seeded["project_id"]),
        },
    )
    tgt_resp = await _post(
        configured_app,
        "/memories",
        token,
        {
            "content": "Destino: nota base del proyecto.",
            "type": "semantic",
            "scope": "project_shared",
            "project_id": str(seeded["project_id"]),
        },
    )
    assert src_resp.status_code == 201, src_resp.text
    assert tgt_resp.status_code == 201, tgt_resp.text
    src_id = src_resp.json()["id"]
    tgt_id = tgt_resp.json()["id"]

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        before = await conn.fetchval(
            "SELECT embedding::text FROM memory_entries WHERE id = $1", UUID(tgt_id)
        )
    finally:
        await conn.close()
    assert before is not None  # se embebió al crear

    merge = await _post(
        configured_app, f"/memories/{src_id}/merge-into", token, {"target_id": tgt_id}
    )
    assert merge.status_code == 200, merge.text
    assert merge.json()["has_embedding"] is True

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        after = await conn.fetchval(
            "SELECT embedding::text FROM memory_entries WHERE id = $1", UUID(tgt_id)
        )
    finally:
        await conn.close()
    # El contenido combinó dos memorias → el embedding (HashEmbedder por
    # contenido) DEBE cambiar.
    assert after is not None
    assert after != before, "el destino debería re-embeberse tras el merge"
