"""System event → notification mapping (Plan 10 Fase A task_10_04).

The bridge between the domain events the platform already emits (plan
approved/rejected, task state changes, execution finished/failed, review
requested, human-validation needed, budget alerts, …) and the
multichannel notification dispatch built in task_10_02 / task_10_03.

The flow, given an arriving event for a tenant:

  1. **Map** the event to a notification ``event_type`` via the data-driven
     :data:`EVENT_REGISTRY` — the single source of truth for which domain
     events notify and on which Celery lane.
  2. **Resolve recipients**: the enabled :class:`NotificationChannel` rows
     in scope (the request's tenant + the platform-wide channels), pairing
     each with its effective :class:`NotificationPreference` resolved
     **most-specific-wins** (user → tenant → platform).
  3. **Filter**: an opt-out preference (``enabled=false``) *suppresses* the
     send; a quiet-hours window *defers* it (computes an ETA past the
     window). A channel with no matching subscription is skipped.
  4. **Render** the template for ``(event_type, channel_type, locale)``
     (task_10_03) and **enqueue** a ``send_notification`` task (task_10_02)
     onto the channel's lane.

Multi-tenancy (NON-NEGOTIABLE): the dispatcher is BYPASSRLS (config.py)
because it legitimately fans out across tenants, so RLS cannot catch a
tampered event payload. We therefore set ``app.tenant_id`` defensively on
the session AND only ever resolve channels/preferences whose
``tenant_id`` equals the event's tenant (or NULL = platform-wide) — a
tenant-A event can NEVER resolve a tenant-B channel. The same Plan 06.14
task_06_14_02 boundary pattern the workers use; every resolved channel is
re-validated at the ``send_notification`` boundary too (defence in depth).

Design split (testability): :func:`resolve_event_dispatch` is a pure
async resolver — given a sessionmaker + an event it returns a
:class:`DispatchPlan` of per-channel decisions (send / suppressed /
deferred) WITHOUT touching Celery. :func:`dispatch_event` (the Celery
task) wires the resolver to the broker. Tests drive the resolver directly.

All tunables (default locale, the quiet-hours defer clamp, the queue
lanes) live in :class:`~notification_dispatcher.config.Settings` — never
inline magic numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notification_dispatcher.config import Settings
from notification_dispatcher.templates import (
    TemplateSource,
    render_notification,
    template_source_from_row,
)

_log = structlog.get_logger("notification_dispatcher.event_mapping")

# Minutes in a day — quiet-hours windows are stored as minutes-of-day
# [0..1439] (see NotificationPreference). Named, not a magic literal.
_MINUTES_PER_DAY = 24 * 60


# =============================================================================
# Data-driven event → notification registry (the single source of truth).
# =============================================================================
class NotificationLane(StrEnum):
    """Which Celery lane a notification for an event is enqueued onto.

    Resolved to the concrete queue name from
    :class:`~notification_dispatcher.config.Settings` at enqueue time
    (``default_queue`` / ``priority_queue``) — the registry names the lane
    semantically so the queue names stay operator-tunable.
    """

    DEFAULT = "default"
    PRIORITY = "priority"


@dataclass(frozen=True)
class EventSpec:
    """How one domain event maps to a notification (registry entry).

    - ``notification_event_type``: the ``event_type`` carried into the
      preference lookup, the template key, and the ``notification_logs``
      row. Usually equal to the domain event, but the registry lets a
      domain event (``task.status_changed`` with ``new_status=blocked``)
      map to a distinct notification event (``task_blocked``).
    - ``lane``: the Celery lane (default vs priority).
    - ``default_channel_types``: the channel types this event fans out to
      when a scope has configured channels but NO explicit per-(event,
      channel) preference row. An empty tuple means "only channels with an
      explicit opt-in preference" (no implicit fan-out).
    """

    notification_event_type: str
    lane: NotificationLane = NotificationLane.DEFAULT
    default_channel_types: tuple[str, ...] = ()


# The closed catalogue of domain events that produce notifications. Adding
# an event = adding ONE entry here (+ a builtin template in templates.py).
# Keyed by the notification event_type the rest of the system already uses
# (matches the builtins + the human-test checklist in the plan). Time-
# sensitive events (escalation, budget alert, human validation) ride the
# priority lane so an ordinary backlog never delays them.
EVENT_REGISTRY: dict[str, EventSpec] = {
    "task_blocked": EventSpec(
        "task_blocked",
        lane=NotificationLane.DEFAULT,
        default_channel_types=("in_app", "telegram"),
    ),
    "plan_approved": EventSpec(
        "plan_approved",
        lane=NotificationLane.DEFAULT,
        default_channel_types=("in_app",),
    ),
    "plan_rejected": EventSpec(
        "plan_rejected",
        lane=NotificationLane.DEFAULT,
        default_channel_types=("in_app",),
    ),
    # c3/T7 (audit 2026-07-03): a plan whose only remaining open tasks are `blocked`
    # is escalated `in_progress -> blocked` by the orchestrator; the operator is
    # notified so the stall is visible and they can unblock/retry a task.
    "plan_blocked": EventSpec(
        "plan_blocked",
        lane=NotificationLane.DEFAULT,
        default_channel_types=("in_app", "telegram"),
    ),
    # prod-12 av_01 (ADR 0105): el backend antivirus lleva >N min inalcanzable —
    # la ingesta está en fail-closed acumulando `pending_scan`; el operador debe
    # levantar ClamAV (el sweep reescanea solo al volver).
    "antivirus_unreachable": EventSpec(
        "antivirus_unreachable",
        lane=NotificationLane.DEFAULT,
        default_channel_types=("in_app", "telegram"),
    ),
    "task_failed": EventSpec(
        "task_failed",
        lane=NotificationLane.DEFAULT,
        default_channel_types=("in_app",),
    ),
    "execution_finished": EventSpec(
        "execution_finished",
        lane=NotificationLane.DEFAULT,
        default_channel_types=("in_app",),
    ),
    "execution_failed": EventSpec(
        "execution_failed",
        lane=NotificationLane.PRIORITY,
        default_channel_types=("in_app", "telegram"),
    ),
    "review_requested": EventSpec(
        "review_requested",
        lane=NotificationLane.DEFAULT,
        default_channel_types=("in_app", "telegram"),
    ),
    "human_validation_needed": EventSpec(
        "human_validation_needed",
        lane=NotificationLane.PRIORITY,
        default_channel_types=("in_app", "telegram"),
    ),
    # Plan 16 task_16_05 — a human task was routed to a concrete User: the
    # orchestrator created a HumanTaskAssignment and moved the task to
    # assigned_to_human (NO runtime container). Time-sensitive (the user has
    # acceptance_timeout_hours to accept before escalation, task_16_06), so it
    # rides the priority lane, fanning out to the user's in-app + telegram
    # channels by default. The context carries task_title / project_name /
    # assigned_to (the human-readable assignee).
    "human_task_assigned": EventSpec(
        "human_task_assigned",
        lane=NotificationLane.PRIORITY,
        default_channel_types=("in_app", "telegram"),
    ),
    "review_escalated": EventSpec(
        "review_escalated",
        lane=NotificationLane.PRIORITY,
        default_channel_types=("in_app", "telegram"),
    ),
    "budget_alert": EventSpec(
        "budget_alert",
        lane=NotificationLane.PRIORITY,
        default_channel_types=("in_app", "email"),
    ),
    # Plan 11 task_11_21 — a tenant guardrail alert rule tripped (violations
    # crossed a threshold within a window). Time-sensitive (a security
    # signal), so it rides the priority lane, fanning out to the Tenant
    # Admins' in-app + email channels by default.
    "guardrail_alert": EventSpec(
        "guardrail_alert",
        lane=NotificationLane.PRIORITY,
        default_channel_types=("in_app", "email"),
    ),
    # Plan 14 task_14_10 — a benchmark's quality drifted (a SUSTAINED pass-rate
    # decline over the trailing window). A quality signal the Tenant Admins act
    # on; rides the priority lane, fanning out to in-app + email by default.
    "quality_drift_alert": EventSpec(
        "quality_drift_alert",
        lane=NotificationLane.PRIORITY,
        default_channel_types=("in_app", "email"),
    ),
    # Plan 14 task_14_13 — a tenant outlier alert rule tripped (an agent's
    # success rate / cost / latency deviated significantly from the tenant
    # norm over the window). A quality/cost signal the Tenant Admins act on;
    # rides the priority lane, fanning out to in-app + email by default.
    "agent_outlier_alert": EventSpec(
        "agent_outlier_alert",
        lane=NotificationLane.PRIORITY,
        default_channel_types=("in_app", "email"),
    ),
    # Plan 15 task_15_17 — the scheduled Vault credential-rotation cycle FAILED
    # (a static-secret rotation, a dynamic-credential issue, or a lease
    # renew/revoke did not complete). A platform-scoped (tenant_id=None) ops
    # signal a System Admin acts on; the rotation engine keeps the system up on
    # its current credentials but must alert. Rides the priority lane, fanning
    # out to in-app + email by default. The payload carries NO credential value
    # (only the audit's secret-free names / lease-ids / counts).
    "credential_rotation_failed": EventSpec(
        "credential_rotation_failed",
        lane=NotificationLane.PRIORITY,
        default_channel_types=("in_app", "email"),
    ),
    # Plan 11.1 task_11_1_02 — the scheduled exchange-rates fetch FAILED (the
    # source feed could not be fetched/parsed, or lacked a USD anchor). A
    # platform-scoped (tenant_id=None) ops signal a System Admin acts on; the
    # catalog keeps its last good rates (conversion falls back to the most-recent
    # prior rate) but the staleness must be surfaced. Rides the priority lane,
    # fanning out to in-app + email by default. The payload carries only the
    # source name + the non-leaky error string.
    "fx_fetch_failed": EventSpec(
        "fx_fetch_failed",
        lane=NotificationLane.PRIORITY,
        default_channel_types=("in_app", "email"),
    ),
}


def registry_event_types() -> frozenset[str]:
    """The notification event_types the registry knows how to dispatch."""
    return frozenset(EVENT_REGISTRY)


def lookup_event(event_type: str) -> EventSpec | None:
    """Return the :class:`EventSpec` for ``event_type``, or None if unknown.

    An unknown event is a no-op (the caller logs and drops it) — never an
    error, so a newly-emitted domain event the registry hasn't catalogued
    yet can't crash the dispatcher.
    """
    return EVENT_REGISTRY.get(event_type)


# =============================================================================
# The inbound event + the resolved plan.
# =============================================================================
@dataclass(frozen=True)
class IncomingEvent:
    """One domain event handed to the mapper. The JSON-safe Celery payload.

    ``tenant_id`` is None for a platform-scoped event (a System-Admin ops
    signal); otherwise it scopes the entire fan-out — only this tenant's
    (and platform-wide) channels are ever resolved. ``context`` is the
    template render context (plan_name, task_title, reason, …). ``locale``
    overrides the per-send locale; falls back to ``Settings.default_locale``.
    ``now`` is injectable so quiet-hours evaluation is deterministic in
    tests.
    """

    event_type: str
    tenant_id: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    locale: str | None = None
    now: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "tenant_id": self.tenant_id,
            "context": self.context,
            "locale": self.locale,
            # `now` is a test-only injection — not part of the wire payload.
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> IncomingEvent:
        return cls(
            event_type=raw["event_type"],
            tenant_id=raw.get("tenant_id"),
            context=dict(raw.get("context") or {}),
            locale=raw.get("locale"),
        )


class DispatchDecision(StrEnum):
    """What the resolver decided for one candidate channel."""

    SEND = "send"
    SUPPRESSED = "suppressed"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class ChannelDispatch:
    """One per-channel resolution outcome.

    For ``SEND`` / ``DEFERRED`` the ``send_request`` carries the rendered
    message ready to enqueue (``eta`` set for DEFERRED — the time past the
    quiet-hours window). For ``SUPPRESSED`` only ``reason`` is set.
    """

    channel_id: UUID
    channel_type: str
    decision: DispatchDecision
    lane: NotificationLane
    reason: str | None = None
    eta: datetime | None = None
    send_request: dict[str, Any] | None = None


@dataclass(frozen=True)
class DispatchPlan:
    """The full fan-out plan for one event — every candidate channel's
    decision. ``no_op`` is True when the event is unknown or had zero
    subscribers (nothing to enqueue)."""

    event_type: str
    tenant_id: str | None
    decisions: tuple[ChannelDispatch, ...] = ()
    no_op: bool = False
    note: str | None = None

    @property
    def to_send(self) -> tuple[ChannelDispatch, ...]:
        return tuple(
            d
            for d in self.decisions
            if d.decision in (DispatchDecision.SEND, DispatchDecision.DEFERRED)
        )


# =============================================================================
# Preference resolution (most-specific-wins: user → tenant → platform).
# =============================================================================
@dataclass(frozen=True)
class _Preference:
    """The fields of a NotificationPreference the resolver needs."""

    scope: str
    enabled: bool
    quiet_hours_start: int | None
    quiet_hours_end: int | None
    quiet_hours_tz: str | None


# Precedence of a preference scope; higher wins. A user-scoped override
# beats a tenant default beats a platform default.
_SCOPE_RANK: dict[str, int] = {"platform": 0, "tenant": 1, "user": 2}


def _most_specific(prefs: list[_Preference]) -> _Preference | None:
    """Pick the highest-precedence preference (user > tenant > platform)."""
    if not prefs:
        return None
    return max(prefs, key=lambda p: _SCOPE_RANK.get(p.scope, -1))


# =============================================================================
# Quiet-hours evaluation.
# =============================================================================
def _resolve_tz(tz_name: str | None) -> ZoneInfo:
    """Resolve the preference timezone; fall back to UTC on an unknown name."""
    if not tz_name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        _log.warning("notification_dispatcher.unknown_quiet_hours_tz", tz=tz_name)
        return ZoneInfo("UTC")


def quiet_hours_defer_until(
    pref: _Preference, *, now: datetime, max_defer_s: int
) -> datetime | None:
    """Return the UTC time a send must be deferred until, or None to send now.

    The window is ``[start, end)`` minutes-of-day in ``quiet_hours_tz``.
    Handles a wrap-around window (e.g. 22:00→07:00). When ``now`` is inside
    the window the send is deferred to the window's end; the ETA is clamped
    to ``max_defer_s`` so a misconfigured window can't push a send absurdly
    far. A half-configured window (only one bound set) means "no quiet
    hours".
    """
    start, end = pref.quiet_hours_start, pref.quiet_hours_end
    if start is None or end is None:
        return None
    # A zero-width window (start == end) is "no quiet hours" — never defers.
    if start == end:
        return None
    start %= _MINUTES_PER_DAY
    end %= _MINUTES_PER_DAY

    tz = _resolve_tz(pref.quiet_hours_tz)
    local_now = now.astimezone(tz)
    minute_of_day = local_now.hour * 60 + local_now.minute

    wraps = start > end  # window crosses midnight (e.g. 22:00 → 07:00)
    in_window = (
        (start <= minute_of_day < end)
        if not wraps
        else (minute_of_day >= start or minute_of_day < end)
    )
    if not in_window:
        return None

    end_time = time(hour=end // 60, minute=end % 60)
    candidate = local_now.replace(
        hour=end_time.hour, minute=end_time.minute, second=0, microsecond=0
    )
    # If the window end already passed today (wrap-around case where now is
    # past midnight but before end is handled above; here now >= start), the
    # window ends tomorrow.
    if candidate <= local_now:
        candidate = candidate + timedelta(days=1)

    deferred_utc = candidate.astimezone(UTC)
    max_eta = now.astimezone(UTC) + timedelta(seconds=max_defer_s)
    return min(deferred_utc, max_eta)


# =============================================================================
# The resolver — pure async, no Celery.
# =============================================================================
async def resolve_event_dispatch(
    event: IncomingEvent,
    *,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> DispatchPlan:
    """Resolve an event to its full per-channel dispatch plan.

    Pure with respect to Celery — looks up channels/preferences/templates
    under the event's tenant scope and returns the decisions. The Celery
    task :func:`dispatch_event` calls this then enqueues each SEND/DEFERRED.

    Tenant isolation: the session's ``app.tenant_id`` is set to the event's
    tenant, and every channel/preference query additionally filters
    ``tenant_id == event.tenant_id`` (or ``IS NULL`` for platform-wide), so
    a tenant-A event can never resolve a tenant-B channel even though the
    dispatcher is BYPASSRLS.
    """
    spec = lookup_event(event.event_type)
    if spec is None:
        _log.info("notification_dispatcher.event_unknown", event_type=event.event_type)
        return DispatchPlan(
            event_type=event.event_type,
            tenant_id=event.tenant_id,
            no_op=True,
            note=f"no registry entry for event {event.event_type!r}",
        )

    request_tenant = UUID(event.tenant_id) if event.tenant_id else None
    now = event.now or datetime.now(UTC)
    locale = event.locale or settings.default_locale

    async with sessionmaker() as session, session.begin():
        # Defensive tenant scoping (Plan 06.14 task_06_14_02): even though
        # the dispatcher is BYPASSRLS, scope any RLS-governed read to the
        # event's tenant. Platform event (NULL) → '' → NULLIF → NULL.
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(request_tenant) if request_tenant is not None else ""},
        )

        channels = await _load_channels(session, request_tenant)
        if not channels:
            return DispatchPlan(
                event_type=spec.notification_event_type,
                tenant_id=event.tenant_id,
                no_op=True,
                note="no enabled channels in scope",
            )

        prefs_by_channel_type = await _load_preferences(
            session, request_tenant, spec.notification_event_type
        )
        overrides = await _load_template_overrides(
            session, request_tenant, spec.notification_event_type, locale
        )

    decisions: list[ChannelDispatch] = []
    for ch in channels:
        decision = _decide_channel(
            channel=ch,
            spec=spec,
            event=event,
            locale=locale,
            now=now,
            settings=settings,
            preference=_most_specific(prefs_by_channel_type.get(ch["channel_type"], [])),
            override=overrides.get(ch["channel_type"]),
        )
        if decision is not None:
            decisions.append(decision)

    no_op = not any(
        d.decision in (DispatchDecision.SEND, DispatchDecision.DEFERRED) for d in decisions
    )
    return DispatchPlan(
        event_type=spec.notification_event_type,
        tenant_id=event.tenant_id,
        decisions=tuple(decisions),
        no_op=no_op,
        note=("no subscribed channels" if no_op else None),
    )


def _decide_channel(
    *,
    channel: dict[str, Any],
    spec: EventSpec,
    event: IncomingEvent,
    locale: str,
    now: datetime,
    settings: Settings,
    preference: _Preference | None,
    override: TemplateSource | None,
) -> ChannelDispatch | None:
    """Decide SEND / SUPPRESSED / DEFERRED for one candidate channel.

    Returns None when the channel is simply not subscribed (no explicit
    preference AND not in the event's default fan-out set) — that channel
    is skipped silently rather than recorded.
    """
    channel_type = channel["channel_type"]
    channel_id = channel["id"]

    has_pref = preference is not None
    is_default = channel_type in spec.default_channel_types

    # Not subscribed: no explicit preference and not in the default fan-out.
    if not has_pref and not is_default:
        return None

    # Opt-out: an explicit preference with enabled=false suppresses the send
    # on this channel (the human_10_02 "mute budget_alert on Slack" case).
    if preference is not None and not preference.enabled:
        return ChannelDispatch(
            channel_id=channel_id,
            channel_type=channel_type,
            decision=DispatchDecision.SUPPRESSED,
            lane=spec.lane,
            reason="opted out (preference.enabled=false)",
        )

    # Render the template now so a render failure suppresses THIS channel
    # rather than crashing the whole fan-out.
    try:
        rendered = render_notification(
            event_type=spec.notification_event_type,
            channel_type=channel_type,
            locale=locale,
            context=event.context,
            override=override,
        )
    except Exception as exc:  # TemplateRenderError + any unexpected
        _log.warning(
            "notification_dispatcher.render_failed",
            event_type=spec.notification_event_type,
            channel_type=channel_type,
            error=str(exc),
        )
        return ChannelDispatch(
            channel_id=channel_id,
            channel_type=channel_type,
            decision=DispatchDecision.SUPPRESSED,
            lane=spec.lane,
            reason=f"template render failed: {exc}",
        )

    send_request = {
        "channel_id": str(channel_id),
        "event_type": spec.notification_event_type,
        "tenant_id": event.tenant_id,
        "target": None,  # the dispatcher falls back to the channel config.
        "body": rendered.body,
        "structured": ({"subject": rendered.subject} if rendered.subject is not None else None),
    }

    # Quiet hours: defer (don't drop) — compute an ETA past the window.
    if preference is not None:
        defer_until = quiet_hours_defer_until(
            preference, now=now, max_defer_s=settings.quiet_hours_max_defer_s
        )
        if defer_until is not None:
            return ChannelDispatch(
                channel_id=channel_id,
                channel_type=channel_type,
                decision=DispatchDecision.DEFERRED,
                lane=spec.lane,
                reason="within quiet hours",
                eta=defer_until,
                send_request=send_request,
            )

    return ChannelDispatch(
        channel_id=channel_id,
        channel_type=channel_type,
        decision=DispatchDecision.SEND,
        lane=spec.lane,
        send_request=send_request,
    )


# =============================================================================
# DB loaders — every query is tenant-scoped (or NULL platform-wide).
# =============================================================================
async def _load_channels(session: AsyncSession, tenant_id: UUID | None) -> list[dict[str, Any]]:
    """Load the enabled, live channels in scope for the event's tenant.

    Returns the tenant's own channels PLUS the platform-wide (NULL-tenant)
    channels — a System Admin's ops channel applies to everyone. A
    tenant-B channel is NEVER loaded for a tenant-A event.
    """
    from api_server.db.notification import NotificationChannel

    stmt = select(
        NotificationChannel.id,
        NotificationChannel.channel_type,
        NotificationChannel.tenant_id,
        NotificationChannel.scope,
    ).where(
        NotificationChannel.enabled.is_(True),
        NotificationChannel.deleted_at.is_(None),
    )
    if tenant_id is not None:
        stmt = stmt.where(
            (NotificationChannel.tenant_id == tenant_id) | (NotificationChannel.tenant_id.is_(None))
        )
    else:
        stmt = stmt.where(NotificationChannel.tenant_id.is_(None))

    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": r.id,
            "channel_type": r.channel_type,
            "tenant_id": r.tenant_id,
            "scope": r.scope,
        }
        for r in rows
    ]


async def _load_preferences(
    session: AsyncSession, tenant_id: UUID | None, event_type: str
) -> dict[str, list[_Preference]]:
    """Load the live preferences for ``event_type`` in scope, grouped by
    channel_type.

    Includes the tenant's own rows + the platform-wide (NULL) defaults so
    the resolver can pick most-specific-wins per channel_type. Never loads
    another tenant's preferences.
    """
    from api_server.db.notification import NotificationPreference

    stmt = select(
        NotificationPreference.scope,
        NotificationPreference.channel_type,
        NotificationPreference.enabled,
        NotificationPreference.quiet_hours_start,
        NotificationPreference.quiet_hours_end,
        NotificationPreference.quiet_hours_tz,
    ).where(
        NotificationPreference.event_type == event_type,
        NotificationPreference.deleted_at.is_(None),
    )
    if tenant_id is not None:
        stmt = stmt.where(
            (NotificationPreference.tenant_id == tenant_id)
            | (NotificationPreference.tenant_id.is_(None))
        )
    else:
        stmt = stmt.where(NotificationPreference.tenant_id.is_(None))

    grouped: dict[str, list[_Preference]] = {}
    for r in (await session.execute(stmt)).all():
        grouped.setdefault(r.channel_type, []).append(
            _Preference(
                scope=r.scope,
                enabled=r.enabled,
                quiet_hours_start=r.quiet_hours_start,
                quiet_hours_end=r.quiet_hours_end,
                quiet_hours_tz=r.quiet_hours_tz,
            )
        )
    return grouped


async def _load_template_overrides(
    session: AsyncSession, tenant_id: UUID | None, event_type: str, locale: str
) -> dict[str, TemplateSource]:
    """Load any live tenant template overrides for ``(event_type, locale)``,
    keyed by channel_type.

    Platform-scoped events (NULL tenant) have no tenant overrides — only
    the builtin templates apply, so this returns empty.
    """
    if tenant_id is None:
        return {}
    from api_server.db.notification import NotificationTemplate

    stmt = select(
        NotificationTemplate.channel_type,
        NotificationTemplate.body_template,
        NotificationTemplate.subject_template,
    ).where(
        NotificationTemplate.tenant_id == tenant_id,
        NotificationTemplate.event_type == event_type,
        NotificationTemplate.locale == locale,
        NotificationTemplate.deleted_at.is_(None),
    )
    overrides: dict[str, TemplateSource] = {}
    for r in (await session.execute(stmt)).all():
        overrides[r.channel_type] = template_source_from_row(r)
    return overrides


# =============================================================================
# Lane → queue name (operator-tunable, never hardcoded).
# =============================================================================
def lane_queue(lane: NotificationLane, settings: Settings) -> str:
    """Resolve a semantic lane to the concrete operator-tunable queue name."""
    if lane is NotificationLane.PRIORITY:
        return settings.priority_queue
    return settings.default_queue


__all__ = [
    "EVENT_REGISTRY",
    "ChannelDispatch",
    "DispatchDecision",
    "DispatchPlan",
    "EventSpec",
    "IncomingEvent",
    "NotificationLane",
    "lane_queue",
    "lookup_event",
    "quiet_hours_defer_until",
    "registry_event_types",
    "resolve_event_dispatch",
]
