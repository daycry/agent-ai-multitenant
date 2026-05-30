"""`/notifications` endpoints — manual retry of a dead-lettered send (task_10_13).

The notification-dispatcher (``apps/notification-dispatcher``) auto-retries a
transient send with exponential backoff and, once the retries are exhausted,
parks it as a ``dead_letter`` ``NotificationLog`` row (+ a Redis DLQ stream
entry). This router exposes the **operator escape hatch**: a Tenant Admin can
re-drive a dead-lettered send back through the dispatcher's normal path.

  - POST /notifications/logs/{log_id}/retry   re-enqueue a dead-lettered log

Security + correctness invariants (all tested in
``tests/integration/test_retries_dlq.py``):

  * **RBAC**: ``tenant_admin`` only — a plain ``tenant_user`` is 403.
  * **RLS-scoped**: the lookup runs on the RLS-bound tenant session, so tenant
    B asking to retry tenant A's log gets a clean 404 (no cross-tenant leak),
    not a 403 that would confirm the id exists.
  * **Only dead-lettered logs are retryable**: retrying a ``sent`` / ``queued``
    / ``retrying`` log is a 409 (nothing to retry) — the endpoint is not a
    generic "send arbitrary notification" surface.
  * **Idempotent**: the re-enqueue flips the source row OUT of ``dead_letter``
    (to ``retrying``) in the SAME transaction as the new ``queued`` row + the
    broker publish, so a double-click finds a non-dead-letter row and 409s —
    no duplicate live send.
  * **Audited**: an append-only ``audit_log`` row (``notification.retry``) is
    written in the same transaction; it can never commit without its audit
    record.

The api-server never imports the dispatcher package — it re-enqueues the send
by task name onto the shared broker via
:func:`api_server.celery_client.enqueue_notification_send` (the dispatcher owns
the implementation + the retry/backoff/DLQ policy).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.audit import write_audit_log
from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
)
from api_server.celery_client import enqueue_notification_send
from api_server.db.notification import NotificationLog, NotificationStatus
from api_server.routers._helpers import require_tenant_id

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Audit action recorded when an operator manually re-enqueues a dead-lettered
# send. Greppable across the audit trail.
_AUDIT_ACTION = "notification.retry"


class NotificationRetryResponse(BaseModel):
    """The fresh ``queued`` attempt produced by a manual retry."""

    log_id: UUID = Field(description="The id of the new queued NotificationLog row.")
    status: str = Field(description="Status of the new row (always 'queued').")
    source_log_id: UUID = Field(description="The dead-lettered log that was retried.")
    attempt: int = Field(description="1-based attempt number of the new row.")


@router.post(
    "/logs/{log_id}/retry",
    response_model=NotificationRetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_notification(
    log_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> NotificationRetryResponse:
    """Re-enqueue a dead-lettered notification send (tenant_admin only).

    Loads the log via the RLS-bound tenant session (another tenant's log →
    404), rejects a non-dead-lettered log (409), then — in one transaction —
    writes a fresh ``queued`` log row, flips the source row out of
    ``dead_letter`` (idempotency), appends a ``notification.retry`` audit row,
    and publishes the send onto the dispatcher's default lane.
    """
    tenant_id = require_tenant_id(principal)

    # RLS scopes this SELECT to the caller's tenant: another tenant's log row
    # is invisible and surfaces as a clean 404 (no cross-tenant confirmation).
    result = await session.execute(select(NotificationLog).where(NotificationLog.id == log_id))
    log = result.scalar_one_or_none()
    if log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="notification log not found"
        )

    # Only a dead-lettered send is retryable — the endpoint is the DLQ escape
    # hatch, not a generic send surface. A second click finds the already-
    # flipped row here and 409s (idempotency: no duplicate live send).
    if log.status != NotificationStatus.DEAD_LETTER.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"notification log is '{log.status}', not '{NotificationStatus.DEAD_LETTER.value}'"
                " — only a dead-lettered send can be retried"
            ),
        )

    # A retry cannot re-drive a send whose channel was deleted (FK SET NULL).
    if log.channel_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="notification log has no channel (it was deleted); cannot retry",
        )

    # Flip the source row out of dead_letter BEFORE enqueueing so a concurrent
    # double-click loses the race on this UPDATE (the per-request transaction
    # holds the row) and finds a non-dead-letter row → 409. The append-only
    # invariant is preserved: we record the new attempt as a NEW row below.
    log.status = NotificationStatus.RETRYING.value

    # The fresh attempt — a new append-only row carrying attempt+1 and the
    # resolved transport, so the full retry history is preserved.
    new_attempt = log.attempt + 1
    new_log = NotificationLog(
        channel_id=log.channel_id,
        tenant_id=tenant_id,
        event_type=log.event_type,
        channel_type=log.channel_type,
        status=NotificationStatus.QUEUED.value,
        target=log.target,
        attempt=new_attempt,
    )
    session.add(new_log)
    await session.flush()

    # Mandatory append-only audit — same transaction as the status flip + new
    # row, so the retry can never commit without its audit record.
    await write_audit_log(
        session,
        action=_AUDIT_ACTION,
        actor_user_id=principal.user_id,
        tenant_id=tenant_id,
        resource_type="notification_log",
        resource_id=new_log.id,
        changes={"source_log_id": str(log_id), "attempt": new_attempt},
    )

    # Re-enqueue onto the dispatcher's default lane. This is a network call to
    # the broker; if it fails the whole transaction rolls back (the row flip,
    # the new row, the audit) so we never claim a retry that was not enqueued.
    send_request: dict[str, Any] = {
        "channel_id": str(log.channel_id),
        "event_type": log.event_type,
        "tenant_id": str(tenant_id),
        "target": log.target,
        "body": "",
        "structured": None,
    }
    await enqueue_notification_send(send_request, queue="notifications.default")

    return NotificationRetryResponse(
        log_id=new_log.id,
        status=NotificationStatus.QUEUED.value,
        source_log_id=log_id,
        attempt=new_attempt,
    )
