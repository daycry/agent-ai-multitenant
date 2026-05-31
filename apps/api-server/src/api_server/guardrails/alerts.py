"""Tenant guardrail ALERT-RULE evaluator + dispatch (Plan 11 Fase E, task_11_21).

The trigger side of the configurable guardrail alerts. A Tenant Admin
defines :class:`~api_server.db.guardrail_alert_rule.GuardrailAlertRule`
rows ("alert me when guardrail violations cross THRESHOLD within
WINDOW_SECONDS", optionally scoped to a ``guardrail_type`` and/or a
``min_severity``). This module evaluates those rules against the tenant's
own ``guardrail_events`` (task_11_20) and, when a rule's count crosses its
threshold, fires ONE alert through the Plan 10 notification system.

Flow (`evaluate_tenant_alert_rules`), all on a TENANT-SCOPED RLS session so
tenant A's violations can NEVER alert tenant B:

  1. Load the tenant's enabled, live rules.
  2. For each rule, count matching ``guardrail_events`` in the trailing
     ``window_seconds`` (filtered by the rule's optional ``guardrail_type``
     and ``min_severity``).
  3. **Threshold**: a rule fires only when ``count >= threshold``.
  4. **Debounce**: a rule that already fired within the last
     ``window_seconds`` (``last_fired_at``) is suppressed — at most ONE
     alert per rule per window, so a sustained breach does not spam.
  5. For each firing rule, dispatch a ``guardrail_alert`` event via the
     Plan 10 notifier (an event → notification to the tenant's Tenant
     Admins' subscribed channels) and stamp ``last_fired_at = now`` so the
     debounce window restarts.

Dispatch goes THROUGH the Plan 10 system (not a parallel notifier): the
default :class:`AlertDispatcher` enqueues
``notification_dispatcher.dispatch_event`` via
:func:`api_server.celery_client.enqueue_event_dispatch`, which resolves the
tenant's channels / Tenant-Admin preferences, renders the
``guardrail_alert`` template, and sends. Tests inject a fake dispatcher to
assert the alert was enqueued without a live broker / channel.

Wiring: :func:`maybe_alert_after_events` is the seam the event recorder
calls right after persisting one or more events, so a spike is observed at
record time. The evaluation is also safe to run periodically (it is
idempotent given the debounce). ``now`` is injectable for deterministic
tests; every tunable is a named constant / rule field — no magic numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import structlog
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.guardrail_alert_rule import (
    GuardrailAlertRule,
    severities_at_or_above,
)
from api_server.db.guardrail_event import GuardrailEvent

_log = structlog.get_logger("api_server.guardrails.alerts")

# The notification event_type the Plan 10 dispatcher maps a fired alert to
# (registered in the dispatcher's EVENT_REGISTRY + builtin templates). A
# named constant, not an inline literal.
GUARDRAIL_ALERT_EVENT_TYPE = "guardrail_alert"


@dataclass(frozen=True)
class AlertFiring:
    """The record of one rule that fired during an evaluation pass.

    Returned so the caller / tests can assert which rules fired with what
    count without inspecting the broker. ``dispatched`` is True when the
    alert was successfully handed to the Plan 10 notifier.
    """

    rule_id: UUID
    rule_name: str
    count: int
    threshold: int
    window_seconds: int
    guardrail_type: str | None
    dispatched: bool


@dataclass
class AlertEvaluationResult:
    """The outcome of evaluating all of a tenant's alert rules once."""

    tenant_id: UUID
    fired: list[AlertFiring] = field(default_factory=list)
    # Rules that crossed the threshold but were SUPPRESSED by the debounce
    # (already fired within the current window). Surfaced for observability.
    suppressed_rule_ids: list[UUID] = field(default_factory=list)
    evaluated: int = 0


class AlertDispatcher(Protocol):
    """The seam through which a fired alert reaches the Plan 10 notifier.

    Implementations enqueue a ``guardrail_alert`` event for the tenant; the
    notification-dispatcher resolves the tenant's channels / Tenant-Admin
    preferences and sends. Tests inject a fake to assert the enqueue
    without a live broker. Returns True iff the event was accepted.
    """

    async def dispatch(self, event: dict[str, object]) -> bool: ...  # pragma: no cover - protocol


class CeleryAlertDispatcher:
    """Default dispatcher: enqueue the event onto the Plan 10 dispatcher lane.

    Goes THROUGH the Plan 10 notification system — it produces the
    ``notification_dispatcher.dispatch_event`` task by name (the api-server
    never imports the dispatcher package). The dispatcher then fans the
    event out to the tenant's Tenant Admins' subscribed channels.
    """

    async def dispatch(self, event: dict[str, object]) -> bool:
        # Imported lazily so importing this module does not pull the Celery
        # producer (and its broker config) into every consumer.
        from api_server.celery_client import enqueue_event_dispatch

        return await enqueue_event_dispatch(event)


def _build_alert_event(rule: GuardrailAlertRule, *, count: int) -> dict[str, object]:
    """Build the JSON-safe ``guardrail_alert`` event payload for the notifier.

    Carries ONLY non-sensitive metadata into the template context (the rule
    name, the matched type, the count / threshold / window) — never any
    masked event detail, let alone raw content.
    """
    return {
        "event_type": GUARDRAIL_ALERT_EVENT_TYPE,
        "tenant_id": str(rule.tenant_id),
        "context": {
            "rule_name": rule.name,
            "count": count,
            "threshold": rule.threshold,
            "window_seconds": rule.window_seconds,
            "guardrail_type": rule.guardrail_type or "",
            "min_severity": rule.min_severity or "",
        },
    }


