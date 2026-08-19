"""El contrato de embeddings de KB, extremo a extremo (ADR 0155, AUD14-03).

Los tests unitarios (`tests/unit/test_kb_embedding_contract*.py`) fijan la
lógica y el cableado con dobles. Esto es lo que NO se puede probar con un doble,
porque vive en PostgreSQL:

  * **regla 5** — el camino vectorial filtra por espacio de embeddings: los
    chunks de una KB sellada con OTRO modelo no compiten con el vector de
    consulta, mientras BM25 los sigue viendo. Un `<=>` entre vectores de dos
    modelos distintos devuelve un número válido y sin sentido: no hay error que
    detectar, sólo recall que se degrada. Por eso se prueba contra pgvector de
    verdad y no contra un `str(stmt)`.
  * **regla 4** — la ingesta se niega antes de embeber cuando el sello de la KB
    no es el modelo activo, y el motivo queda escrito en `documents.status` /
    `error_message`, que es lo que la ficha enseña al operador.
  * y el no-falso-positivo que sostiene todo lo demás: una KB sellada con la
    etiqueta heredada `nomic-embed-text-v1.5` —las 14 de la instalación medida
    el 2026-08-19— se ingiere y se busca con normalidad, porque es el MISMO
    modelo que `nomic-embed-text`.

Sin Ollama: `HashEmbedder` produce vectores deterministas de 768 dims.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.ingestion import DoclingChunk, NullAntivirus, ingest_document
from api_server.ingestion.docling import StaticDoclingClient
from api_server.ingestion.embeddings import HashEmbedder
from api_server.rag.search import bm25_chunks, vector_chunks
from api_server.storage import InMemoryObjectStorage
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

#: El modelo activo que se le pasa al pipeline. Explícito (y no leído del
#: entorno) para que el test diga qué está probando y no dependa de un env var.
_ACTIVE = "nomic-embed-text"
#: La etiqueta heredada sellada en las KBs reales. Mismo modelo que `_ACTIVE`.
_LEGACY = "nomic-embed-text-v1.5"
#: Otro embedder de 768 dims del catálogo: mismo tamaño, otro espacio semántico.
_OTHER = "granite-embedding:278m"

_TEXT_OK = "El planificador reparte tareas del DAG entre los workers."
_TEXT_STALE = "El reconciliador repara los worktrees huérfanos del proyecto."


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(dsn: str) -> dict[str, Any]:
    """Un tenant, un proyecto y DOS KBs concedidas: una con el sello heredado y
    otra sellada con otro modelo. Ambas con un chunk vectorizado."""
    tenant_id, project_id = uuid4(), uuid4()
    kb_ok, kb_stale = uuid4(), uuid4()
    doc_ok, doc_stale = uuid4(), uuid4()
    chunk_ok, chunk_stale = uuid4(), uuid4()

    embedder = HashEmbedder()
    vec_ok, vec_stale = await embedder.embed([_TEXT_OK, _TEXT_STALE])

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
            "Tenant Embeddings",
            "tenant-embeddings",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-embeddings",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Proyecto",
        )
        for kb_id, name, stamp in (
            (kb_ok, "KB sello heredado", _LEGACY),
            (kb_stale, "KB sellada con otro modelo", _OTHER),
        ):
            await conn.execute(
                "INSERT INTO knowledge_bases (id, tenant_id, name, embedding_model_id)"
                " VALUES ($1, $2, $3, $4)",
                kb_id,
                tenant_id,
                name,
                stamp,
            )
            await conn.execute(
                "INSERT INTO kb_projects (kb_id, project_id, tenant_id) VALUES ($1, $2, $3)",
                kb_id,
                project_id,
                tenant_id,
            )
        for doc_id, kb_id, title in (
            (doc_ok, kb_ok, "Doc OK"),
            (doc_stale, kb_stale, "Doc Stale"),
        ):
            await conn.execute(
                "INSERT INTO documents"
                " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
                "  source_storage_key, source_size_bytes, status)"
                " VALUES ($1, $2, $3, $4, 'doc.pdf', 'application/pdf', $5, 10, 'indexed')",
                doc_id,
                tenant_id,
                kb_id,
                title,
                f"kb/{tenant_id}/{kb_id}/{doc_id}/doc.pdf",
            )
        for chunk_id, doc_id, content, vector in (
            (chunk_ok, doc_ok, _TEXT_OK, vec_ok),
            (chunk_stale, doc_stale, _TEXT_STALE, vec_stale),
        ):
            # Mismo formato de literal que usa el resto de la suite (y que
            # `vector_chunks` para la consulta): 6 decimales, sin espacios.
            vec_str = "[" + ",".join(f"{x:.6f}" for x in vector) + "]"
            await conn.execute(
                "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content, embedding)"
                " VALUES ($1, $2, $3, 0, $4, $5::vector)",
                chunk_id,
                tenant_id,
                doc_id,
                content,
                vec_str,
            )
    finally:
        await conn.close()

    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "kb_ok": kb_ok,
        "kb_stale": kb_stale,
        "chunk_ok": chunk_ok,
        "chunk_stale": chunk_stale,
    }


async def _open_tenant_session(app_database_url: str, tenant_id: UUID) -> tuple[Any, Any]:
    engine = create_async_engine(app_database_url)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": str(tenant_id)},
    )
    return engine, session


# ===========================================================================
# Regla 5 — el camino vectorial no mezcla espacios semánticos
# ===========================================================================
@pytest.mark.asyncio
async def test_vector_path_skips_a_kb_sealed_with_another_model(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn)

    # La consulta es EXACTAMENTE el texto del chunk de la KB desfasada, así que
    # su distancia coseno es 0: sin el filtro sería el primer resultado.
    qvec = (await HashEmbedder().embed([_TEXT_STALE]))[0]

    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        vector_ids = await vector_chunks(
            session,
            query_embedding=qvec,
            tenant_id=seeded["tenant_id"],
            project_id=seeded["project_id"],
            limit=10,
            embedding_model=_ACTIVE,
        )
        bm25_ids = await bm25_chunks(
            session,
            query="reconciliador worktrees huérfanos",
            tenant_id=seeded["tenant_id"],
            project_id=seeded["project_id"],
            limit=10,
        )
    finally:
        await session.close()
        await engine.dispose()

    # El chunk de la KB desfasada NO compite por vector…
    assert seeded["chunk_stale"] not in vector_ids
    # …y el de la KB con el sello heredado SÍ (mismo modelo, otra grafía): si
    # esta línea fallara, el filtro estaría dejando fuera a todas las KBs reales.
    assert seeded["chunk_ok"] in vector_ids
    # …pero la KB desfasada no se vuelve invisible: BM25 la sigue recuperando.
    assert seeded["chunk_stale"] in bm25_ids


# ===========================================================================
# Regla 4 — la ingesta se niega antes que mezclar
# ===========================================================================
async def _ingest_into(
    *,
    app_database_url: str,
    dsn: str,
    tenant_id: UUID,
    kb_id: UUID,
) -> tuple[Any, UUID]:
    """Sube un documento `pending` a `kb_id` y corre el pipeline con dobles."""
    document_id = uuid4()
    storage_key = f"kb/{tenant_id}/{kb_id}/{document_id}/nuevo.pdf"
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, status)"
            " VALUES ($1, $2, $3, 'Nuevo', 'nuevo.pdf', 'application/pdf', $4, 10, 'pending')",
            document_id,
            tenant_id,
            kb_id,
            storage_key,
        )
    finally:
        await conn.close()

    storage = InMemoryObjectStorage()
    await storage.put_object(key=storage_key, data=b"bytes", content_type="application/pdf")
    docling = StaticDoclingClient(
        chunks=[DoclingChunk(ordinal=0, content="contenido nuevo", bbox=None, metadata={})]
    )

    engine, session = await _open_tenant_session(app_database_url, tenant_id)
    try:
        result = await ingest_document(
            session,
            document_id=document_id,
            storage=storage,
            antivirus=NullAntivirus(),
            docling=docling,
            embedder=HashEmbedder(),
            redis=None,
            platform_embedding_model=_ACTIVE,
        )
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()
    return result, document_id


@pytest.mark.asyncio
async def test_ingestion_refuses_a_document_for_a_stale_kb(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn)

    result, document_id = await _ingest_into(
        app_database_url=app_database_url,
        dsn=migrations_pg_dsn,
        tenant_id=seeded["tenant_id"],
        kb_id=seeded["kb_stale"],
    )

    assert result.status == "failed"
    assert result.chunks_persisted == 0

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT status, error_message FROM documents WHERE id = $1", document_id
        )
        chunks = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", document_id
        )
    finally:
        await conn.close()

    assert row["status"] == "failed"
    # El motivo llega a la ficha nombrando los dos modelos: sin eso el operador
    # ve un documento rojo y no sabe qué reindexar.
    assert _OTHER in row["error_message"]
    assert _ACTIVE in row["error_message"]
    # Y NO se escribió ni un chunk con vectores del modelo equivocado.
    assert chunks == 0


@pytest.mark.asyncio
async def test_ingestion_accepts_the_legacy_stamped_kb(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """El no-falso-positivo. Si esto fallara, desplegar la guarda dejaría sin
    ingesta a las 14 KBs de la instalación real."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn)

    result, document_id = await _ingest_into(
        app_database_url=app_database_url,
        dsn=migrations_pg_dsn,
        tenant_id=seeded["tenant_id"],
        kb_id=seeded["kb_ok"],
    )

    assert result.status == "indexed"
    assert result.chunks_persisted == 1

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        chunks = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", document_id
        )
    finally:
        await conn.close()
    assert chunks == 1
