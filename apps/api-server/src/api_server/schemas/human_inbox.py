"""Pydantic schemas for the personal inbox (Plan 16 task_16_08).

The inbox is the "Tareas asignadas a mí" tray: the CALLER user's OWN active
:class:`~api_server.db.domain.HumanTaskAssignment` rows folded with the Task /
project / plan context they need to act on, plus the four contextual actions
(accept, reject-with-justification, mark complete, escalate to admin).

A user sees and acts ONLY on their own assignments — the router filters every
read/write on ``assigned_to_user_id == principal.user_id`` ON TOP of RLS, so a
forged cross-tenant or someone-else's assignment id resolves to 404 (task_16_08
NON-NEGOTIABLE multi-tenancy + per-user scoping).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class InboxAction(StrEnum):
    """The four contextual actions the assignee can take on a task.

    - ``ACCEPT``: ``pending_acceptance`` assignment -> ``accepted``; Task
      ``assigned_to_human -> in_progress`` (§7.2, human assignee).
    - ``REJECT``: the assignee declines with a justification; the assignment
      goes ``declined`` and the Task ``assigned_to_human -> blocked`` so a
      Tenant Admin can re-route it (the justification is audited).
    - ``COMPLETE``: the assignee submits work; Task ``in_progress -> in_review``
      (the full delivery form — attachments + logged hours — lands in
      task_16_09; this MVP records the transition + an audit event).
    - ``ESCALATE``: the assignee hands the task to the Tenant Admin without
      doing it; the assignment goes ``declined``, the Task moves to ``blocked``
      and a ``task_blocked`` notification fans out to the tenant's admins.
    """

    ACCEPT = "accept"
    REJECT = "reject"
    COMPLETE = "complete"
    ESCALATE = "escalate"


class InboxActionRequest(BaseModel):
    """Body for an inbox action.

    ``justification`` is REQUIRED for ``reject`` (the plan calls for a reason)
    and optional for the rest. ``comments`` carries the assignee's free-form
    note on ``complete`` (the lightweight precursor to the task_16_09 form).
    """

    model_config = _BASE_CONFIG

    justification: str | None = Field(default=None, max_length=4000)
    comments: str | None = Field(default=None, max_length=4000)


class InboxAssignmentResponse(BaseModel):
    """One of the caller's active assignments, with the context to act on it.

    ``task_status`` is the live Task §7.2 status (assigned_to_human / in_progress
    / in_review); ``assignment_status`` is the accept-cycle status
    (pending_acceptance / accepted). ``acceptance_deadline`` is the moment the
    acceptance window lapses for a ``pending_acceptance`` row (assigned_at +
    the Human Agent's acceptance_timeout_hours) — the inbox's actionable
    "deadline"; ``None`` once the task is accepted.
    """

    model_config = _BASE_CONFIG

    assignment_id: UUID
    task_id: UUID
    human_agent_id: UUID | None
    assignment_status: str
    task_status: str
    assigned_at: datetime
    acceptance_deadline: datetime | None

    task_title: str
    task_description: str | None
    project_id: UUID
    project_name: str | None
    plan_id: UUID | None
    plan_title: str | None


class InboxActionResult(BaseModel):
    """The outcome of an inbox action — the new assignment + task status."""

    model_config = _BASE_CONFIG

    assignment_id: UUID
    task_id: UUID
    action: str
    assignment_status: str
    task_status: str


__all__ = [
    "InboxAction",
    "InboxActionRequest",
    "InboxActionResult",
    "InboxAssignmentResponse",
]
