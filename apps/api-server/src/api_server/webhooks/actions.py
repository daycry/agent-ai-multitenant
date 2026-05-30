"""Execute a resolved webhook action against the domain (Plan 13 task_13_10).

The DB-touching half of the mapping phase: :mod:`api_server.webhooks.mapping`
DECIDES (pure) which action a verified, normalised incoming event triggers and
renders its templates; THIS module EXECUTES that :class:`ResolvedAction` against
the existing task / comment / escalation domain, IN the config's own
tenant/project.

The three actions (the closed :class:`WebhookActionKind` set):

  * ``create_task``  -> INSERT a ``tasks`` row in the config's project (status
    ``backlog``, priority ``medium``), titled/bodied from the rendered
    templates. Returns the new task id.
  * ``comment``      -> APPEND a ``task_audit_events`` row (``kind='comment'``)
    on the rule's ``target_task_id``. Mirrors the append-only audit trail the
    task lifecycle already uses; never mutates the task itself.
  * ``escalate``     -> APPEND a ``task_audit_events`` row
    (``kind='escalation'``) AND flip the target task to ``blocked`` (a human
    must now act) — the same terminal-ish state the lifecycle's
    ``escalate_if_exhausted`` lands on.

Multi-tenancy (CLAUDE.md principle 1): every statement runs on a session whose
``app.tenant_id`` GUC is ALREADY bound to the config's tenant by the caller
(:mod:`api_server.routers.incoming_webhooks`), so RLS scopes the INSERT/UPDATE
to that tenant — an event for project A can never write a task into tenant B.
``create_task`` writes the config's ``tenant_id`` + ``project_id`` explicitly
(belt-and-braces with the RLS ``WITH CHECK``). ``comment`` / ``escalate`` target
a task by id but are still RLS-fenced: a ``target_task_id`` that belongs to
ANOTHER tenant is invisible under RLS, so the UPDATE/lookup matches zero rows
and the action is a safe no-op (reported via :class:`MissingTargetTaskError`).

Idempotency: this executor is invoked by the endpoint ONLY on the
first successful insert of the event row (a redelivery collides on the
``(config_id, delivery_id)`` UNIQUE and never reaches here), so a redelivered
webhook never creates a duplicate task — the dedup is the event row's, the
action simply rides its transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.webhooks.mapping import ResolvedAction, WebhookActionKind

# Title column is String(200); keep the rendered title within it so a long
# templated title never overflows the column (truncation is preferable to a
# failed inbound event).
_TASK_TITLE_MAX = 200

# How the actor of a webhook-driven audit event is recorded (mirrors the
# task_lifecycle "actor" convention: "system:plan_runner", "agent:reviewer").
_AUDIT_ACTOR = "system:incoming_webhook"


class MissingTargetTaskError(Exception):
    """Raised when a comment/escalate target task is not found IN this tenant.

    A ``target_task_id`` that does not exist, or belongs to another tenant (so
    RLS hides it), matches zero rows. Surfaced as a typed error so the caller
    records the event but reports the action as a no-op rather than silently
    succeeding. Carries the id (a UUID, not a secret) for the log only.
    """

    def __init__(self, target_task_id: str) -> None:
        super().__init__(f"target task {target_task_id} not found in this tenant")
        self.target_task_id = target_task_id


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Outcome of :func:`execute_action` — the action kind + the affected id.

    ``task_id`` is the created task (``create_task``) or the targeted task
    (``comment`` / ``escalate``). ``audit_event_id`` is the appended
    ``task_audit_events`` row for ``comment`` / ``escalate`` (None for
    ``create_task``).
    """

    kind: WebhookActionKind
    task_id: UUID
    audit_event_id: UUID | None = None


async def execute_action(
    session: AsyncSession,
    *,
    action: ResolvedAction,
    tenant_id: UUID,
    project_id: UUID,
) -> ActionResult:
    """Execute one :class:`ResolvedAction` on an ALREADY tenant-scoped session.

    Pre-conditions (the caller, the incoming-webhook endpoint, guarantees):

      * the session's ``app.tenant_id`` GUC is bound to ``tenant_id`` (so RLS
        scopes every statement here), and
      * this runs inside the same transaction that inserts the event row, so
        the action commits atomically with the event (idempotent redelivery is
        the event row's UNIQUE, not this function's concern).

    Dispatches on ``action.kind``. Raises :class:`MissingTargetTaskError` when a
    ``comment`` / ``escalate`` target task is not visible in this tenant.
    """
    if action.kind is WebhookActionKind.CREATE_TASK:
        return await _create_task(
            session, action=action, tenant_id=tenant_id, project_id=project_id
        )
    if action.kind is WebhookActionKind.COMMENT:
        return await _comment_on_task(session, action=action)
    return await _escalate_task(session, action=action)


