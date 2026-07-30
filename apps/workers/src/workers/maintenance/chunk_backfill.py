"""Chunk-embedding back-fill — ``workers.backfill_chunk_embeddings`` (P1-11b).

La ingesta deja ``chunks.embedding = NULL`` cuando Ollama falla en el momento de
crear (fail-open deliberado: el chunk sigue recuperable por BM25), y el propio
pipeline citaba un job de re-embed «follow-up pendiente» que NUNCA se cableó —
solo existía el de memorias (``memory_backfill``). Este beat es su espejo:
idempotente, por lotes con ``FOR UPDATE SKIP LOCKED``, throttled y best-effort
(un embedder caído corta la pasada y la siguiente reintenta). Reutiliza las
MISMAS platform settings del backfill de memorias (mismo embedder, misma
dimensión 768, mismo criterio operativo).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import structlog
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine
from workers.maintenance.memory_backfill import (
    _BACKFILL_MAX_BATCHES_PER_RUN,
    EmbedderFactory,
    _default_embedder_factory,
)

_log = structlog.get_logger("workers.maintenance")


@app.task(name="workers.backfill_chunk_embeddings")  # type: ignore[untyped-decorator]
def backfill_chunk_embeddings() -> dict[str, Any]:
    """Rellena los ``chunks.embedding`` NULL — idempotente y convergente."""
    settings = get_settings()
    return asyncio.run(
        _backfill_chunk_embeddings_async(
            settings=settings,
            embedder_factory=_default_embedder_factory,
        )
    )


async def _backfill_chunk_embeddings_async(
    *,
    settings: Settings,
    embedder_factory: EmbedderFactory,
) -> dict[str, Any]:
    """Núcleo async (los tests inyectan un HashEmbedder determinista)."""
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
            return {"updated": 0, "batches": 0, "reason": "disabled"}

        embedder = embedder_factory(settings)
        try:
            while batches < _BACKFILL_MAX_BATCHES_PER_RUN:
                async with sessionmaker() as session, session.begin():
                    rows = (
                        await session.execute(
                            sa_text(
                                "SELECT chunks.id, chunks.tenant_id, chunks.content"
                                " FROM chunks"
                                " JOIN documents ON documents.id = chunks.document_id"
                                "      AND documents.deleted_at IS NULL"
                                " WHERE chunks.embedding IS NULL"
                                " ORDER BY chunks.created_at"
                                " LIMIT :limit"
                                " FOR UPDATE OF chunks SKIP LOCKED"
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
                    except Exception as exc:  # embedder caído → próxima pasada
                        _log.warning(
                            "maintenance.backfill_chunk_embeddings.embed_failed",
                            error=str(exc),
                            count=len(contents),
                        )
                        break
                    if len(vectors) != len(rows):
                        _log.warning(
                            "maintenance.backfill_chunk_embeddings.count_mismatch",
                            expected=len(rows),
                            got=len(vectors),
                        )
                        break

                    for row, vec in zip(rows, vectors, strict=True):
                        qvec = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
                        await session.execute(
                            sa_text(
                                "UPDATE chunks SET embedding = CAST(:vec AS vector)"
                                " WHERE id = :id AND tenant_id = :tenant_id"
                            ),
                            {"vec": qvec, "id": row.id, "tenant_id": row.tenant_id},
                        )
                        updated += 1
                if throttle_ms > 0:
                    await asyncio.sleep(throttle_ms / 1000.0)
        finally:
            with contextlib.suppress(Exception):  # pragma: no cover - best-effort
                await embedder.aclose()
        _log.info("maintenance.backfill_chunk_embeddings.done", updated=updated, batches=batches)
        return {"updated": updated, "batches": batches}
    except Exception as exc:  # el beat jamás muere por una pasada
        _log.warning("maintenance.backfill_chunk_embeddings.failed", error=str(exc))
        return {"updated": updated, "batches": batches, "error": str(exc)}
    finally:
        await engine.dispose()
