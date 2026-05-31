"""Acceptance-timeout escalation sweep (Plan 16 task_16_06).

When the orchestrator routes a ``ready`` human task (task_16_05) it creates a
``pending_acceptance`` :class:`~api_server.db.domain.HumanTaskAssignment` and
parks the Task in ``assigned_to_human``. The assigned User then has up to the
Human Agent's ``acceptance_timeout_hours`` to accept. This module is the sweep
that enforces that deadline:

  * A ``pending_acceptance`` assignment older than ``acceptance_timeout_hours``
    that has NOT yet been escalated is ESCALATED: the timed-out row is marked
    ``reassigned``; a FRESH ``pending_acceptance`` row is created for the Human
    Agent's ``escalation_target_user_id`` (resetting ``assigned_at`` so the
    escalation target gets a full window); the Task stays in
    ``assigned_to_human`` via the §7.2 reassignment self-loop; and the
    escalation target is notified (``human_task_assigned``).

  * If the ESCALATION target's assignment ALSO times out (the task has already
    been escalated once), the timed-out row is marked ``expired``, the Task
    transitions ``assigned_to_human -> blocked`` (§7.2), and the Tenant Admin is
    notified (``task_blocked``). No escalation target configured is treated the
    same way — there is nobody left to hand it to, so the task blocks.

Best-effort + idempotent
------------------------
Marking the timed-out row ``reassigned``/``expired`` removes it from the
``pending_acceptance`` set, and a fresh escalation row resets ``assigned_at``,
so re-running the sweep does NOT double-escalate. Each assignment is processed
in its OWN transaction so one bad row never poisons the batch; a per-row failure
is logged and the sweep moves on (beat keeps its cadence).

Multi-tenancy (NON-NEGOTIABLE)
------------------------------
The worker runs BYPASSRLS, so every read/write carries an explicit
``tenant_id`` predicate keyed on the assignment's OWN tenant: the Human Agent
config, the escalation-target resolution, the prior-assignment lookup and the
Task transition are all scoped to ``assignment.tenant_id``. A tenant-A
assignment can never reassign to a tenant-B user or block a tenant-B task. The
``task_blocked`` alert carries the assignment's ``tenant_id`` so the dispatcher
fans it out to that tenant's admins ONLY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api_server.db.domain import (
    Agent,
    AgentType,
    HumanAgentConfig,
    HumanTaskAssignment,
    HumanTaskAssignmentStatus,
    Project,
    Task,
    TaskStatus,
)
from api_server.task_state_machine import transition_task_status

_log = structlog.get_logger("api_server.human_agents.escalation")

#: Notification event a (re)assignment to the escalation target fires — the
#: SAME event the orchestrator uses for the first assignment (task_16_05),
#: registered in the dispatcher's EVENT_REGISTRY + templates. Priority lane.
HUMAN_TASK_ASSIGNED_EVENT = "human_task_assigned"

#: Notification event a blocked (escalation-exhausted) task fires. Carries the
#: assignment's tenant_id so the dispatcher fans it out to that tenant's admin
#: channels only — this is how the Tenant Admin is notified.
TASK_BLOCKED_EVENT = "task_blocked"


class EscalationOutcome(StrEnum):
    """What the sweep did with one timed-out assignment."""

    #: Reassigned to the escalation target (a fresh pending_acceptance row).
    ESCALATED = "escalated"
    #: Escalation exhausted (already escalated, or no target) -> task blocked.
    BLOCKED = "blocked"
    #: Could not act (e.g. the task is no longer assigned_to_human) — skipped.
    SKIPPED = "skipped"


@dataclass(frozen=True)
class EscalationNotice:
    """A notification the sweep wants sent, handed to the notifier seam.

    ``event_type`` is the dispatcher event_type; ``tenant_id`` scopes the
    fan-out; ``context`` is the template render context. The domain core BUILDS
    these but never enqueues — the worker's :class:`HumanEscalationNotifier`
    sends them (clean app boundary; no Celery in the domain layer).
    """

    event_type: str
    tenant_id: str
    context: dict[str, Any]


@dataclass(frozen=True)
class EscalationOutcomeRow:
    """The result of processing ONE timed-out assignment."""

    assignment_id: UUID
    task_id: UUID
    tenant_id: UUID
    outcome: EscalationOutcome
    new_assignment_id: UUID | None = None
    reassigned_to_user_id: UUID | None = None


@dataclass
class EscalationSweepResult:
    """Summary of one sweep pass — what the beat task logs/returns."""

    scanned: int = 0
    escalated: int = 0
    blocked: int = 0
    skipped: int = 0
    rows: list[EscalationOutcomeRow] = field(default_factory=list)

    def record(self, row: EscalationOutcomeRow) -> None:
        self.rows.append(row)
        if row.outcome is EscalationOutcome.ESCALATED:
            self.escalated += 1
        elif row.outcome is EscalationOutcome.BLOCKED:
            self.blocked += 1
        else:
            self.skipped += 1


class HumanEscalationNotifier(Protocol):
    """Sends the notifications the sweep produces (injectable seam).

    The production implementation (``workers.human_escalation``) enqueues a
    ``notification_dispatcher.dispatch_event`` by name onto the priority lane;
    tests assert on the captured notices without a real broker.
    """

    def notify(self, notice: EscalationNotice) -> None:  # pragma: no cover - Protocol
        ...


# ---------------------------------------------------------------------------
# Candidate selection — the open, aged-out pending_acceptance rows.
# ---------------------------------------------------------------------------
async def _find_timed_out_assignments(
    session: AsyncSession, *, now: datetime
) -> list[tuple[HumanTaskAssignment, HumanAgentConfig | None]]:
    """The ``pending_acceptance`` assignments whose age exceeds their Human
    Agent's ``acceptance_timeout_hours``, paired with that config.

    The age threshold is per-assignment (each Human Agent carries its own
    ``acceptance_timeout_hours``), so the cut-off cannot be expressed as a
    single SQL ``WHERE`` clause across the batch. We load the open
    ``pending_acceptance`` rows + their config (a single join) and apply the
    per-row deadline in Python. The config join is tenant-scoped on BOTH sides
    so a forged cross-tenant ``human_agent_id`` can never pull in another
    tenant's timeout. An assignment with no resolvable config (the agent or its
    config was deleted) is still returned with ``config=None`` so the sweep can
    block the stranded task rather than leak it forever.
    """
    stmt = (
        select(HumanTaskAssignment, HumanAgentConfig)
        .outerjoin(
            HumanAgentConfig,
            (HumanAgentConfig.agent_id == HumanTaskAssignment.human_agent_id)
            & (HumanAgentConfig.tenant_id == HumanTaskAssignment.tenant_id),
        )
        .where(HumanTaskAssignment.status == HumanTaskAssignmentStatus.PENDING_ACCEPTANCE.value)
    )
    rows = (await session.execute(stmt)).all()
    timed_out: list[tuple[HumanTaskAssignment, HumanAgentConfig | None]] = []
    for assignment, config in rows:
        timeout_hours = config.acceptance_timeout_hours if config is not None else 24
        deadline = assignment.assigned_at + timedelta(hours=timeout_hours)
        if _as_utc(deadline) <= now:
            timed_out.append((assignment, config))
    return timed_out


def _as_utc(value: datetime) -> datetime:
    """Treat a naive timestamp (asyncpg can hand one back) as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Per-assignment processing (its own transaction — best-effort isolation).
