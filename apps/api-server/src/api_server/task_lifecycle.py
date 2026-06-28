"""Task lifecycle transitions + audit log (Plan 06 Fase G2).

Six tasks of Fase G2 live in this module — they all revolve around
the *task* as the unit of audit:

  * :meth:`TaskLifecycle.reject_review` (06_34b1) — auto-review
    rejection sends the task back to ``backlog`` with a structured
    comment and increments ``retry_count``.
  * :meth:`TaskLifecycle.escalate_if_exhausted` (06_34b2) — after
    ``retry_count >= max_review_retries`` the task transitions to
    ``blocked`` (the canonical human-escalation state at TASK level,
    consistent with ``reviewer_bridge.apply_reviewer_verdict`` and
    CLAUDE.md ppio 7) and a notification fires.
  * The escalated-tasks panel (06_34b3) consumes
    :meth:`TaskLifecycle.list_escalated`.
  * :meth:`TaskLifecycle.create_task_from_checkbox` (06_34b4) — a
    failed human checkbox spawns a new plan-scoped task in
    ``backlog`` and the plan returns to ``in_progress``.
  * :meth:`TaskLifecycle.create_free_task` (06_34b5) — same as
    above but with reviewer-provided title/description, not bound
    to any checkbox.
  * :meth:`TaskLifecycle.history` (06_34b6) — append-only timeline
    of every event on a task.

The class is in-process and DB-agnostic — production wires it to
SQLAlchemy sessions, tests pass an in-memory ``TaskStore``.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import structlog

_log = structlog.get_logger("api_server.task_lifecycle")

# Hard cap per task. Plan 06 section 7.9 default; per-tenant override
# lives on Settings (a follow-up task).
DEFAULT_MAX_REVIEW_RETRIES = 3

# Subset of the canonical :class:`api_server.db.domain.TaskStatus` values
# this in-process module touches. Every member MUST exist in that enum —
# there is no orphan ``awaiting_human`` (F43): a review-exhausted task
# escalates to ``blocked`` (the canonical human-escalation state at TASK
# level), NOT to a status the DB/state-machine has never heard of.
TaskStatus = Literal[
    "backlog",
    "in_progress",
    "in_review",
    "done",
    "blocked",
    "cancelled",
]

HumanAction = Literal[
    "approve_manual",
    "reassign_with_guidance",
    "block_with_reason",
    "cancel",
]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass
class TaskRecord:
    """Mutable per-task state. Production maps this to a SQL row."""

    id: str
    plan_id: str
    title: str
    description: str
    status: TaskStatus = "backlog"
    retry_count: int = 0
    max_retries: int = DEFAULT_MAX_REVIEW_RETRIES
    manual_approval: bool = False
    parent_checkbox_id: str | None = None
    """Set when the task was created from a failed human checkbox."""
    is_free_task: bool = False
    """Set when the task was created via "Añadir tarea libre"."""


@dataclass(frozen=True)
class ReviewComment:
    """Structured rejection comment from the auto-reviewer."""

    failed_criterion: str
    testreport_evidence: str
    what_to_fix: str

    def to_dict(self) -> dict[str, str]:
        return {
            "failed_criterion": self.failed_criterion,
            "testreport_evidence": self.testreport_evidence,
            "what_to_fix": self.what_to_fix,
        }


@dataclass(frozen=True)
class AuditEvent:
    """One entry in the task's append-only history."""

    task_id: str
    at: float
    kind: str  # "transition" / "review_comment" / "human_action" / "creation" / ...
    actor: str
    payload: Mapping[str, Any]


# ---------------------------------------------------------------------------
# Store protocol (in-memory for tests; SQLAlchemy in prod)
# ---------------------------------------------------------------------------


