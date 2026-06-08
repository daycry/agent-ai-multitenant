"""Back-fill IDEMPOTENTE de embeddings de memoria (Plan 06.17 task_06_17_03).

El worker dedicado ``workers.backfill_memory_embeddings`` rellena la columna
``memory_entries.embedding`` que nace NULL (``persistence.py``). Aquí se verifica
contra Postgres real que:

  * una pasada rellena TODAS las filas NULL elegibles (``embedding IS NOT NULL``
    después) usando un :class:`HashEmbedder` determinista inyectado vía el
    ``embedder_factory`` (no se necesita Ollama);
  * es IDEMPOTENTE: una segunda pasada no vuelve a tocar filas ya rellenadas
    (``updated == 0``) y no rompe;
  * respeta las palancas operator-configurable de ``platform_settings``: con
    ``memory.backfill_enabled=false`` la pasada es un no-op; el batch acota
    cuántas filas se rellenan por lote;
  * las filas soft-deleted nunca se rellenan;
  * no cruza tenants de forma incorrecta (cada UPDATE casa por ``tenant_id``;
    dos tenants se rellenan en la misma pasada sin mezclarse).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str):
    """``workers.config.Settings`` apuntado a la DB de test (rol BYPASSRLS)."""
    sync_dsn = migrations_pg_dsn
    async_dsn = sync_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import reset_settings_cache

    reset_settings_cache()
    from workers.config import get_settings

    yield get_settings()
    reset_settings_cache()


async def _seed(dsn: str, *, n_null: int = 3, with_deleted: bool = False) -> dict[str, object]:
    """Siembra dos tenants con memorias 'global' sin embedding.

    Tenant A: ``n_null`` filas NULL + una ya embebida (para probar
    idempotencia) + (opcional) una soft-deleted. Tenant B: una fila NULL.
    """
    tenant_a = uuid4()
    tenant_b = uuid4()
    null_ids_a: list[UUID] = []
    already_embedded = uuid4()
    deleted_id = uuid4()
    null_id_b = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE memory_entries, organizations RESTART IDENTITY CASCADE")
        # platform_settings sobrevive a la recreación del esquema entre tests del
        # mismo módulo; lo limpiamos para que el flag/batch de un test previo no
        # contamine este (cada test fija explícitamente lo que necesita).
        await conn.execute("DELETE FROM platform_settings")
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Tenant A",
            "tenant-a-bf",
            tenant_b,
            "Tenant B",
            "tenant-b-bf",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-bf",
        )
        for i in range(n_null):
            mid = uuid4()
            null_ids_a.append(mid)
            await conn.execute(
                "INSERT INTO memory_entries"
                " (id, tenant_id, scope, type, content, embedding)"
                " VALUES ($1, $2, 'global', 'semantic', $3, NULL)",
                mid,
                tenant_a,
                f"Tenant A memory number {i}: asyncpg and pgvector.",
            )
        # Una fila YA embebida — el back-fill no debe re-tocarla.
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, embedding)"
            " VALUES ($1, $2, 'global', 'semantic', $3, $4::vector)",
            already_embedded,
            tenant_a,
            "Already embedded memory.",
            "[" + ",".join("0.010000" for _ in range(768)) + "]",
        )
        if with_deleted:
            await conn.execute(
                "INSERT INTO memory_entries"
                " (id, tenant_id, scope, type, content, embedding, deleted_at)"
                " VALUES ($1, $2, 'global', 'semantic', $3, NULL, now())",
                deleted_id,
                tenant_a,
                "Soft-deleted memory.",
            )
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, embedding)"
            " VALUES ($1, $2, 'global', 'semantic', $3, NULL)",
            null_id_b,
            tenant_b,
            "Tenant B memory: REST endpoints.",
        )
    finally:
        await conn.close()
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "null_ids_a": null_ids_a,
        "already_embedded": already_embedded,
        "deleted_id": deleted_id,
        "null_id_b": null_id_b,
    }


async def _set_setting(dsn: str, key: str, json_value: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_settings (key, value)"
            " VALUES ($1, $2::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key,
            json_value,
        )
    finally:
        await conn.close()


async def _count_null(dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM memory_entries" " WHERE embedding IS NULL AND deleted_at IS NULL"
        )
    finally:
        await conn.close()


def _hash_factory(_settings):
    from api_server.ingestion.embeddings import HashEmbedder

    return HashEmbedder()


# ---------------------------------------------------------------------------
# 1. Una pasada rellena todos los NULL elegibles (across tenants)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_backfill_fills_all_null_embeddings(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, n_null=3)

    from workers.maintenance import _backfill_memory_embeddings_async

    result = await _backfill_memory_embeddings_async(
        settings=workers_settings, embedder_factory=_hash_factory
    )

    # 3 (tenant A) + 1 (tenant B) = 4 NULL rellenados; la fila ya embebida no.
    assert result["updated"] == 4, result
    assert await _count_null(migrations_pg_dsn) == 0

    # La fila ya embebida conserva su vector original (no se re-tocó).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        emb = await conn.fetchval(
            "SELECT embedding FROM memory_entries WHERE id = $1", seeded["already_embedded"]
        )
        # Cada componente seguía siendo 0.01 (no el del HashEmbedder).
        assert emb is not None
        assert str(emb).startswith("[0.01")
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 2. Idempotencia — segunda pasada no toca nada
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_backfill_is_idempotent(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _seed(migrations_pg_dsn, n_null=2)

    from workers.maintenance import _backfill_memory_embeddings_async

    first = await _backfill_memory_embeddings_async(
        settings=workers_settings, embedder_factory=_hash_factory
    )
    assert first["updated"] == 3  # 2 (A) + 1 (B)
    assert await _count_null(migrations_pg_dsn) == 0

    second = await _backfill_memory_embeddings_async(
        settings=workers_settings, embedder_factory=_hash_factory
    )
    assert second["updated"] == 0, second
    assert second["batches"] == 0, second


# ---------------------------------------------------------------------------
# 3. Flag operator-configurable OFF ⇒ no-op
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_backfill_respects_disabled_flag(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _seed(migrations_pg_dsn, n_null=2)
    await _set_setting(migrations_pg_dsn, "memory.backfill_enabled", "false")

    from workers.maintenance import _backfill_memory_embeddings_async

    result = await _backfill_memory_embeddings_async(
        settings=workers_settings, embedder_factory=_hash_factory
    )
    assert result["updated"] == 0
    assert result["reason"] == "disabled"
    # Nada se rellenó.
    assert await _count_null(migrations_pg_dsn) == 3  # 2 (A) + 1 (B)


# ---------------------------------------------------------------------------
# 4. Batch size acota el lote pero la pasada converge (varios lotes)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_backfill_batches_until_done(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _seed(migrations_pg_dsn, n_null=5)
    await _set_setting(migrations_pg_dsn, "memory.backfill_batch_size", "2")

    from workers.maintenance import _backfill_memory_embeddings_async

    result = await _backfill_memory_embeddings_async(
        settings=workers_settings, embedder_factory=_hash_factory
    )
    # 5 (A) + 1 (B) = 6 filas, batch=2 ⇒ 3 lotes.
    assert result["updated"] == 6, result
    assert result["batches"] == 3, result
    assert await _count_null(migrations_pg_dsn) == 0


# ---------------------------------------------------------------------------
# 5. Soft-deleted nunca se rellena
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_backfill_skips_soft_deleted(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, n_null=1, with_deleted=True)

    from workers.maintenance import _backfill_memory_embeddings_async

    result = await _backfill_memory_embeddings_async(
        settings=workers_settings, embedder_factory=_hash_factory
    )
    assert result["updated"] == 2  # 1 (A) + 1 (B); la soft-deleted NO

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        deleted_emb = await conn.fetchval(
            "SELECT embedding FROM memory_entries WHERE id = $1", seeded["deleted_id"]
        )
    finally:
        await conn.close()
    assert deleted_emb is None