# ---------------------------------------------------------------------------
async def _process_one(
    session: AsyncSession,
    assignment_id: UUID,
    *,
    now: datetime,
) -> tuple[EscalationOutcomeRow, EscalationNotice | None]:
    """Escalate or block ONE assignment, in the caller's open transaction.

    Re-reads the assignment under the lock (a concurrent accept may have moved
    it out of ``pending_acceptance`` since the candidate scan) and the Task, so
    a stale candidate is a safe no-op (idempotency). Returns the outcome row +
    the notice to send AFTER the commit (so a notify failure never rolls back
    the DB change — best-effort).
    """
    assignment = (
        await session.execute(
            select(HumanTaskAssignment).where(HumanTaskAssignment.id == assignment_id)
        )
    ).scalar_one_or_none()
    # Concurrency / idempotency guard: only an open pending_acceptance row is
    # actionable. A re-run (or a race with an accept) finds it already moved.
    if (
        assignment is None
        or assignment.status != HumanTaskAssignmentStatus.PENDING_ACCEPTANCE.value
    ):
        return (
            EscalationOutcomeRow(
                assignment_id=assignment_id,
                task_id=assignment.task_id if assignment is not None else assignment_id,
                tenant_id=assignment.tenant_id if assignment is not None else assignment_id,
                outcome=EscalationOutcome.SKIPPED,
            ),
            None,
        )

    tenant_id = assignment.tenant_id
    task = (
        await session.execute(
            select(Task).where(
                Task.id == assignment.task_id,
                Task.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    # The transition is legal only from assigned_to_human. If the task has moved
    # on (accepted -> in_progress, cancelled, …) the assignment is stale: close
    # it without touching the task.
    if task is None or task.status != TaskStatus.ASSIGNED_TO_HUMAN.value:
        assignment.status = HumanTaskAssignmentStatus.EXPIRED.value
        return (
            EscalationOutcomeRow(
                assignment_id=assignment_id,
                task_id=assignment.task_id,
                tenant_id=tenant_id,
                outcome=EscalationOutcome.SKIPPED,
            ),
            None,
        )

    config = await _resolve_config(session, assignment, tenant_id)
    escalation_target = config.escalation_target_user_id if config is not None else None
    already_escalated = await _task_already_escalated(session, task.id, tenant_id)

    project_name = await _project_name(session, task.project_id, tenant_id)

    # Exhausted: already escalated once, OR no target to hand it to -> block.
    if already_escalated or escalation_target is None:
        assignment.status = HumanTaskAssignmentStatus.EXPIRED.value
        # assigned_to_human -> blocked (§7.2; legal for a human assignee).
        transition_task_status(task, TaskStatus.BLOCKED.value, assignee_agent_type=AgentType.HUMAN)
        notice = EscalationNotice(
            event_type=TASK_BLOCKED_EVENT,
            tenant_id=str(tenant_id),
            context={
                "task_id": str(task.id),
                "task_title": task.title,
                "project_name": project_name,
                "reason": (
                    "acceptance timeout exhausted after escalation"
                    if already_escalated
                    else "acceptance timeout with no escalation target configured"
                ),
            },
        )
        return (
            EscalationOutcomeRow(
                assignment_id=assignment_id,
                task_id=task.id,
                tenant_id=tenant_id,
                outcome=EscalationOutcome.BLOCKED,
            ),
            notice,
        )

    # First escalation: supersede this row, hand the task to the target.
    assignment.status = HumanTaskAssignmentStatus.REASSIGNED.value
    new_assignment = HumanTaskAssignment(
        tenant_id=tenant_id,
        task_id=task.id,
        human_agent_id=assignment.human_agent_id,
        assigned_to_user_id=escalation_target,
        assigned_at=now,  # reset the clock — the target gets a full window
        status=HumanTaskAssignmentStatus.PENDING_ACCEPTANCE.value,
    )
    session.add(new_assignment)
    await session.flush()  # populate new_assignment.id
    # assigned_to_human -> assigned_to_human (reassignment self-loop, §7.2).
    transition_task_status(
        task, TaskStatus.ASSIGNED_TO_HUMAN.value, assignee_agent_type=AgentType.HUMAN
    )
    notice = EscalationNotice(
        event_type=HUMAN_TASK_ASSIGNED_EVENT,
        tenant_id=str(tenant_id),
        context={
            "task_id": str(task.id),
            "task_title": task.title,
            "project_name": project_name,
            "assigned_to_user_id": str(escalation_target),
            "human_agent_id": (
                str(assignment.human_agent_id) if assignment.human_agent_id is not None else None
            ),
            "escalated": True,
        },
    )
    return (
        EscalationOutcomeRow(
            assignment_id=assignment_id,
            task_id=task.id,
            tenant_id=tenant_id,
            outcome=EscalationOutcome.ESCALATED,
            new_assignment_id=new_assignment.id,
            reassigned_to_user_id=escalation_target,
        ),
        notice,
    )


async def _resolve_config(
    session: AsyncSession, assignment: HumanTaskAssignment, tenant_id: UUID
) -> HumanAgentConfig | None:
    """The Human Agent's config for this assignment, tenant-scoped.

    Joins through the assignment's ``human_agent_id`` and requires the Agent +
    config to live in the assignment's OWN tenant — a forged cross-tenant agent
    id resolves to None (and the task blocks rather than escalating wrongly)."""
    if assignment.human_agent_id is None:
        return None
    return (
        await session.execute(
            select(HumanAgentConfig)
            .join(Agent, Agent.id == HumanAgentConfig.agent_id)
            .where(
                HumanAgentConfig.agent_id == assignment.human_agent_id,
                HumanAgentConfig.tenant_id == tenant_id,
                Agent.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()


async def _task_already_escalated(session: AsyncSession, task_id: UUID, tenant_id: UUID) -> bool:
    """True iff a prior assignment for this task is already ``reassigned``.

    That marks "we escalated once". When the escalation target's own assignment
    times out, this is True, so the sweep blocks instead of escalating again."""
    prior = (
        await session.execute(
            select(HumanTaskAssignment.id).where(
                HumanTaskAssignment.task_id == task_id,
                HumanTaskAssignment.tenant_id == tenant_id,
                HumanTaskAssignment.status == HumanTaskAssignmentStatus.REASSIGNED.value,
            )
        )
    ).first()
    return prior is not None


async def _project_name(session: AsyncSession, project_id: UUID, tenant_id: UUID) -> str | None:
    """The task's project name for the notification context (tenant-scoped)."""
    return (
        await session.execute(
            select(Project.name).where(Project.id == project_id, Project.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Public entry point — one full sweep pass.
# ---------------------------------------------------------------------------
async def sweep_acceptance_timeouts(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    notifier: HumanEscalationNotifier | None = None,
    now: datetime | None = None,
) -> EscalationSweepResult:
    """Run one acceptance-timeout escalation pass.

    Finds the timed-out ``pending_acceptance`` assignments, then processes each
    one in its OWN transaction (best-effort isolation — one poison row never
    aborts the batch). Notifications are sent AFTER each commit so a broker
    hiccup never rolls back the DB change. ``now`` is injectable so tests are
    deterministic. Idempotent: re-running is safe (a processed row has left
    ``pending_acceptance`` and a fresh escalation row has a reset clock).
    """
    moment = now or datetime.now(UTC)
    result = EscalationSweepResult()

    async with sessionmaker() as scan_session:
        candidates = await _find_timed_out_assignments(scan_session, now=moment)
    result.scanned = len(candidates)

    for assignment, _config in candidates:
        try:
            async with sessionmaker() as session, session.begin():
                row, notice = await _process_one(session, assignment.id, now=moment)
        except Exception as exc:  # pragma: no cover - defensive: one bad row
            _log.warning(
                "human_escalation.row_failed",
                assignment_id=str(assignment.id),
                error=str(exc),
            )
            continue
        result.record(row)
        # Notify AFTER the commit (best-effort — never rolls back the move).
        if notice is not None and notifier is not None:
            try:
                notifier.notify(notice)
            except Exception as exc:  # pragma: no cover - best-effort notify
                _log.warning(
                    "human_escalation.notify_failed",
                    assignment_id=str(assignment.id),
                    event_type=notice.event_type,
                    error=str(exc),
                )

    _log.info(
        "human_escalation.sweep_done",
        scanned=result.scanned,
        escalated=result.escalated,
        blocked=result.blocked,
        skipped=result.skipped,
    )
    return result


__all__ = [
    "HUMAN_TASK_ASSIGNED_EVENT",
    "TASK_BLOCKED_EVENT",
    "EscalationNotice",
    "EscalationOutcome",
    "EscalationOutcomeRow",
    "EscalationSweepResult",
    "HumanEscalationNotifier",
    "sweep_acceptance_timeouts",
]
