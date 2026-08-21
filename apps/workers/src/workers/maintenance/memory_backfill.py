"""Memory-embedding back-fill — `workers.backfill_memory_embeddings` (Plan 06.17
task_06_17_03). Best-effort: a single failure must not crash beat itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine

_log = structlog.get_logger("workers.maintenance")

# Tope duro de lotes por ejecución del back-fill — defensa contra un bucle
# infinito si el embedder devolviese siempre vectores inválidos (las filas
# seguirían NULL y el SELECT las re-encontraría). Con embedder sano el back-fill
# converge mucho antes (cada lote rellena sus filas y el siguiente ya no las ve).
_BACKFILL_MAX_BATCHES_PER_RUN = 10_000

# Tipo del factory de embedder que los tests sobreescriben (inyectan un
# HashEmbedder determinista para no depender de Ollama). El embedder concreto
# vive en ``api_server.ingestion.embeddings`` (un paquete hermano que el hook de
# mypy de pre-commit NO ve), así que aquí el tipo es ``Any``: lo único que se le
# pide es ``await .embed([...])`` / ``await .aclose()``, lo importamos lazy en
# ``_default_embedder_factory``.
EmbedderFactory = Callable[[Settings], Any]


def _default_embedder_factory(settings: Settings) -> Any:
    """Embedder por defecto del back-fill: el ``OllamaEmbedder`` de la ingesta
    de KBs (mismo modelo / dimensión), apuntado a la URL de Ollama del worker.

    Import perezoso: solo los workers que ejecutan el back-fill pagan el coste
    de importar ``api_server.ingestion.embeddings``."""
    from api_server.ingestion.embeddings import OllamaEmbedder

    return OllamaEmbedder(base_url=settings.memory_embedder_base_url)


@app.task(name="workers.backfill_memory_embeddings")  # type: ignore[untyped-decorator]
def backfill_memory_embeddings() -> dict[str, Any]:
    """Rellena los ``memory_entries.embedding`` NULL — IDEMPOTENTE, por lotes y
    throttled (Plan 06.17 task_06_17_03).

    Worker DEDICADO: nunca forma parte del flujo de un run (sin auto-retry). Una
    pasada solo toca filas con ``embedding IS NULL`` y deja el resto intacto, así
    que re-ejecutarlo es seguro y convergente. Las palancas (enabled / batch /
    throttle) son PLATFORM settings que un System Admin posee y que se leen en
    vivo al inicio de cada pasada.
    """
    settings = get_settings()
    return asyncio.run(
        _backfill_memory_embeddings_async(
            settings=settings,
            embedder_factory=_default_embedder_factory,
        )
    )


async def _backfill_memory_embeddings_async(
    *,
    settings: Settings,
    embedder_factory: EmbedderFactory,
) -> dict[str, Any]:
    """Núcleo async del back-fill. Los tests inyectan ``embedder_factory`` para
    usar un :class:`HashEmbedder` determinista sin Ollama.

    Recorre TODOS los tenants (rol BYPASSRLS, como el Memorizer): por eso no fija
    ``app.tenant_id``. La columna ``tenant_id`` viaja igualmente en cada UPDATE
    como defensa en profundidad. Devuelve un dict con cuántas filas se
    rellenaron y en cuántos lotes."""
    from api_server.db.platform_settings import (
        get_memory_backfill_batch_size,
        get_memory_backfill_enabled,
        get_memory_backfill_throttle_ms,
    )

    engine = worker_engine(settings)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    updated = 0
    batches = 0
    try:
        async with sessionmaker() as session:
            enabled = await get_memory_backfill_enabled(session)
            batch_size = await get_memory_backfill_batch_size(session)
            throttle_ms = await get_memory_backfill_throttle_ms(session)
        if not enabled:
            _log.info("maintenance.backfill_memory_embeddings.disabled")
            return {"updated": 0, "batches": 0, "reason": "disabled"}

        embedder = embedder_factory(settings)
        try:
            while batches < _BACKFILL_MAX_BATCHES_PER_RUN:
                async with sessionmaker() as session, session.begin():
                    rows = (
                        await session.execute(
                            sa_text(
                                "SELECT id, tenant_id, content FROM memory_entries"
                                " WHERE embedding IS NULL AND deleted_at IS NULL"
                                " ORDER BY created_at"
                                " LIMIT :limit"
                                " FOR UPDATE SKIP LOCKED"
                            ),
                            {"limit": batch_size},
                        )
                    ).all()
                    if not rows:
                        break
                    batches += 1

                    contents = [r.content for r in rows]
                    try:
                        vectors = await embedder.embed(contents)
                    except Exception as exc:  # EmbeddingError u otro fallo de red
                        _log.warning(
                            "maintenance.backfill_memory_embeddings.embed_failed",
                            error=str(exc),
                            count=len(contents),
                        )
                        # No marcamos nada: el commit del bloque libera el lock y
                        # estas filas se reintentan en la PRÓXIMA pasada del beat
                        # (idempotente, sin auto-retry dentro del run).
                        break
                    if len(vectors) != len(rows):
                        _log.warning(
                            "maintenance.backfill_memory_embeddings.count_mismatch",
                            expected=len(rows),
                            got=len(vectors),
                        )
                        break

                    for row, vec in zip(rows, vectors, strict=True):
                        qvec = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
                        await session.execute(
                            sa_text(
                                "UPDATE memory_entries"
                                " SET embedding = CAST(:qvec AS vector)"
                                " WHERE id = :id AND tenant_id = :tenant_id"
                            ),
                            {"qvec": qvec, "id": row.id, "tenant_id": row.tenant_id},
                        )
                        updated += 1

                if throttle_ms > 0:
                    await asyncio.sleep(throttle_ms / 1000.0)
        finally:
            await embedder.aclose()
    except Exception as exc:  # pragma: no cover — defensive logging
        _log.warning("maintenance.backfill_memory_embeddings.error", error=str(exc))
        return {"updated": updated, "batches": batches, "error": str(exc)}
    finally:
        await engine.dispose()

    _log.info(
        "maintenance.backfill_memory_embeddings.done",
        updated=updated,
        batches=batches,
    )
    return {"updated": updated, "batches": batches}
