"""prod-13 · task_prod13_16 — el backfill de `chunks.embedding` NULL.

`workers.backfill_chunk_embeddings` ya existía (P1-11b) pero **sin un solo test
propio**: el plan lo daba por hecho y nadie había comprobado que rellena, que es
idempotente, que respeta las palancas ni —lo que más importa aquí— que no cruza
tenants. Un backfill que escribe el vector del chunk de un tenant sobre la fila
de otro es una fuga de contenido, no una molestia de rendimiento.

Va contra PostgreSQL real (pgvector, `FOR UPDATE SKIP LOCKED` y el JOIN contra
`documents` no se prueban con un doble) e inyecta un `HashEmbedder` determinista,
así que no necesita Ollama.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str, test_redis_url: str):
    """`workers.config.Settings` apuntado a la BD de test (rol BYPASSRLS).

    Redis se apunta también a la BD de test y se vacía: el backfill lee sus tres
    palancas con `get_platform_setting`, que desde task_prod13_21 sirve de una
    caché Redis con TTL. Sin vaciarla, el valor que deja un test se serviría al
    siguiente y el resultado dependería del orden.
    """
    import asyncio

    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings as api_settings
    from workers.config import get_settings, reset_settings_cache

    from tests.integration.conftest import _flush_redis

    api_settings.cache_clear()
    reset_redis_cache()
    reset_settings_cache()
    asyncio.run(_flush_redis(test_redis_url))
    try:
        yield get_settings()
    finally:
        reset_settings_cache()
        reset_redis_cache()
        api_settings.cache_clear()


def _hash_factory(_settings):
    from api_server.ingestion.embeddings import HashEmbedder

    return HashEmbedder()


async def _seed(dsn: str, *, n_null_a: int = 3) -> dict[str, object]:
    """Dos tenants con chunks sin vector + un chunk de un documento borrado."""
    tenant_a, tenant_b = uuid4(), uuid4()
    kb_a, kb_b = uuid4(), uuid4()
    doc_a, doc_b, doc_deleted = uuid4(), uuid4(), uuid4()
    null_ids_a: list[UUID] = []
    already = uuid4()
    orphan = uuid4()
    null_id_b = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE chunks, documents, knowledge_bases CASCADE")
        await conn.execute("TRUNCATE organizations RESTART IDENTITY CASCADE")
        await conn.execute("DELETE FROM platform_settings")
        for tid, slug in ((tenant_a, "chunk-bf-a"), (tenant_b, "chunk-bf-b")):
            await conn.execute(
                "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)", tid, slug, slug
            )
        for kb, tid in ((kb_a, tenant_a), (kb_b, tenant_b)):
            await conn.execute(
                "INSERT INTO knowledge_bases (id, tenant_id, name, embedding_model_id)"
                " VALUES ($1, $2, 'kb', 'nomic-embed-text')",
                kb,
                tid,
            )
        for did, kb, tid, deleted in (
            (doc_a, kb_a, tenant_a, False),
            (doc_b, kb_b, tenant_b, False),
            (doc_deleted, kb_a, tenant_a, True),
        ):
            await conn.execute(
                "INSERT INTO documents (id, tenant_id, kb_id, title, source_filename,"
                " source_mime_type, source_storage_key, source_size_bytes, status, deleted_at)"
                " VALUES ($1, $2, $3, 'd', 'd.pdf', 'application/pdf', 'k', 1, 'indexed',"
                f" {'now()' if deleted else 'NULL'})",
                did,
                tid,
                kb,
            )

        async def _chunk(cid, tid, did, ordinal, content, vector=None):
            await conn.execute(
                "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content, embedding)"
                " VALUES ($1, $2, $3, $4, $5, $6::vector)",
                cid,
                tid,
                did,
                ordinal,
                content,
                vector,
            )

        for i in range(n_null_a):
            cid = uuid4()
            null_ids_a.append(cid)
            await _chunk(cid, tenant_a, doc_a, i, f"tenant A chunk {i}")
        await _chunk(
            already,
            tenant_a,
            doc_a,
            900,
            "ya embebido",
            "[" + ",".join("0.010000" for _ in range(768)) + "]",
        )
        # Chunk de un documento SOFT-DELETED: el backfill no debe tocarlo (su
        # contenido está retirado; re-embeberlo lo devolvería al RAG vectorial).
        await _chunk(orphan, tenant_a, doc_deleted, 0, "documento borrado")
        await _chunk(null_id_b, tenant_b, doc_b, 0, "tenant B chunk")
    finally:
        await conn.close()
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "null_ids_a": null_ids_a,
        "already": already,
        "orphan": orphan,
        "null_id_b": null_id_b,
    }


async def _set_setting(dsn: str, key: str, json_value: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_settings (key, value) VALUES ($1, $2::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key,
            json_value,
        )
    finally:
        await conn.close()


async def _fetch(dsn: str, chunk_id: UUID) -> tuple[str | None, UUID]:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT embedding::text AS emb, tenant_id FROM chunks WHERE id = $1", chunk_id
        )
        return (row["emb"], row["tenant_id"])
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_pass_fills_every_eligible_null_across_tenants(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    from workers.maintenance.chunk_backfill import _backfill_chunk_embeddings_async

    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, n_null_a=3)

    result = await _backfill_chunk_embeddings_async(
        settings=workers_settings, embedder_factory=_hash_factory
    )

    # 3 de A + 1 de B. El del documento borrado NO cuenta.
    assert result["updated"] == 4, result
    for cid in seeded["null_ids_a"]:  # type: ignore[union-attr]
        emb, tid = await _fetch(migrations_pg_dsn, cid)
        assert emb is not None
        assert tid == seeded["tenant_a"], "el UPDATE cruzó el tenant del chunk"
    emb_b, tid_b = await _fetch(migrations_pg_dsn, seeded["null_id_b"])  # type: ignore[arg-type]
    assert emb_b is not None
    assert tid_b == seeded["tenant_b"]


@pytest.mark.asyncio
async def test_a_chunk_of_a_soft_deleted_document_is_never_re_embedded(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """El JOIN contra `documents.deleted_at IS NULL` es lo que impide devolver al
    RAG vectorial el contenido de un documento que el tenant retiró."""
    from workers.maintenance.chunk_backfill import _backfill_chunk_embeddings_async

    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, n_null_a=1)

    await _backfill_chunk_embeddings_async(
        settings=workers_settings, embedder_factory=_hash_factory
    )

    emb, _ = await _fetch(migrations_pg_dsn, seeded["orphan"])  # type: ignore[arg-type]
    assert emb is None, "se embebió un chunk de un documento borrado"


@pytest.mark.asyncio
async def test_the_pass_is_idempotent_and_leaves_existing_vectors_alone(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    from workers.maintenance.chunk_backfill import _backfill_chunk_embeddings_async

    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, n_null_a=2)

    first = await _backfill_chunk_embeddings_async(
        settings=workers_settings, embedder_factory=_hash_factory
    )
    assert first["updated"] == 3  # 2 de A + 1 de B

    second = await _backfill_chunk_embeddings_async(
        settings=workers_settings, embedder_factory=_hash_factory
    )
    assert second["updated"] == 0, second
    assert second["batches"] == 0, second

    emb, _ = await _fetch(migrations_pg_dsn, seeded["already"])  # type: ignore[arg-type]
    assert emb is not None and emb.startswith("[0.01"), "se pisó un vector que ya existía"


@pytest.mark.asyncio
async def test_the_operator_switch_off_makes_the_pass_a_no_op(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """La palanca compartida con el backfill de memorias tiene que morder aquí
    también: si no, apagarla dejaba media plataforma embebiendo igual."""
    from workers.maintenance.chunk_backfill import _backfill_chunk_embeddings_async

    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, n_null_a=2)
    await _set_setting(migrations_pg_dsn, "memory.backfill_enabled", "false")

    result = await _backfill_chunk_embeddings_async(
        settings=workers_settings, embedder_factory=_hash_factory
    )

    assert result == {"updated": 0, "batches": 0, "reason": "disabled"}
    emb, _ = await _fetch(migrations_pg_dsn, seeded["null_ids_a"][0])  # type: ignore[index]
    assert emb is None


@pytest.mark.asyncio
async def test_a_dead_embedder_stops_the_pass_without_killing_the_beat(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """Ollama caído no puede tumbar el beat ni dejar filas a medias."""
    from workers.maintenance.chunk_backfill import _backfill_chunk_embeddings_async

    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, n_null_a=2)

    class _DeadEmbedder:
        async def embed(self, texts):
            raise RuntimeError("ollama unreachable")

        async def aclose(self) -> None:
            return None

    result = await _backfill_chunk_embeddings_async(
        settings=workers_settings, embedder_factory=lambda _s: _DeadEmbedder()
    )

    assert result["updated"] == 0, result
    emb, _ = await _fetch(migrations_pg_dsn, seeded["null_ids_a"][0])  # type: ignore[index]
    assert emb is None


@pytest.mark.asyncio
async def test_the_batch_size_lever_bounds_how_much_one_pass_touches(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """El tamaño de lote es la palanca que evita que una pasada se coma la BD.
    Con lote 1 y 5 chunks pendientes, la pasada hace 5 lotes, no uno."""
    from workers.maintenance.chunk_backfill import _backfill_chunk_embeddings_async

    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _seed(migrations_pg_dsn, n_null_a=4)  # 4 de A + 1 de B = 5
    await _set_setting(migrations_pg_dsn, "memory.backfill_batch_size", "1")

    result = await _backfill_chunk_embeddings_async(
        settings=workers_settings, embedder_factory=_hash_factory
    )

    assert result["updated"] == 5, result
    assert result["batches"] == 5, result