async def _create_task(
    session: AsyncSession,
    *,
    action: ResolvedAction,
    tenant_id: UUID,
    project_id: UUID,
) -> ActionResult:
    """INSERT a backlog task in the config's project from the rendered templates.

    Writes ``tenant_id`` + ``project_id`` explicitly; the RLS ``WITH CHECK``
    additionally guarantees the row's ``tenant_id`` equals the bound GUC, so a
    cross-tenant write is impossible even if the caller passed a foreign id.
    """
    task_id = uuid7()
    title = (action.title or "Incoming webhook event")[:_TASK_TITLE_MAX]
    await session.execute(
        text(
            "INSERT INTO tasks "
            "(id, tenant_id, project_id, title, description, status, priority) "
            "VALUES (:id, :tid, :pid, :title, :description, 'backlog', 'medium')"
        ),
        {
            "id": str(task_id),
            "tid": str(tenant_id),
            "pid": str(project_id),
            "title": title,
            "description": action.body or None,
        },
    )
    return ActionResult(kind=WebhookActionKind.CREATE_TASK, task_id=task_id)


async def _require_target_task(session: AsyncSession, target_task_id: str) -> UUID:
    """Resolve a target task id under RLS, or raise MissingTargetTaskError.

    The SELECT is RLS-fenced: a task in another tenant is invisible, so a
    cross-tenant ``target_task_id`` returns no row and is rejected — the action
    can only ever touch a task in the config's own tenant.
    """
    result = await session.execute(
        text("SELECT id FROM tasks WHERE id = :id"),
        {"id": target_task_id},
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise MissingTargetTaskError(target_task_id)
    return row if isinstance(row, UUID) else UUID(str(row))


async def _append_audit(
    session: AsyncSession,
    *,
    task_id: UUID,
    tenant_id: UUID,
    kind: str,
    payload_json: str,
) -> UUID:
    """Append one ``task_audit_events`` row (the comment / escalation trail)."""
    audit_id = uuid7()
    await session.execute(
        text(
            "INSERT INTO task_audit_events "
            "(id, tenant_id, task_id, at, kind, actor, payload) "
            "VALUES (:id, :tid, :task_id, :at, :kind, :actor, CAST(:payload AS jsonb))"
        ),
        {
            "id": str(audit_id),
            "tid": str(tenant_id),
            "task_id": str(task_id),
            "at": datetime.now(tz=UTC),
            "kind": kind,
            "actor": _AUDIT_ACTOR,
            "payload": payload_json,
        },
    )
    return audit_id


async def _comment_on_task(session: AsyncSession, *, action: ResolvedAction) -> ActionResult:
    """APPEND a ``kind='comment'`` audit event on the target task (RLS-scoped)."""
    assert action.target_task_id is not None  # guaranteed by resolve_action
    task_id = await _require_target_task(session, action.target_task_id)
    tenant_id = await _current_tenant(session)
    payload = json.dumps({"source": "incoming_webhook", "body": action.body, "title": action.title})
    audit_id = await _append_audit(
        session, task_id=task_id, tenant_id=tenant_id, kind="comment", payload_json=payload
    )
    return ActionResult(kind=WebhookActionKind.COMMENT, task_id=task_id, audit_event_id=audit_id)


async def _escalate_task(session: AsyncSession, *, action: ResolvedAction) -> ActionResult:
    """Flip the target task to ``blocked`` + APPEND a ``kind='escalation'`` event."""
    assert action.target_task_id is not None  # guaranteed by resolve_action
    task_id = await _require_target_task(session, action.target_task_id)
    tenant_id = await _current_tenant(session)
    # Move the task to blocked — a human now owns it (mirrors the lifecycle's
    # escalation landing state). RLS already fenced the lookup to this tenant.
    await session.execute(
        text("UPDATE tasks SET status = 'blocked', updated_at = :now WHERE id = :id"),
        {"now": datetime.now(tz=UTC), "id": str(task_id)},
    )
    payload = json.dumps(
        {"source": "incoming_webhook", "reason": action.body or action.title, "escalated": True}
    )
    audit_id = await _append_audit(
        session, task_id=task_id, tenant_id=tenant_id, kind="escalation", payload_json=payload
    )
    return ActionResult(kind=WebhookActionKind.ESCALATE, task_id=task_id, audit_event_id=audit_id)


async def _current_tenant(session: AsyncSession) -> UUID:
    """Read back the session's bound ``app.tenant_id`` GUC (the action's tenant).

    The audit rows carry ``tenant_id`` explicitly; reading the GUC the caller
    set keeps us from threading the tenant through every helper while still
    being RLS-consistent (the value the policy enforces).
    """
    result = await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
    value = result.scalar_one()
    return UUID(str(value))


__all__ = [
    "ActionResult",
    "MissingTargetTaskError",
    "execute_action",
]