def _match_filters(
    rule: GuardrailAlertRule, *, tenant_id: UUID, since: datetime
) -> list[ColumnElement[bool]]:
    """Build the event-count predicate for a rule's window + optional scoping.

    The ``tenant_id`` equality is defence in depth on top of RLS. The
    ``min_severity`` filter expands to ``severity IN (<at-or-above>)`` on the
    engine's ordered scale.
    """
    filters: list[ColumnElement[bool]] = [
        GuardrailEvent.tenant_id == tenant_id,
        GuardrailEvent.created_at >= since,
    ]
    if rule.guardrail_type is not None:
        filters.append(GuardrailEvent.guardrail_type == rule.guardrail_type)
    if rule.min_severity is not None:
        filters.append(GuardrailEvent.severity.in_(severities_at_or_above(rule.min_severity)))
    return filters


async def _count_matching_events(
    session: AsyncSession,
    rule: GuardrailAlertRule,
    *,
    tenant_id: UUID,
    now: datetime,
) -> int:
    """Count this tenant's matching events in the rule's trailing window."""
    since = now - timedelta(seconds=rule.window_seconds)
    stmt = (
        select(func.count())
        .select_from(GuardrailEvent)
        .where(*_match_filters(rule, tenant_id=tenant_id, since=since))
    )
    return int((await session.execute(stmt)).scalar_one())


def _is_debounced(rule: GuardrailAlertRule, *, now: datetime) -> bool:
    """True when the rule already fired within the current window.

    The debounce window equals the rule's own ``window_seconds`` measured
    from ``last_fired_at``: a rule may fire again only once a full window
    has elapsed since its last alert, so a sustained breach yields at most
    one alert per rule per window.
    """
    if rule.last_fired_at is None:
        return False
    last = rule.last_fired_at
    if last.tzinfo is None:  # defensive: treat a naive timestamp as UTC
        last = last.replace(tzinfo=UTC)
    return now - last < timedelta(seconds=rule.window_seconds)


async def evaluate_tenant_alert_rules(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    dispatcher: AlertDispatcher | None = None,
    now: datetime | None = None,
) -> AlertEvaluationResult:
    """Evaluate all of a tenant's enabled alert rules once and fire any breach.

    Runs on a TENANT-SCOPED RLS session (the caller's
    ``open_tenant_session`` / request session): both the event count and the
    rule load/update see ONLY this tenant's rows, so tenant A's violations
    can never alert tenant B. For each enabled, live rule whose matching
    event count in the trailing window crosses ``threshold`` AND is not
    debounced, dispatch a ``guardrail_alert`` event via the Plan 10 notifier
    and stamp ``last_fired_at``.

    The caller owns the transaction — the ``last_fired_at`` update is flushed
    but not committed here (it commits atomically with the events that
    produced it when the caller commits). Returns the per-rule outcome.
    """
    now = now or datetime.now(tz=UTC)
    dispatcher = dispatcher or CeleryAlertDispatcher()

    rules = (
        (
            await session.execute(
                select(GuardrailAlertRule).where(
                    GuardrailAlertRule.tenant_id == tenant_id,
                    GuardrailAlertRule.enabled.is_(True),
                    GuardrailAlertRule.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    result = AlertEvaluationResult(tenant_id=tenant_id, evaluated=len(rules))

    for rule in rules:
        count = await _count_matching_events(session, rule, tenant_id=tenant_id, now=now)
        if count < rule.threshold:
            continue
        if _is_debounced(rule, now=now):
            result.suppressed_rule_ids.append(rule.id)
            _log.info(
                "guardrail_alert.debounced",
                tenant_id=str(tenant_id),
                rule_id=str(rule.id),
                count=count,
                threshold=rule.threshold,
            )
            continue

        # Stamp the debounce anchor BEFORE awaiting the dispatch so a
        # concurrent evaluation in the same window cannot double-fire (the
        # row update is row-locked in this transaction).
        rule.last_fired_at = now
        await session.flush()

        dispatched = await dispatcher.dispatch(_build_alert_event(rule, count=count))
        result.fired.append(
            AlertFiring(
                rule_id=rule.id,
                rule_name=rule.name,
                count=count,
                threshold=rule.threshold,
                window_seconds=rule.window_seconds,
                guardrail_type=rule.guardrail_type,
                dispatched=dispatched,
            )
        )
        _log.info(
            "guardrail_alert.fired",
            tenant_id=str(tenant_id),
            rule_id=str(rule.id),
            rule_name=rule.name,
            count=count,
            threshold=rule.threshold,
            dispatched=dispatched,
        )

    return result


async def maybe_alert_after_events(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    dispatcher: AlertDispatcher | None = None,
    now: datetime | None = None,
) -> AlertEvaluationResult:
    """Evaluate alert rules right after the host recorded guardrail events.

    The seam the recorder host calls once it has persisted one or more
    ``guardrail_events`` rows for ``tenant_id`` (so a spike is observed at
    record time). A thin wrapper over :func:`evaluate_tenant_alert_rules`
    that never raises into the recording path — alerting is best-effort
    observability layered on top of the event write, so a failure here must
    not roll back the event the host just recorded.
    """
    try:
        return await evaluate_tenant_alert_rules(
            session, tenant_id=tenant_id, dispatcher=dispatcher, now=now
        )
    except Exception as exc:  # pragma: no cover - defensive best-effort
        _log.warning(
            "guardrail_alert.evaluation_failed",
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        return AlertEvaluationResult(tenant_id=tenant_id)


__all__ = [
    "AlertDispatcher",
    "AlertEvaluationResult",
    "AlertFiring",
    "CeleryAlertDispatcher",
    "GUARDRAIL_ALERT_EVENT_TYPE",
    "evaluate_tenant_alert_rules",
    "maybe_alert_after_events",
]
