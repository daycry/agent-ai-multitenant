"""Celery tasks the notification-dispatcher executes (task_10_02 / task_10_04).

Two entry points:

  * ``send_notification`` (task_10_02) — deliver one notification over a
    configured channel and record the attempt in ``notification_logs``.
  * ``dispatch_event`` (task_10_04) — fan one domain event out to its
    subscribed channels: resolve recipients via ``NotificationPreference``
    (most-specific-wins), suppress opt-outs, defer quiet-hours, render the
    template, and enqueue a ``send_notification`` per surviving channel.

The dispatcher connects with the BYPASSRLS ``migrations_user`` role
(config.py) because it legitimately delivers across tenants, so RLS
cannot catch a tampered or buggy Celery payload that pairs one tenant
with another tenant's channel. We therefore validate channel↔tenant
ownership EXPLICITLY at the worker boundary (``channel.tenant_id ==
request.tenant_id``) before doing anything with the channel, and we set
``app.tenant_id`` defensively on the session so any RLS-governed read the
dispatch path makes is scoped to the request's tenant — exactly the Plan
06.14 task_06_14_02 pattern the workers use (multi-tenancy-rls-1/5).

On a delivery failure (a channel API error, a tampered cross-tenant
payload, a missing adapter) the send is recorded as ``failed`` and parked
on a Redis dead-letter stream for operator visibility / manual
reprocessing. We deliberately do NOT auto-retry here — the
exponential-backoff retry policy is Plan 10 Fase C task_10_13.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from notification_dispatcher.adapters import (
    ChannelMessage,
    ChannelSendError,
    DeliveryResult,
    get_adapter,
)
from notification_dispatcher.celery_app import app
from notification_dispatcher.config import Settings, get_settings
from notification_dispatcher.event_mapping import (
    DispatchPlan,
    IncomingEvent,
    lane_queue,
    resolve_event_dispatch,
)
from notification_dispatcher.secrets import resolve_channel_secret

_log = structlog.get_logger("notification_dispatcher.tasks")

# Truncate a provider error before it reaches NotificationLog.error so a
# verbose stack/HTML body can't bloat the row. Tunable here, not magic.
_MAX_ERROR_LEN = 2_000


class CrossTenantNotificationError(RuntimeError):
    """A SendRequest's ``channel_id`` does not belong to its declared
    ``tenant_id``.

    The dispatcher is BYPASSRLS (config.py) because it legitimately
    delivers notifications for many tenants — so RLS cannot catch a
    tampered or buggy Celery payload that pairs one tenant with another
    tenant's channel. We validate the channel↔tenant ownership explicitly
    at the worker boundary instead (Plan 06.14 task_06_14_02 /
    multi-tenancy-rls-1, multi-tenancy-rls-5).
    """


@dataclass(frozen=True)
class SendRequest:
    """One notification to deliver. The JSON-safe Celery payload.

    ``tenant_id`` is None for a platform-scoped send (a System Admin ops
    channel, tenant-agnostic by design — see the notification model
    docstring). ``channel_id`` is the configured channel to deliver over;
    ``event_type`` is the system event that triggered the send (task_10_04
    maps events → notifications); ``body`` / ``structured`` are the
    rendered message (Jinja2 templating lands in task_10_03).
    """

    channel_id: str
    event_type: str
    tenant_id: str | None = None
    target: str | None = None
    body: str = ""
    structured: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "event_type": self.event_type,
            "tenant_id": self.tenant_id,
            "target": self.target,
            "body": self.body,
            "structured": self.structured,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SendRequest:
        return cls(
            channel_id=raw["channel_id"],
            event_type=raw["event_type"],
            tenant_id=raw.get("tenant_id"),
            target=raw.get("target"),
            body=raw.get("body", ""),
            structured=raw.get("structured"),
        )


@app.task(name="notification_dispatcher.send_notification")  # type: ignore[misc]
def send_notification(request: dict[str, Any]) -> dict[str, Any]:
    """Deliver one notification end to end (Plan 10 Fase A task_10_02).

    The enqueuer (api-server / orchestrator, task_10_04) sends the request
    as a plain dict. The DB and Redis handles are built from ``Settings``;
    the result is a JSON-safe dict ``{log_id, status, channel_type,
    attempt}``.

    On an unhandled failure (a tampered cross-tenant payload, a DB/broker
    outage, a channel API error) the send is recorded — when possible —
    as ``failed`` and pushed onto the dead-letter stream, then the
    exception is re-raised so Celery marks the job failed. Sends are NOT
    auto-retried here (task_10_13 owns retry/backoff).
    """
    settings = get_settings()
    try:
        return asyncio.run(_send_notification(SendRequest.from_dict(request), settings))
    except Exception as exc:
        _record_dead_letter(settings, request, exc)
        raise


def _record_dead_letter(settings: Settings, request: dict[str, Any], exc: Exception) -> None:
    """Best-effort: push a failed send onto the dead-letter stream. Never
    masks the original error (a DLQ outage just logs a warning)."""
    try:
        asyncio.run(_push_dead_letter(settings, request, exc))
    except Exception as dlq_exc:  # pragma: no cover - DLQ is best-effort
        _log.warning(
            "notification_dispatcher.dead_letter_record_failed",
            channel_id=str(request.get("channel_id", "")),
            error=str(dlq_exc),
        )


async def _push_dead_letter(settings: Settings, request: dict[str, Any], exc: Exception) -> None:
    redis: Redis = Redis.from_url(settings.events_redis_url, decode_responses=True)
    try:
        await redis.xadd(
            settings.dead_letter_stream,
            {
                "task": "notification_dispatcher.send_notification",
                "tenant_id": str(request.get("tenant_id") or ""),
                "channel_id": str(request.get("channel_id", "")),
                "event_type": str(request.get("event_type", "")),
                "error": f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_LEN],
                "failed_at_unix": str(time.time()),
            },
            maxlen=settings.dead_letter_maxlen,
            approximate=True,
        )
    finally:
        await redis.aclose()


# ===========================================================================
# Plan 10 Fase A task_10_04 — system event → notification fan-out.
#
# `dispatch_event` is the entry point the api-server / orchestrator enqueues
# when a domain event fires (plan approved, task blocked, review requested,
# …). It resolves the event to a per-channel `DispatchPlan` (recipients via
# NotificationPreference most-specific-wins, opt-out suppression, quiet-hours
# deferral, template render) then enqueues one `send_notification` per
# surviving channel onto that event's lane. The plan resolution is a pure
# async function (`resolve_event_dispatch`) so it is unit-testable without
# the broker; this task only adds the engine lifecycle + the enqueue.
# ===========================================================================
@app.task(name="notification_dispatcher.dispatch_event")  # type: ignore[misc]
def dispatch_event(event: dict[str, Any]) -> dict[str, Any]:
    """Fan one domain event out to its subscribed channels (task_10_04).

    The enqueuer sends the event as a plain dict (``event_type``,
    ``tenant_id``, ``context``, optional ``locale``). We resolve the
    dispatch plan and enqueue a ``send_notification`` per SEND/DEFERRED
    decision (DEFERRED rides an ``eta`` past its quiet-hours window).

    Tenant isolation is enforced inside ``resolve_event_dispatch`` (only
    the event's tenant + platform-wide channels are ever resolved) and again
    at each ``send_notification`` boundary. Returns a JSON-safe summary
    ``{event_type, tenant_id, enqueued, suppressed, deferred, no_op}``.
    """
    settings = get_settings()
    return asyncio.run(_dispatch_event(IncomingEvent.from_dict(event), settings))


async def _dispatch_event(event: IncomingEvent, settings: Settings) -> dict[str, Any]:
    """Async core of ``dispatch_event`` — resolve then enqueue."""
    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        plan = await resolve_event_dispatch(event, settings=settings, sessionmaker=sessionmaker)
    finally:
        await engine.dispose()

    return _enqueue_plan(plan, settings)


def _enqueue_plan(plan: DispatchPlan, settings: Settings) -> dict[str, Any]:
    """Enqueue a ``send_notification`` per SEND/DEFERRED decision in ``plan``.

    Split out (and synchronous) so a test can assert which sends the plan
    produced without a live broker — it can pass a plan and patch
    ``send_notification.apply_async``. DEFERRED sends ride an ``eta``.
    """
    from notification_dispatcher.event_mapping import DispatchDecision

    enqueued = 0
    deferred = 0
    suppressed = 0
    for decision in plan.decisions:
        if decision.decision is DispatchDecision.SUPPRESSED:
            suppressed += 1
            continue
        if decision.send_request is None:  # pragma: no cover - defensive
            continue
        queue = lane_queue(decision.lane, settings)
        kwargs: dict[str, Any] = {"queue": queue}
        if decision.decision is DispatchDecision.DEFERRED and decision.eta is not None:
            kwargs["eta"] = decision.eta
            deferred += 1
        else:
            enqueued += 1
        send_notification.apply_async(args=[decision.send_request], **kwargs)

    return {
        "event_type": plan.event_type,
        "tenant_id": plan.tenant_id,
        "enqueued": enqueued,
        "deferred": deferred,
        "suppressed": suppressed,
        "no_op": plan.no_op,
    }


async def _send_notification(request: SendRequest, settings: Settings) -> dict[str, Any]:
    """Async core of ``send_notification`` — owns the engine lifecycle."""
    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        return await _dispatch(request, settings=settings, sessionmaker=sessionmaker)
    finally:
        await engine.dispose()


async def _dispatch(
    request: SendRequest,
    *,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """Look up the channel, validate ownership, deliver, log the attempt.

    Split out from ``_send_notification`` so a test can drive it with an
    injected sessionmaker (and a registered fake adapter) without touching
    the Celery / engine plumbing.
    """
    from api_server.db.notification import NotificationChannel, NotificationLog

    channel_id = UUID(request.channel_id)
    request_tenant = UUID(request.tenant_id) if request.tenant_id else None

    async with sessionmaker() as session, session.begin():
        # Defensive tenant scoping: even though the dispatcher is BYPASSRLS,
        # set app.tenant_id so any RLS-governed read on this session is
        # scoped to the request's tenant (Plan 06.14 task_06_14_02). A
        # platform-scoped send (NULL tenant) sets it to the empty string,
        # which NULLIF(...)::uuid maps to NULL → matches zero tenant rows.
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(request_tenant) if request_tenant is not None else ""},
        )

        channel = await session.get(NotificationChannel, channel_id)

        # --- Tenant ownership check at the worker boundary --------------
        # RLS can't stop a Celery payload that pairs a tenant with another
        # tenant's channel (we're BYPASSRLS). Validate explicitly: the
        # channel must exist AND its tenant_id must equal the request's.
        # NULL == NULL (platform-scoped) is allowed; everything else is a
        # cross-tenant attempt and is rejected.
        if channel is None or channel.tenant_id != request_tenant:
            _log.error(
                "notification_dispatcher.cross_tenant_send_rejected",
                requested_tenant_id=(str(request_tenant) if request_tenant else None),
                channel_id=str(channel_id),
                actual_tenant_id=(
                    str(channel.tenant_id)
                    if channel is not None and channel.tenant_id is not None
                    else None
                ),
            )
            raise CrossTenantNotificationError(
                f"channel {channel_id} does not belong to tenant {request_tenant}"
            )

        if not channel.enabled or channel.deleted_at is not None:
            raise ChannelSendError(f"channel {channel_id} is disabled or deleted")

        channel_type = channel.channel_type
        target = request.target or _default_target(channel)
        # Resolve the secret IN MEMORY (Vault ref or Fernet ciphertext) —
        # never persisted, never logged.
        secret = resolve_channel_secret(channel, settings)

        # --- Deliver via the channel adapter ----------------------------
        result = await _deliver(
            channel_type=channel_type,
            message=ChannelMessage(
                channel_type=channel_type,
                target=target,
                body=request.body,
                structured=request.structured,
                secret=secret,
                config=dict(channel.config or {}),
            ),
            timeout_s=settings.channel_send_timeout_s,
        )

        # --- Record the attempt -----------------------------------------
        now = datetime.now(UTC)
        log = NotificationLog(
            channel_id=channel_id,
            tenant_id=request_tenant,
            event_type=request.event_type,
            channel_type=channel_type,
            status="sent" if result.ok else "failed",
            target=target,
            attempt=1,
            error=(result.error[:_MAX_ERROR_LEN] if result.error else None),
            sent_at=now if result.ok else None,
        )
        session.add(log)
        await session.flush()
        log_id = log.id

    if not result.ok:
        # A failed send is dead-lettered (NOT auto-retried). The log row is
        # already committed as ``failed``; raising here also surfaces the
        # failure to Celery and triggers the dead-letter push in the task
        # wrapper.
        raise ChannelSendError(f"channel {channel_id} ({channel_type}) send failed: {result.error}")

    return {
        "log_id": str(log_id),
        "status": "sent",
        "channel_type": channel_type,
        "attempt": 1,
    }


async def _deliver(
    *, channel_type: str, message: ChannelMessage, timeout_s: float
) -> DeliveryResult:
    """Route to the registered adapter and bound the send by a timeout.

    A missing adapter (a catalogued channel whose Fase B/C adapter has not
    landed), a timeout, or an adapter raising :class:`ChannelSendError`
    all collapse to ``DeliveryResult(ok=False, ...)`` so the caller records
    a ``failed`` log + dead-letters — the dispatcher never crashes on a
    channel-side problem.
    """
    adapter = get_adapter(channel_type)
    if adapter is None:
        return DeliveryResult(ok=False, error=f"no adapter registered for {channel_type!r}")
    try:
        return await asyncio.wait_for(adapter.send(message), timeout=timeout_s)
    except TimeoutError:
        return DeliveryResult(ok=False, error=f"send timed out after {timeout_s}s")
    except ChannelSendError as exc:
        return DeliveryResult(ok=False, error=str(exc))


def _default_target(channel: Any) -> str | None:
    """Best-effort non-secret target from the channel config.

    Channels store their recipient (chat id, address, URL) in the
    non-secret ``config`` JSONB. The send request may override it; when it
    doesn't, fall back to the well-known config keys.
    """
    config = channel.config or {}
    for key in ("target", "chat_id", "address", "to", "url", "webhook_url"):
        value = config.get(key)
        if value:
            return str(value)
    return None


__all__ = [
    "CrossTenantNotificationError",
    "SendRequest",
    "dispatch_event",
    "send_notification",
]
