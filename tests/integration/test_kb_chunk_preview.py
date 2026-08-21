"""Guard de re-embedding + preview/búsqueda de chunks de una KB
(Plan 06.17 task_06_17_05).

Dos contratos que cierran la honestidad de SABER en la API de KBs:

  1. **``PUT /knowledge-bases/{id}`` y el sello de embeddings** (ADR 0155).
     La plataforma indexa con UN modelo, así que el campo no es un selector:

       * pedir un modelo que no es el de la plataforma → **422**, tenga o no
         chunks la KB. Antes esto devolvía 200 sobre una KB vacía y guardaba
         tan ricamente `text-embedding-3-small`, un modelo de OpenAI que este
         stack —Ollama, 768 dims— no puede ni ejecutar;
       * pedir el modelo activo sobre una KB sellada con otro y CON chunks →
         **409**: re-sellar sin re-embeber convertiría el sello en mentira y
         el camino vectorial seguiría sin casar;
       * lo mismo sobre una KB vacía → 200, no hay nada que invalidar.

     El resto del PUT (name/description/category) sigue funcionando.

  2. **``GET /knowledge-bases/{id}/search?q=...``** devuelve los chunks
     de la KB que casan la query (BM25 + vector opcional), tenant-scoped
     vía RLS y acotado al ``kb_id``. Es el preview que la UI usa para
     verificar qué indexó.

  3. **Cross-tenant**: el tenant B nunca ve/busca chunks del tenant A
     (404 en GET search de una KB ajena).
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


# ---------------------------------------------------------------------------
# App fixture
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
    from api_server.ingestion.embeddings import HashEmbedder
    from api_server.routers.docs_viewer import get_query_embedder

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()

    # El embedder real (Ollama) no está disponible en CI; usamos el fake
    # determinista para el path vectorial del search.
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


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------
async def _seed_single(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " memory_entries, plans, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Tenant KB', 'tenant-kb-prev'),"
            " ($2, 'Platform', 'platform-kb-prev')",
            tenant_id,
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'alice@prev.test', 'h')",
            user_id,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_id,
            user_id,
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "user_id": user_id}


async def _seed_kb_with_chunks(
    dsn: str,
    *,
    tenant_id: UUID,
    kb_name: str,
    chunks: list[str],
    embedding_model_id: str = "nomic-embed-text-v1.5",
) -> dict[str, UUID]:
    """Siembra una KB con documento y chunks.

    El sello por defecto es la etiqueta heredada porque es lo que hay en las
    KBs reales; los tests del ADR 0155 lo sobreescriben para fabricar una KB
    «sellada con otro modelo» sin depender del entorno."""
    kb_id = uuid4()
    document_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name, embedding_model_id)"
            " VALUES ($1, $2, $3, $4)",
            kb_id,
            tenant_id,
            kb_name,
            embedding_model_id,
        )
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, status)"
            " VALUES ($1, $2, $3, 'Doc', 'doc.pdf', 'application/pdf', $4, 10, 'indexed')",
            document_id,
            tenant_id,
            kb_id,
            f"kb/{tenant_id}/{kb_id}/{document_id}/doc.pdf",
        )
        for ordinal, content in enumerate(chunks):
            await conn.execute(
                "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content)"
                " VALUES ($1, $2, $3, $4, $5)",
                uuid4(),
                tenant_id,
                document_id,
                ordinal,
                content,
            )
    finally:
        await conn.close()
    return {"kb_id": kb_id, "document_id": document_id}


async def _seed_two_tenants(dsn: str) -> dict[str, dict[str, UUID]]:
    a_tenant, a_user = uuid4(), uuid4()
    b_tenant, b_user = uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " memory_entries, plans, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug)"
            " VALUES ($1, 'Tenant A', 'tenant-a-prev'), ($2, 'Tenant B', 'tenant-b-prev'),"
            " ($3, 'Platform', 'platform-prev-xt')",
            a_tenant,
            b_tenant,
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1, 'a@prev.xt', 'h'), ($2, 'b@prev.xt', 'h')",
            a_user,
            b_user,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
            uuid4(),
            a_tenant,
            a_user,
            uuid4(),
            b_tenant,
            b_user,
        )
    finally:
        await conn.close()
    return {
        "a": {"tenant_id": a_tenant, "user_id": a_user},
        "b": {"tenant_id": b_tenant, "user_id": b_user},
    }


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


# ===========================================================================
# 1. PUT embedding_model_id guard (ADR 0155)
# ===========================================================================
@pytest.mark.asyncio
async def test_put_rejects_a_model_the_platform_does_not_use(
    configured_app, migrations_pg_dsn: str
) -> None:
    app = configured_app
    seeded = await _seed_single(migrations_pg_dsn)
    kb = await _seed_kb_with_chunks(
        migrations_pg_dsn,
        tenant_id=seeded["tenant_id"],
        kb_name="KB With Chunks",
        chunks=["arquitectura del sistema"],
    )
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Un modelo que esta plataforma no ejecuta → 422, no un guardado feliz.
        resp = await client.put(
            f"/knowledge-bases/{kb['kb_id']}",
            json={"embedding_model_id": "text-embedding-3-small"},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text

        # El resto del PUT (description) sigue funcionando aunque haya chunks.
        ok = await client.put(
            f"/knowledge-bases/{kb['kb_id']}",
            json={"description": "renombrada"},
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["description"] == "renombrada"
        # El sello NO cambió, y se devuelve canonizado (lo GUARDADO sigue siendo
        # la etiqueta heredada hasta que corra la migración de datos).
        assert ok.json()["embedding_model_id"] == "nomic-embed-text"
        assert ok.json()["embedding_model_stale"] is False


@pytest.mark.asyncio
async def test_put_restamping_a_stale_kb_with_chunks_is_409(
    configured_app, migrations_pg_dsn: str
) -> None:
    app = configured_app
    seeded = await _seed_single(migrations_pg_dsn)
    kb = await _seed_kb_with_chunks(
        migrations_pg_dsn,
        tenant_id=seeded["tenant_id"],
        kb_name="KB Sellada Con Otro",
        chunks=["arquitectura del sistema"],
        embedding_model_id="granite-embedding:278m",
    )
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # La KB se ve como desfasada: no calla que sus vectores son de otro
        # espacio semántico.
        got = await client.get(f"/knowledge-bases/{kb['kb_id']}", headers=headers)
        assert got.status_code == 200, got.text
        assert got.json()["embedding_model_id"] == "granite-embedding:278m"
        assert got.json()["embedding_model_stale"] is True

        # Y re-sellarla al modelo activo sin re-embeber es 409: sería cambiar la
        # etiqueta sin cambiar los vectores, o sea, otra mentira.
        resp = await client.put(
            f"/knowledge-bases/{kb['kb_id']}",
            json={"embedding_model_id": got.json()["platform_embedding_model"]},
            headers=headers,
        )
        assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_put_restamping_an_empty_kb_is_allowed(
    configured_app, migrations_pg_dsn: str
) -> None:
    app = configured_app
    seeded = await _seed_single(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/knowledge-bases", json={"name": "KB Empty"}, headers=headers)
        assert created.status_code == 201, created.text
        active = created.json()["platform_embedding_model"]
        kb_id = created.json()["id"]

        # Re-sellar con el modelo activo: sin chunks no hay nada que invalidar.
        resp = await client.put(
            f"/knowledge-bases/{kb_id}",
            json={"embedding_model_id": active},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["embedding_model_id"] == active
        assert resp.json()["embedding_model_stale"] is False


# ===========================================================================
# 2. GET /knowledge-bases/{id}/search
# ===========================================================================
@pytest.mark.asyncio
async def test_kb_search_returns_matching_chunks(configured_app, migrations_pg_dsn: str) -> None:
    app = configured_app
    seeded = await _seed_single(migrations_pg_dsn)
    kb = await _seed_kb_with_chunks(
        migrations_pg_dsn,
        tenant_id=seeded["tenant_id"],
        kb_name="KB Searchable",
        chunks=[
            "La arquitectura del sistema usa PostgreSQL y Redis.",
            "El despliegue es Docker Compose en una sola maquina.",
        ],
    )
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/knowledge-bases/{kb['kb_id']}/search",
            params={"q": "arquitectura"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        results = resp.json()
        assert isinstance(results, list)
        assert len(results) >= 1
        contents = [r["content"] for r in results]
        assert any("arquitectura" in c.lower() for c in contents)
        # Cada hit identifica su chunk/documento.
        assert all("chunk_id" in r and "document_id" in r for r in results)


@pytest.mark.asyncio
async def test_kb_search_blank_query_returns_empty(configured_app, migrations_pg_dsn: str) -> None:
    app = configured_app
    seeded = await _seed_single(migrations_pg_dsn)
    kb = await _seed_kb_with_chunks(
        migrations_pg_dsn,
        tenant_id=seeded["tenant_id"],
        kb_name="KB Blank",
        chunks=["contenido"],
    )
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/knowledge-bases/{kb['kb_id']}/search",
            params={"q": "   "},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == []


@pytest.mark.asyncio
async def test_kb_search_404_for_missing_kb(configured_app, migrations_pg_dsn: str) -> None:
    app = configured_app
    seeded = await _seed_single(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/knowledge-bases/{uuid4()}/search",
            params={"q": "x"},
            headers=headers,
        )
        assert resp.status_code == 404, resp.text


# ===========================================================================
# 3. Cross-tenant: B no busca chunks de A
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_b_cannot_search_tenant_a_kb(configured_app, migrations_pg_dsn: str) -> None:
    app = configured_app
    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    kb = await _seed_kb_with_chunks(
        migrations_pg_dsn,
        tenant_id=a["tenant_id"],
        kb_name="A Secret KB",
        chunks=["secreto de la arquitectura del tenant A"],
    )
    token_a = await _mint_token(a["user_id"], a["tenant_id"])
    token_b = await _mint_token(b["user_id"], b["tenant_id"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # A sí ve sus chunks.
        a_resp = await client.get(
            f"/knowledge-bases/{kb['kb_id']}/search",
            params={"q": "arquitectura"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert a_resp.status_code == 200, a_resp.text
        assert len(a_resp.json()) >= 1

        # B no ve la KB (RLS la oculta) → 404, nunca filtra contenido.
        b_resp = await client.get(
            f"/knowledge-bases/{kb['kb_id']}/search",
            params={"q": "arquitectura"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert b_resp.status_code == 404, b_resp.text
