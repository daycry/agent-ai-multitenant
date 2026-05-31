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

On a delivery failure the behaviour depends on the failure kind (task_10_13):

  * a TRANSIENT channel failure (``ChannelSendError`` — a 5xx, a timeout, a
    flaky provider) is RETRIED with EXPONENTIAL BACKOFF + JITTER up to
    ``settings.max_retries`` times. Between attempts the ``notification_logs``
    row records ``status=retrying``; once the retries are exhausted the send is
    parked (``status=dead_letter`` + a Redis dead-letter stream entry) for
    operator visibility / manual reprocessing via the api-server endpoint.
  * a NON-retryable failure (a tampered cross-tenant payload, a DB/broker
    outage) is dead-lettered immediately and re-raised — retrying it would
    never succeed.

The retry ceiling is bounded (never unbounded) and every backoff tunable
(``max_retries``, ``retry_base_backoff_s``, ``retry_max_backoff_s``,
``retry_jitter``) lives on :class:`~notification_dispatcher.config.Settings`,
so there is no magic number in this module.
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

# Import the channel-adapter package for its registration side effect: each
# channel module registers itself with the adapter registry at import time,
# so a worker that imports tasks (celery_app.imports) has every Fase B/C
# adapter wired up before the first send_notification runs. Beyond in_app
# (registered in adapters.py), this is what makes get_adapter('telegram')
# return the real adapter rather than None.
from notification_dispatcher import channels as _channels  # noqa: F401
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
from notification_dispatcher.retry import compute_backoff
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


@app.task(  # type: ignore[misc]
    bind=True,
    name="notification_dispatcher.send_notification",
)
def send_notification(self: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Deliver one notification end to end, with bounded retries (task_10_13).

    The enqueuer (api-server / orchestrator, task_10_04, or the manual-retry
    endpoint) sends the request as a plain dict. The DB and Redis handles are
    built from ``Settings``; the result is a JSON-safe dict ``{log_id, status,
    channel_type, attempt}``.

    Failure handling:

      * a TRANSIENT channel failure (:class:`ChannelSendError`) records the
        attempt as ``retrying`` and is re-scheduled via ``self.retry`` with an
        EXPONENTIAL BACKOFF + JITTER (``notification_dispatcher.retry``) until
        ``settings.max_retries`` is reached, after which the final attempt is
        recorded as ``dead_letter`` and pushed onto the DLQ stream.
      * a NON-retryable failure (:class:`CrossTenantNotificationError`, a
        DB/broker outage) is dead-lettered immediately and re-raised — no retry
        would ever make it succeed.

    ``self.request.retries`` is Celery's 0-based count of retries already made,
    so the 1-based ``attempt`` recorded on the log row is ``retries + 1``.
    """
    settings = get_settings()
    # Celery's retry counter: 0 on the first try, +1 each retry. Bounded by
    # settings.max_retries — never unbounded.
    retries = int(getattr(self.request, "retries", 0) or 0)
    is_last_attempt = retries >= settings.max_retries

    try:
        return asyncio.run(
            _send_notification(
                SendRequest.from_dict(request),
                settings,
                attempt=retries + 1,
                is_last_attempt=is_last_attempt,
            )
        )
    except ChannelSendError as exc:
        # A transient send failure: retry with backoff until exhausted, then
        # dead-letter. The ``retrying`` / ``dead_letter`` log row was already
        # written by _dispatch for this attempt (it knows is_last_attempt).
        if is_last_attempt:
            _record_dead_letter(settings, request, exc)
            raise
        countdown = compute_backoff(
            retries,
            base_backoff_s=settings.retry_base_backoff_s,
            max_backoff_s=settings.retry_max_backoff_s,
            jitter=settings.retry_jitter,
        )
        _log.info(
            "notification_dispatcher.send_retry_scheduled",
            channel_id=str(request.get("channel_id", "")),
            event_type=str(request.get("event_type", "")),
            attempt=retries + 1,
            max_retries=settings.max_retries,
            countdown_s=round(countdown, 3),
        )
        # max_retries here matches our own ceiling so Celery never out-lives it.
        # ``self.retry`` raises celery.exceptions.Retry; chain it from the
        # original ChannelSendError so the cause is preserved in logs.
        raise self.retry(exc=exc, countdown=countdown, max_retries=settings.max_retries) from exc
    except Exception as exc:
        # A non-retryable failure (cross-tenant payload, DB/broker outage):
        # dead-letter immediately and re-raise.
        _record_dead_letter(settings, request, exc)
        raise


def _record_dead_letter(settings: Settings, request: dict[str, Any], exc: Exception) -> None:
    """Best-effort: push a dead-lettered send onto the dead-letter stream.
    Never masks the original error (a DLQ outage just logs a warning)."""
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


async def _send_notification(
    request: SendRequest,
    settings: Settings,
    *,
    attempt: int = 1,
    is_last_attempt: bool = True,
) -> dict[str, Any]:
    """Async core of ``send_notification`` — owns the engine lifecycle."""
    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        return await _dispatch(
            request,
            settings=settings,
            sessionmaker=sessionmaker,
            attempt=attempt,
            is_last_attempt=is_last_attempt,
        )
    finally:
        await engine.dispose()


async def _dispatch(
    request: SendRequest,
    *,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    attempt: int = 1,
    is_last_attempt: bool = True,
) -> dict[str, Any]:
    """Look up the channel, validate ownership, deliver, log the attempt.

    Split out from ``_send_notification`` so a test can drive it with an
    injected sessionmaker (and a registered fake adapter) without touching
    the Celery / engine plumbing.

    ``attempt`` is the 1-based attempt number recorded on the
    ``notification_logs`` row; ``is_last_attempt`` tells the failure path
    whether more retries remain. A failed delivery that still has retries left
    is recorded as ``retrying`` (the task wrapper re-schedules it with
    backoff); a failed delivery on the last attempt is recorded as
    ``dead_letter`` (the wrapper parks it on the DLQ stream). Either way a
    NEW append-only log row is written for this attempt and a
    :class:`ChannelSendError` is raised so the wrapper decides retry-vs-park.
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
        # On failure the status reflects whether a retry remains: a failed
        # attempt with retries left is ``retrying`` (the wrapper re-schedules
        # it with backoff); the final failed attempt is ``dead_letter`` (the
        # wrapper parks it on the DLQ stream). The log is append-only — each
        # attempt is a NEW row carrying its 1-based ``attempt`` number, so the
        # full retry history is preserved.
        if result.ok:
            status = "sent"
        elif is_last_attempt:
            status = "dead_letter"
        else:
            status = "retrying"

        now = datetime.now(UTC)
        log = NotificationLog(
            channel_id=channel_id,
            tenant_id=request_tenant,
            event_type=request.event_type,
            channel_type=channel_type,
            status=status,
            target=target,
            attempt=attempt,
            error=(result.error[:_MAX_ERROR_LEN] if result.error else None),
            sent_at=now if result.ok else None,
        )
        session.add(log)
        await session.flush()
        log_id = log.id

    if not result.ok:
        # The attempt's log row (``retrying`` or ``dead_letter``) is already
        # committed; raising surfaces the failure to the task wrapper, which
        # decides retry-with-backoff vs park-on-DLQ from ``is_last_attempt``.
        raise ChannelSendError(f"channel {channel_id} ({channel_type}) send failed: {result.error}")

    return {
        "log_id": str(log_id),
        "status": "sent",
        "channel_type": channel_type,
        "attempt": attempt,
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
