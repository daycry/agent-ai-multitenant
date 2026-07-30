"""Celery adapter for the document ingestion pipeline (Plan 06.11
task_06_11_01).

Plan 04 built the pure pipeline `api_server.ingestion.ingest_document`
(AV scan → docling parse → embed → persist chunks) but **never wired a
producer**: `upload_document` wrote a `pending` row and returned, and no
task ever processed it. Documents sat in `pending` forever and were
never searchable by RAG.

This module is the thin adapter the pipeline's docstring always assumed
existed:

  - :func:`ingest_document_task` — Celery entry point
    (`workers.ingest_document`), queue ``ingestion``. Opens a session
    on the BYPASSRLS worker engine, builds the four real dependencies
    (MinIO / ClamAV / docling-serve / Ollama embedder), runs the
    pipeline and commits. The pipeline writes ``tenant_id`` explicitly
    on every chunk, so BYPASSRLS is safe.
  - :func:`trigger_ingestion` — called by `upload_document` right after
    the flush. Swallows broker errors so an upload still returns 201
    when Redis is down; the beat sweep below is the safety net.
  - :func:`sweep_pending_documents` — beat task that re-enqueues
    documents stuck in ``pending`` (a missed enqueue, a worker crash
    mid-flight, or an upload while the broker was down).

The async cores take injectable factories so tests pass fakes
(`HashEmbedder`, `StaticDoclingClient`, `NullAntivirus`,
`InMemoryObjectStorage`) and never touch the network.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine

_log = structlog.get_logger("workers.ingestion")

# The ingestion queue (declared in celery_app QUEUE_NAMES).
_QUEUE = "ingestion"

# Factory aliases — zero-arg for the heavy deps (they read their own
# api-server Settings from env), and settings-arg for redis (needs the
# events stream URL the worker carries).
StorageFactory = Callable[[], Any]
AntivirusFactory = Callable[[], Any]
DoclingFactory = Callable[[], Any]
EmbedderFactory = Callable[[], Any]
RedisFactory = Callable[[Settings], "Redis | None"]


# ---------------------------------------------------------------------------
# Default (production) factories — wire the real services.
# ---------------------------------------------------------------------------
def _default_storage_factory() -> Any:
    from api_server.storage import get_object_storage

    return get_object_storage()


def _default_antivirus_factory() -> Any:
    from api_server.ingestion.antivirus import ClamAVScanner

    return ClamAVScanner()


def _default_docling_factory() -> Any:
    from api_server.config import get_settings as get_api_settings
    from api_server.ingestion.docling import HttpDoclingClient

    return HttpDoclingClient(base_url=get_api_settings().docling_serve_url)


def _default_embedder_factory() -> Any:
    from api_server.ingestion.embeddings import OllamaEmbedder

    return OllamaEmbedder()


def _default_redis_factory(settings: Settings) -> Redis | None:
    client: Redis = Redis.from_url(settings.events_redis_url)
    return client


async def _aclose(obj: Any) -> None:
    """Best-effort aclose — the real httpx-backed clients hold sockets;
    the in-memory fakes are no-ops. The shared MinIO client is process
    global (lru_cache) so we never close storage here."""
    closer = getattr(obj, "aclose", None)
    if closer is None:
        return
    with contextlib.suppress(Exception):  # pragma: no cover - defensive
        await closer()


# ---------------------------------------------------------------------------
# Disponibilidad del antivirus (prod-12 av_01 / ADR 0105)
# ---------------------------------------------------------------------------
# Best-effort: marca cuándo empezó la racha de fallos del backend AV y, pasado
# el umbral, emite UNA notificación al operador vía notification-dispatcher
# (re-aviso como mucho cada 6 h). La regla de alerta "de verdad" vive en
# prod-08; esto es la señal temprana que ese plan cablea.
_AV_DOWN_KEY = "ingestion:av_down_since"
_AV_NOTIFIED_KEY = "ingestion:av_down_notified"
_AV_NOTIFY_AFTER_S = 15 * 60
_AV_RENOTIFY_TTL_S = 6 * 3600
_DISPATCH_EVENT_TASK = "notification_dispatcher.dispatch_event"


async def _track_av_availability(
    redis: Any,
    settings: Settings,
    *,
    tenant_id: str | None,
    unavailable: bool,
) -> None:
    """Registra la racha de indisponibilidad del AV y avisa pasado el umbral.

    Nunca rompe la ingesta: cualquier error aquí se loguea y se traga."""
    if redis is None:
        return
    try:
        if not unavailable:
            await redis.delete(_AV_DOWN_KEY)
            return
        now = time.time()
        await redis.set(_AV_DOWN_KEY, str(now), nx=True)
        raw = await redis.get(_AV_DOWN_KEY)
        if raw is None:
            return
        since = float(raw.decode() if isinstance(raw, bytes | bytearray) else raw)
        if (now - since) < _AV_NOTIFY_AFTER_S:
            return
        # Single-flight del aviso (nx + TTL de re-aviso).
        if not await redis.set(_AV_NOTIFIED_KEY, "1", nx=True, ex=_AV_RENOTIFY_TTL_S):
            return
        from workers.celery_app import app as celery_app

        celery_app.send_task(
            _DISPATCH_EVENT_TASK,
            args=[
                {
                    "event_type": "antivirus_unreachable",
                    "tenant_id": tenant_id or "",
                    "context": {"minutes_down": int((now - since) // 60)},
                }
            ],
            queue=settings.notifications_event_queue,
        )
        _log.warning("ingestion.antivirus_down_notified", minutes_down=int((now - since) // 60))
    except Exception as exc:  # pragma: no cover — best-effort
        _log.warning("ingestion.av_tracking_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Async core
# ---------------------------------------------------------------------------
async def _ingest_document_async(
    document_id: UUID,
    *,
    settings: Settings,
    storage_factory: StorageFactory = _default_storage_factory,
    antivirus_factory: AntivirusFactory = _default_antivirus_factory,
    docling_factory: DoclingFactory = _default_docling_factory,
    embedder_factory: EmbedderFactory = _default_embedder_factory,
    redis_factory: RedisFactory = _default_redis_factory,
) -> dict[str, Any]:
    """Run the ingestion pipeline for one document and commit.

    Returns a JSON-safe dict (so Celery's result backend keeps a
    breadcrumb): ``{"document_id", "status", "chunks", "error"}``.
    Never raises into the Celery worker.
    """
    from api_server.ingestion import ingest_document

    engine = worker_engine(settings)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    storage = storage_factory()
    antivirus = antivirus_factory()
    docling = docling_factory()
    embedder = embedder_factory()
    redis = redis_factory(settings)
    try:
        async with sessionmaker() as session:
            try:
                result = await ingest_document(
                    session,
                    document_id=document_id,
                    storage=storage,
                    antivirus=antivirus,
                    docling=docling,
                    embedder=embedder,
                    redis=redis,
                    av_failure_mode=settings.av_failure_mode,
                )
                await session.commit()
            except LookupError as exc:
                await session.rollback()
                _log.info("ingestion.document_missing", document_id=str(document_id))
                return {
                    "document_id": str(document_id),
                    "status": "not_found",
                    "chunks": 0,
                    "error": str(exc),
                }
        # prod-12 av_01: racha de indisponibilidad del AV → aviso al operador.
        av_unavailable = result.status == "pending_scan"
        tenant_for_alert: str | None = None
        if av_unavailable:
            async with sessionmaker() as session:
                row = await session.execute(
                    text("SELECT tenant_id FROM documents WHERE id = :doc"),
                    {"doc": str(result.document_id)},
                )
                tenant = row.scalar_one_or_none()
                tenant_for_alert = str(tenant) if tenant is not None else None
        await _track_av_availability(
            redis, settings, tenant_id=tenant_for_alert, unavailable=av_unavailable
        )
        return {
            "document_id": str(result.document_id),
            "status": result.status,
            "chunks": result.chunks_persisted,
            "error": result.error_message,
        }
    except Exception as exc:  # never propagate into Celery
        _log.exception("ingestion.failed", document_id=str(document_id), error=str(exc))
        return {
            "document_id": str(document_id),
            "status": "error",
            "chunks": 0,
            "error": str(exc),
        }
    finally:
        for dep in (docling, embedder, antivirus):
            await _aclose(dep)
        if redis is not None:
            await _aclose(redis)
        await engine.dispose()


async def _sweep_pending_documents_async(
    *,
    settings: Settings,
    enqueue: Callable[[UUID], bool],
    older_than_seconds: int = 300,
    lease_seconds: int = 600,
) -> dict[str, Any]:
    """Claim documents stuck in ``pending`` and re-enqueue them via
    ``enqueue``. Returns ``{"reenqueued": int}``.

    Runs on the BYPASSRLS worker engine so it sweeps every tenant. Two guards
    keep it from re-enqueueing documents that are still legitimately in flight
    (workers-11 — a >5-min backlog on the ``ingestion`` queue used to cause a
    re-enqueue storm):

      - ``older_than_seconds`` — the age cutoff that avoids racing a
        just-uploaded document whose own enqueue is still in flight.
      - ``lease_seconds`` — the enqueue LEASE. A document is re-enqueued only if
        its lease (``enqueued_at``) is NULL (the enqueue never landed) or has
        expired; a document enqueued within the lease window is presumed still
        queued and is left alone.

    The claim is a single ``UPDATE … RETURNING`` that stamps the lease and
    returns the claimed ids in one statement, so two concurrent sweeps never
    both claim the same document (the second's predicate no longer matches the
    freshly-stamped rows).
    """
    engine = worker_engine(settings)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session, session.begin():
            rows = await session.execute(
                text(
                    "UPDATE documents SET enqueued_at = now()"
                    # pending_scan (prod-12 av_01): documento a la espera de que
                    # el antivirus vuelva — el mismo sweep lo reintenta.
                    " WHERE status IN ('pending', 'pending_scan')"
                    "   AND deleted_at IS NULL"
                    "   AND created_at < now() - make_interval(secs => :age)"
                    "   AND (enqueued_at IS NULL"
                    "        OR enqueued_at < now() - make_interval(secs => :lease))"
                    " RETURNING id"
                ),
                {"age": older_than_seconds, "lease": lease_seconds},
            )
            doc_ids = [row[0] for row in rows.all()]
    finally:
        await engine.dispose()

    count = 0
    for doc_id in doc_ids:
        if enqueue(doc_id):
            count += 1
    if count:
        _log.info("ingestion.swept_pending", reenqueued=count)
    return {"reenqueued": count}


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------
@app.task(name="workers.ingest_document")  # type: ignore[untyped-decorator]
def ingest_document_task(document_id: str) -> dict[str, Any]:
    """Celery entry point. Ingest one document end-to-end."""
    return asyncio.run(_ingest_document_async(UUID(document_id), settings=get_settings()))


@app.task(name="workers.sweep_pending_documents")  # type: ignore[untyped-decorator]
def sweep_pending_documents() -> dict[str, Any]:
    """Beat task: re-enqueue documents stuck in ``pending``."""
    return asyncio.run(
        _sweep_pending_documents_async(settings=get_settings(), enqueue=trigger_ingestion)
    )


def trigger_ingestion(document_id: UUID) -> bool:
    """Enqueue ingestion for one document. Returns True if enqueued.

    Called by `upload_document` after the flush and by the sweep. A
    broker failure is swallowed (the upload must still return 201; the
    sweep re-tries) — mirrors `workers.memorizer.trigger_memorize`.
    """
    try:
        ingest_document_task.apply_async(args=[str(document_id)], queue=_QUEUE)
    except Exception as exc:
        _log.warning("ingestion.enqueue_failed", document_id=str(document_id), error=str(exc))
        return False
    return True


__all__ = [
    "ingest_document_task",
    "sweep_pending_documents",
    "trigger_ingestion",
]
