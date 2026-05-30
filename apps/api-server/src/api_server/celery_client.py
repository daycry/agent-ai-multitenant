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
from typing import Any
from uuid import UUID

import structlog
from celery import Celery

from api_server.config import get_settings

_log = structlog.get_logger("api_server.celery_client")

_INGEST_TASK = "workers.ingest_document"
_INGEST_QUEUE = "ingestion"

# The notification-dispatcher send task + its default lane (Plan 10). The
# api-server only PRODUCES onto the shared broker by name — it never imports
# the notification_dispatcher package (same clean app boundary as ingestion).
# The dispatcher owns the implementation, the retry/backoff policy, and the
# DLQ. The manual-retry endpoint (task_10_13) re-enqueues a dead-lettered send
# through this producer.
_SEND_NOTIFICATION_TASK = "notification_dispatcher.send_notification"
_NOTIFICATIONS_DEFAULT_QUEUE = "notifications.default"

# The notification-dispatcher fan-out task (Plan 10 task_10_04): given a
# domain event ({event_type, tenant_id, context}) it resolves the tenant's
# subscribed channels (most-specific-wins preferences, quiet-hours, template
# render) and enqueues one send per surviving channel. The api-server only
# PRODUCES it by name (clean app boundary — never imports the dispatcher).
# Plan 11 task_11_21 fires a `guardrail_alert` event through this path.
_DISPATCH_EVENT_TASK = "notification_dispatcher.dispatch_event"
_EVENTS_PRIORITY_QUEUE = "notifications.priority"


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


async def enqueue_notification_send(
    send_request: dict[str, Any],
    *,
    queue: str = _NOTIFICATIONS_DEFAULT_QUEUE,
) -> bool:
    """Re-enqueue one notification send onto the dispatcher's lane (task_10_13).

    Used by the manual-retry endpoint to re-drive a dead-lettered
    ``NotificationLog`` through the notification-dispatcher's normal send path
    (which owns the retry/backoff + DLQ policy). ``send_request`` is the same
    JSON-safe payload ``notification_dispatcher.tasks.SendRequest.as_dict``
    produces (``channel_id`` / ``event_type`` / ``tenant_id`` / ``target`` /
    ``body`` / ``structured``).

    Returns True iff the task was published. ``send_task`` does blocking socket
    I/O, so we run it off the event loop (same approach as `enqueue_ingestion`).
    A broker failure raises so the caller can surface it (the endpoint has
    already not committed its log row in that case — unlike the best-effort
    ingestion enqueue, a manual retry that can't reach the broker must fail
    loudly rather than silently drop the user's action).
    """
    await asyncio.to_thread(
        get_celery_client().send_task,
        _SEND_NOTIFICATION_TASK,
        args=[send_request],
        queue=queue,
    )
    return True


async def enqueue_event_dispatch(
    event: dict[str, Any],
    *,
    queue: str = _EVENTS_PRIORITY_QUEUE,
) -> bool:
    """Fan a domain event out to its subscribed channels via the dispatcher.

    Enqueues ``notification_dispatcher.dispatch_event`` (task_10_04) onto the
    dispatcher's lane. ``event`` is the JSON-safe payload the dispatcher's
    ``IncomingEvent.from_dict`` expects (``event_type`` / ``tenant_id`` /
    ``context`` / optional ``locale``). The dispatcher owns recipient
    resolution (the tenant's subscribed channels / Tenant-Admin preferences),
    quiet-hours, template render, and the per-channel send + retry/DLQ — the
    api-server never imports it (clean app boundary).

    Best-effort: a broker failure is logged and swallowed (returns False) so
    the work that produced the event still completes — an alert is a
    notification, not a transaction the caller must roll back on a broker
    outage. ``send_task`` does blocking socket I/O, so we run it off the event
    loop (same approach as :func:`enqueue_ingestion`).
    """
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            _DISPATCH_EVENT_TASK,
            args=[event],
            queue=queue,
        )
    except Exception as exc:
        _log.warning(
            "event_dispatch.enqueue_failed",
            event_type=str(event.get("event_type", "")),
            tenant_id=str(event.get("tenant_id") or ""),
            error=str(exc),
        )
        return False
    return True


__all__ = [
    "enqueue_event_dispatch",
    "enqueue_ingestion",
    "enqueue_notification_send",
    "get_celery_client",
    "reset_celery_client_cache",
]
