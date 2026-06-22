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

# The restore background jobs (Plan 12 task_12_12). A restore is LONG +
# DESTRUCTIVE, so the api-server NEVER runs it inline — it enqueues one of these
# by name onto the privileged lane (a restore touches infra + secrets) and then
# polls the job's status via the result backend (`get_restore_job_status`). The
# `workers.restore_task` module owns the implementation + the double-confirmation
# / verify-before-restore / per-tenant-isolation guards.
_RESTORE_FULL_TASK = "workers.run_restore"
_RESTORE_PER_TENANT_TASK = "workers.run_restore_per_tenant"
_RESTORE_QUEUE = "privileged"

# The human Memorizer task (Plan 16 task_16_15). When a human task reaches
# `done` (auto_approve submit, or a peer reviewer's approval) the inbox/review
# endpoint enqueues this by name so the Memorizer distils the HumanWorkSession
# into MemoryEntries. The `workers.memorizer` module owns the implementation;
# the api-server only PRODUCES it by name (clean app boundary, same as the
# execution Memorizer trigger that lives in the workers package).
_MEMORIZE_HUMAN_WS_TASK = "workers.memorize_human_work_session"
_MEMORIZE_QUEUE = "default"


@lru_cache(maxsize=1)
def get_celery_client() -> Celery:
    """Process-global producer bound to the broker. Cached so we don't
    rebuild the connection pool on every enqueue.

    Also wired to the result backend so `AsyncResult(job_id)` resolves the state
    a background task wrote (the restore job's progress/result, task_12_12). The
    api-server still only PRODUCES + READS state — it runs no tasks."""
    settings = get_settings()
    return Celery(broker=settings.broker_url, backend=settings.result_backend)


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


async def enqueue_clone_project_repo(project_id: UUID) -> bool:
    """Encola el clone/fetch autenticado del repo de un proyecto (ADR 0072).

    Best-effort: un fallo del broker se loguea y se traga — la config git ya está
    persistida y el clone se puede re-disparar (acción "Sincronizar"). Corre el
    ``send_task`` (I/O bloqueante) fuera del event loop."""
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            "workers.clone_project_repo",
            args=[str(project_id)],
            queue="default",
        )
    except Exception as exc:
        _log.warning("clone_repo.enqueue_failed", project_id=str(project_id), error=str(exc))
        return False
    return True


async def enqueue_open_plan_pr(project_id: UUID, plan_id: UUID, *, title: str, body: str) -> bool:
    """Encola el auto-PR de un plan (ADR 0072 fase 2): push autenticado de la rama
    + apertura del PR/MR por proveedor. La rama se deriva en el worker de
    ``plan_id`` + ``title``. Best-effort."""
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            "workers.open_plan_pr",
            args=[str(project_id), str(plan_id), title, body],
            queue="default",
        )
    except Exception as exc:
        _log.warning("plan_pr.enqueue_failed", project_id=str(project_id), error=str(exc))
        return False
    return True


async def revoke_execution_job(job_id: str) -> bool:
    """Revoke a running/queued execution Celery job (cooperative cancellation).

    ``terminate=True`` kills the worker child if the job is already running (the
    worker then sees the ``cancel_requested_at`` flag to finalise as ``cancelled``)
    and drops it if still queued. Best-effort: the DB flag is the source of truth,
    so a broker failure is logged and swallowed — the worker still honours the flag
    cooperatively. ``control.revoke`` does blocking socket I/O, so it runs off the
    event loop. Returns True iff the revoke was published.
    """
    try:
        await asyncio.to_thread(
            get_celery_client().control.revoke,
            job_id,
            terminate=True,
        )
    except Exception as exc:
        _log.warning("execution.revoke_failed", job_id=job_id, error=str(exc))
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


async def enqueue_memorize_human_work_session(work_session_id: UUID) -> bool:
    """Hand a finished HumanWorkSession to the Memorizer (Plan 16 task_16_15).

    Called by the inbox submit (``auto_approve``) and the peer-review approve
    endpoints right after the Task reaches ``done`` — the human's deliverable
    is final, so the Memorizer can distil it into MemoryEntries cited back at
    this work session.

    Best-effort: a broker failure is logged and swallowed (returns False) so the
    human's delivery is never rolled back on a Memorizer-side outage — the
    memory is a nice-to-have, not part of the delivery transaction.
    ``send_task`` does blocking socket I/O, so we run it off the event loop
    (same approach as :func:`enqueue_ingestion`).
    """
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            _MEMORIZE_HUMAN_WS_TASK,
            args=[str(work_session_id)],
            queue=_MEMORIZE_QUEUE,
        )
    except Exception as exc:
        _log.warning(
            "memorize_human_ws.enqueue_failed",
            work_session_id=str(work_session_id),
            error=str(exc),
        )
        return False
    return True


