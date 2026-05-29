"""Lightweight Celery producer for api-server (Plan 06.11 task_06_11_01).

api-server runs **no** Celery tasks — it only *enqueues* them onto the
shared broker by name (the `workers` package owns the implementations).
A bare ``Celery(broker=...)`` is all `send_task` needs, so we never
import the `workers` package: that keeps the app boundary clean and
mirrors `orchestrator.dispatch`, which enqueues `workers.run_execution`
the same way.

The single producer here is `enqueue_ingestion`, called by
`upload_document` right after the document row is flushed.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from uuid import UUID

import structlog
from celery import Celery

from api_server.config import get_settings

_log = structlog.get_logger("api_server.celery_client")

_INGEST_TASK = "workers.ingest_document"
_INGEST_QUEUE = "ingestion"


@lru_cache(maxsize=1)
def get_celery_client() -> Celery:
    """Process-global producer bound to the broker. Cached so we don't
    rebuild the connection pool on every enqueue."""
    return Celery(broker=get_settings().broker_url)


def reset_celery_client_cache() -> None:
    """Drop the cached client (tests that swap the broker URL)."""
    get_celery_client.cache_clear()


async def enqueue_ingestion(document_id: UUID) -> bool:
    """Hand a freshly-uploaded document to the ingestion worker.

    Best-effort: a broker failure is logged and swallowed so the upload
    still returns 201 — the document is already persisted as `pending`
    and the beat sweep `workers.sweep_pending_documents` re-enqueues it.
    Returns True iff the task was published.

    `send_task` does blocking socket I/O, so we run it off the event
    loop (same approach as `orchestrator.dispatch`).
    """
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            _INGEST_TASK,
            args=[str(document_id)],
            queue=_INGEST_QUEUE,
        )
    except Exception as exc:
        _log.warning("ingestion.enqueue_failed", document_id=str(document_id), error=str(exc))
        return False
    return True


__all__ = ["enqueue_ingestion", "get_celery_client", "reset_celery_client_cache"]
