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
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class InboxAction(StrEnum):
    """The four contextual actions the assignee can take on a task.

    - ``ACCEPT``: ``pending_acceptance`` assignment -> ``accepted``; Task
      ``assigned_to_human -> in_progress`` (§7.2, human assignee).
    - ``REJECT``: the assignee declines with a justification; the assignment
      goes ``declined`` and the Task ``assigned_to_human -> blocked`` so a
      Tenant Admin can re-route it (the justification is audited).
    - ``COMPLETE``: the assignee submits the delivery form (output text +
      attachments + optional logged hours, task_16_09); a HumanWorkSession is
      created and the Task moves ``in_progress -> in_review``.
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


class AttachmentKind(StrEnum):
    """The kind of deliverable a human attached to a submission (task_16_09).

    - ``FILE``: an uploaded artefact, referenced by ``ref`` (e.g. an object-store
      key / path). The MVP stores only the *reference*, not the bytes.
    - ``URL``: an external link (a PR, a doc, a screenshot host).
    - ``SCREENSHOT``: a captured image, referenced by ``ref`` like a file.
    """

    FILE = "file"
    URL = "url"
    SCREENSHOT = "screenshot"


class SubmitAttachment(BaseModel):
    """One deliverable descriptor stored in ``output_files_attached`` (JSONB).

    The shape is intentionally light: a ``kind`` plus a human ``label`` plus
    EITHER a ``url`` (for ``url`` kind) OR a ``ref`` (an object-store key / path
    for ``file`` / ``screenshot`` kinds). The MVP does NOT upload bytes here —
    it records references the assignee provides — so the schema stays
    migration-free and the storage backend can evolve independently.
    """

    model_config = _BASE_CONFIG

    kind: AttachmentKind
    label: str = Field(min_length=1, max_length=300)
    url: str | None = Field(default=None, max_length=2000)
    ref: str | None = Field(default=None, max_length=2000)

    @field_validator("url", "ref", mode="after")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """An empty/whitespace string is treated as absent."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def has_target(self) -> bool:
        """A usable attachment names at least a ``url`` or a ``ref``."""
        return bool(self.url) or bool(self.ref)


class InboxSubmitRequest(BaseModel):
    """Delivery form body for ``POST /inbox/assignments/{id}/complete`` (task_16_09).

    The assignee marks an accepted task complete with their deliverable:

    - ``output`` — the free-form output text (what they did / the result). Maps
      to the ``HumanWorkSession.comments`` column. Optional but the form
      encourages it; the modal disables submit until there is output OR an
      attachment.
    - ``attachments`` — files / URLs / screenshots (references), stored in
      ``HumanWorkSession.output_files_attached`` (JSONB).
    - ``hours_worked`` — OPTIONAL logged hours. Feeds coste humano (Fase D);
      ``None`` means the human did not log hours. Non-negative, 2 decimals.

    Backwards-compatible with the task_16_08 lightweight body: a caller posting
    just ``{ "comments": "…" }`` still works — ``comments`` is accepted as an
    alias-of-last-resort for ``output`` when ``output`` is absent.
    """

    model_config = _BASE_CONFIG

    output: str | None = Field(default=None, max_length=20000)
    comments: str | None = Field(default=None, max_length=20000)
    attachments: list[SubmitAttachment] = Field(default_factory=list, max_length=50)
    hours_worked: Decimal | None = Field(default=None, ge=0, max_digits=8, decimal_places=2)

    def output_text(self) -> str | None:
        """The effective output text — ``output`` if set, else ``comments``."""
        for candidate in (self.output, self.comments):
            if candidate is not None and candidate.strip():
                return candidate.strip()
        return None

    def usable_attachments(self) -> list[SubmitAttachment]:
        """Attachments that actually point at something (url or ref present)."""
        return [a for a in self.attachments if a.has_target()]


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


class InboxSubmitResult(InboxActionResult):
    """The outcome of the delivery-form submit (task_16_09).

    Extends :class:`InboxActionResult` with the id of the
    :class:`~api_server.db.domain.HumanWorkSession` the submission created and a
    count of the deliverables that were recorded, so the UI can confirm the
    work session was persisted.
    """

    work_session_id: UUID
    attachments_count: int


__all__ = [
    "AttachmentKind",
    "InboxAction",
    "InboxActionRequest",
    "InboxActionResult",
    "InboxAssignmentResponse",
    "InboxSubmitRequest",
    "InboxSubmitResult",
    "SubmitAttachment",
]