async def enqueue_restore(
    backup_id: str,
    *,
    confirm: str,
    tenant_id: str | None = None,
) -> str:
    """Enqueue a restore background job (Plan 12 task_12_12) and return its job id.

    A restore is LONG + DESTRUCTIVE, so it runs as a Celery background job, NOT
    inline on this request thread. When ``tenant_id`` is ``None`` a FULL-stack
    restore (``workers.run_restore``) is enqueued; otherwise a SELECTIVE
    per-tenant restore (``workers.run_restore_per_tenant``) that touches ONLY
    that tenant's data.

    ``confirm`` is the double-confirmation token the operator supplied — it is
    forwarded verbatim to the task, where the engine refuses unless it matches
    (the full restore wants the bundle id; the per-tenant restore wants
    ``<tenant_id>@<backup_id>``). The api-server does NOT re-derive or weaken it.

    Returns the Celery job id the UI then polls via :func:`get_restore_job_status`.
    A broker failure RAISES so the endpoint surfaces it (a restore the operator
    explicitly triggered must fail loudly, never be silently dropped).
    ``send_task`` does blocking socket I/O, so we run it off the event loop.
    """
    if tenant_id is None:
        async_result = await asyncio.to_thread(
            get_celery_client().send_task,
            _RESTORE_FULL_TASK,
            args=[backup_id],
            kwargs={"confirm": confirm},
            queue=_RESTORE_QUEUE,
        )
    else:
        async_result = await asyncio.to_thread(
            get_celery_client().send_task,
            _RESTORE_PER_TENANT_TASK,
            args=[backup_id],
            kwargs={"tenant_id": tenant_id, "confirm": confirm},
            queue=_RESTORE_QUEUE,
        )
    return str(async_result.id)


async def get_restore_job_status(job_id: str) -> dict[str, Any]:
    """Read a restore background job's status from the result backend (task_12_12).

    Returns a JSON-safe ``{job_id, state, progress, result, error}`` snapshot the
    UI polls:

      * ``state``    — Celery's task state (``PENDING`` / ``PROGRESS`` /
        ``SUCCESS`` / ``FAILURE``).
      * ``progress`` — the ``{phase, message}`` meta the task reported while
        in-flight (``PROGRESS``), else ``None``.
      * ``result``   — the engine's JSON-safe result dict on ``SUCCESS``, else
        ``None``.
      * ``error``    — a non-leaky error string on ``FAILURE``, else ``None``.

    Reading ``AsyncResult`` touches the result backend (blocking socket I/O), so
    we run it off the event loop. The api-server only READS this state — it runs
    no tasks.
    """
    return await asyncio.to_thread(_read_restore_status, job_id)


def _read_restore_status(job_id: str) -> dict[str, Any]:
    """Synchronous AsyncResult read (run via ``to_thread``)."""
    async_result = get_celery_client().AsyncResult(job_id)
    state = str(async_result.state)
    progress: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    if state == "SUCCESS":
        raw = async_result.result
        if isinstance(raw, dict):
            result = raw
    elif state == "FAILURE":
        # `async_result.result` is the exception instance; str() is non-leaky
        # (the engines never echo secret material into their error messages).
        error = str(async_result.result)
    else:
        # PENDING / PROGRESS / any custom in-flight state — the meta carries the
        # {phase, message} the task reported (None for an unknown/PENDING job).
        info = async_result.info
        if isinstance(info, dict):
            progress = info

    return {
        "job_id": job_id,
        "state": state,
        "progress": progress,
        "result": result,
        "error": error,
    }


__all__ = [
    "enqueue_clone_project_repo",
    "enqueue_event_dispatch",
    "enqueue_ingestion",
    "enqueue_memorize_human_work_session",
    "enqueue_notification_send",
    "enqueue_open_plan_pr",
    "enqueue_restore",
    "get_celery_client",
    "get_restore_job_status",
    "reset_celery_client_cache",
]