class TaskStore(Protocol):
    """The bits of the task DB the lifecycle needs."""

    def get(self, task_id: str) -> TaskRecord | None: ...
    def save(self, task: TaskRecord) -> None: ...
    def append_event(self, event: AuditEvent) -> None: ...
    def list_events(self, task_id: str) -> Iterable[AuditEvent]: ...
    def list_by_status(self, plan_id: str, status: TaskStatus) -> Iterable[TaskRecord]: ...


class InMemoryTaskStore:
    """Reference implementation used by tests + dev scripts."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._events: list[AuditEvent] = []
        self._closed_tasks: set[str] = set()

    def get(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def save(self, task: TaskRecord) -> None:
        # task_06_34b6: append-only enforcement — once a task is
        # done/cancelled, the store rejects further mutations to its
        # CORE fields (status). Adding events is fine.
        if task.id in self._closed_tasks and task.status not in {"done", "cancelled"}:
            raise ValueError(f"task {task.id!r} is closed; cannot transition from terminal state")
        if task.status in {"done", "cancelled"}:
            self._closed_tasks.add(task.id)
        self._tasks[task.id] = task

    def append_event(self, event: AuditEvent) -> None:
        self._events.append(event)

    def list_events(self, task_id: str) -> Iterable[AuditEvent]:
        return [e for e in self._events if e.task_id == task_id]

    def list_by_status(self, plan_id: str, status: TaskStatus) -> Iterable[TaskRecord]:
        return [t for t in self._tasks.values() if t.plan_id == plan_id and t.status == status]


# ---------------------------------------------------------------------------
# Notification protocol — escalation fires it
# ---------------------------------------------------------------------------


class Notifier(Protocol):
    def notify_escalation(self, task: TaskRecord, history: list[AuditEvent]) -> None: ...


class NullNotifier:
    def notify_escalation(
        self,
        task: TaskRecord,  # noqa: ARG002
        history: list[AuditEvent],  # noqa: ARG002
    ) -> None:
        return


# ---------------------------------------------------------------------------
# TaskLifecycle
# ---------------------------------------------------------------------------


class TaskClosedError(RuntimeError):
    """Raised when an attempted transition violates the append-only rule."""


@dataclass
class TaskLifecycle:
    """Transitions + audit for one task store + notifier."""

    store: TaskStore
    notifier: Notifier = field(default_factory=NullNotifier)

    # --- task_06_34b1 — reject auto review ---------------------------

    def reject_review(
        self,
        task_id: str,
        *,
        comment: ReviewComment,
        reviewer_actor: str = "agent:reviewer",
    ) -> TaskRecord:
        task = self._must_get(task_id)
        if task.status in {"done", "cancelled"}:
            raise TaskClosedError(f"task {task_id!r} already in terminal state")

        task.retry_count += 1
        task.status = "backlog"
        self.store.save(task)
        self._emit(
            task_id,
            kind="review_comment",
            actor=reviewer_actor,
            payload={"rejected": True, **comment.to_dict()},
        )
        self._emit(
            task_id,
            kind="transition",
            actor=reviewer_actor,
            payload={"from": "in_review", "to": "backlog", "retry_count": task.retry_count},
        )
        return self.escalate_if_exhausted(task)

    # --- task_06_34b2 — escalation -----------------------------------

    def escalate_if_exhausted(self, task: TaskRecord) -> TaskRecord:
        if task.status != "backlog" or task.retry_count < task.max_retries:
            return task
        # F43: escalate to the canonical `blocked` state, not the orphan
        # `awaiting_human` (which existed in no enum / state-machine table and
        # was persisted in silence). `blocked` is what reviewer_bridge and the
        # worker also escalate to — the inbox/panel surfaces it (F44).
        task.status = "blocked"
        self.store.save(task)
        self._emit(
            task.id,
            kind="transition",
            actor="system",
            payload={"from": "backlog", "to": "blocked", "reason": "max_retries"},
        )
        history = list(self.store.list_events(task.id))
        self.notifier.notify_escalation(task, history)
        _log.warning("task.escalated", task=task.id, retries=task.retry_count)
        return task

    # --- task_06_34b3 — list escalated -------------------------------

    def list_escalated(self, plan_id: str) -> list[TaskRecord]:
        # F43: escalated tasks now live in `blocked` (see escalate_if_exhausted).
        return list(self.store.list_by_status(plan_id, "blocked"))

    # --- task_06_34b3 — four human actions ---------------------------

    def apply_human_action(
        self,
        task_id: str,
        action: HumanAction,
        *,
        actor: str,
        reason: str | None = None,
        guidance: str | None = None,
    ) -> TaskRecord:
        task = self._must_get(task_id)
        # F43: the human acts on an escalated (`blocked`) task, not the orphan
        # `awaiting_human`.
        if task.status != "blocked":
            raise TaskClosedError(f"task {task_id!r} not escalated/blocked (got {task.status!r})")

        if action == "approve_manual":
            task.manual_approval = True
            task.status = "done"
        elif action == "reassign_with_guidance":
            task.retry_count = 0
            task.status = "backlog"
        elif action == "block_with_reason":
            task.status = "blocked"
        elif action == "cancel":
            task.status = "cancelled"
        self.store.save(task)
        self._emit(
            task_id,
            kind="human_action",
            actor=actor,
            payload={
                "action": action,
                "reason": reason,
                "guidance": guidance,
                "new_status": task.status,
            },
        )
        return task

    # --- task_06_34b4 — task from failed checkbox --------------------

    def create_task_from_checkbox(
        self,
        *,
        plan_id: str,
        checkbox_id: str,
        checkbox_text: str,
        reviewer_comment: str,
        actor: str = "human:reviewer",
    ) -> TaskRecord:
        return self._create_plan_task(
            plan_id=plan_id,
            title=checkbox_text,
            description=reviewer_comment,
            checkbox_id=checkbox_id,
            is_free=False,
            actor=actor,
        )

    # --- task_06_34b5 — free task ------------------------------------

    def create_free_task(
        self,
        *,
        plan_id: str,
        title: str,
        description: str,
        actor: str = "human:reviewer",
    ) -> TaskRecord:
        return self._create_plan_task(
            plan_id=plan_id,
            title=title,
            description=description,
            checkbox_id=None,
            is_free=True,
            actor=actor,
        )

    def _create_plan_task(
        self,
        *,
        plan_id: str,
        title: str,
        description: str,
        checkbox_id: str | None,
        is_free: bool,
        actor: str,
    ) -> TaskRecord:
        task = TaskRecord(
            id=uuid.uuid4().hex,
            plan_id=plan_id,
            title=title,
            description=description,
            status="backlog",
            parent_checkbox_id=checkbox_id,
            is_free_task=is_free,
        )
        self.store.save(task)
        self._emit(
            task.id,
            kind="creation",
            actor=actor,
            payload={
                "plan_id": plan_id,
                "from_checkbox": checkbox_id,
                "is_free_task": is_free,
            },
        )
        return task

    # --- task_06_34b6 — audit log accessors --------------------------

    def history(self, task_id: str) -> list[AuditEvent]:
        return sorted(self.store.list_events(task_id), key=lambda e: e.at)

    # --- helpers -----------------------------------------------------

    def _emit(self, task_id: str, *, kind: str, actor: str, payload: Mapping[str, Any]) -> None:
        self.store.append_event(
            AuditEvent(
                task_id=task_id, at=time.time(), kind=kind, actor=actor, payload=dict(payload)
            )
        )

    def _must_get(self, task_id: str) -> TaskRecord:
        task = self.store.get(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} not found")
        return task


__all__ = [
    "AuditEvent",
    "DEFAULT_MAX_REVIEW_RETRIES",
    "HumanAction",
    "InMemoryTaskStore",
    "Notifier",
    "NullNotifier",
    "ReviewComment",
    "TaskClosedError",
    "TaskLifecycle",
    "TaskRecord",
    "TaskStatus",
    "TaskStore",
]
