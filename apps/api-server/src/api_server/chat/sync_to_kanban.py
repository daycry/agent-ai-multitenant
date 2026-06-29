"""Plan → Kanban sync (Plan 03 task_03_27 / task_03_28 / task_03_29).

Materialises the flat ``plan.specification.tasks`` into the operational
``tasks`` table so the orchestrator (Plan 02) can start scheduling
them. The plan's ``depends_on`` strings are translated into
``task_dependencies`` rows linking the freshly-created tasks together.

Three scopes are supported (task_03_27):

  - ``total``     — materialise every task in the spec.
  - ``phase``     — only the tasks listed under one ``phases[i].tasks``.
  - ``selection`` — only the spec task ids the caller passes in.

Idempotency (task_03_29) is achieved by tagging each materialised task
with its source id in ``Task.inputs["plan_task_spec_id"]``. A second
call on the same plan never duplicates a task: rows whose spec id is
already mapped become "skipped" and are reused as dependency targets
for any new siblings.

This module deliberately holds **no** HTTP / FastAPI imports: the
router translates ``SyncScopeError`` into a 422 and ``SyncResult`` into
its response model.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.chat.planning_graph import PlanningRole
from api_server.chat.responder import team_role_agents
from api_server.db.domain import Plan, Project, Task, TaskDependency, TaskStatus

# Key under which we stash the spec id on the materialised Task row.
# Living inside the JSONB `inputs` column keeps the schema migration-free.
PLAN_TASK_SPEC_ID_KEY = "plan_task_spec_id"

# Default complexity stored on the materialised task when the spec
# doesn't carry one. Matches the cost calculator default (`m`).
_DEFAULT_COMPLEXITY = "m"


class SyncScopeError(ValueError):
    """Raised when the requested scope is incoherent with the spec.

    The router maps this to a 422 with a focused message so the UI can
    surface "phase index out of range" or "task X not in plan" instead
    of a generic 500.
    """


@dataclass(frozen=True)
class SyncResult:
    """Outcome of a single sync call (returned by ``sync_plan_to_kanban``).

    ``created_task_ids`` and ``skipped_task_ids`` are keyed by the
    spec id so the UI can highlight which rows landed and which were
    already there from a previous sync.
    """

    created_task_ids: dict[str, UUID] = field(default_factory=dict)
    skipped_task_ids: dict[str, UUID] = field(default_factory=dict)
    dependencies_created: int = 0


def select_spec_ids(
    specification: dict[str, Any],
    *,
    scope: str,
    phase_index: int | None = None,
    task_ids: Iterable[str] | None = None,
) -> list[str]:
    """Resolve which ``specification.tasks[*].id`` the scope picks.

    Returns the spec-task ids in the order they appear in the flat
    ``tasks`` list (so dependencies land before their dependents when
    a phase-scoped sync includes both).
    """
    all_tasks: list[dict[str, Any]] = list(specification.get("tasks") or [])
    if not all_tasks:
        return []

    valid_ids: list[str] = [t["id"] for t in all_tasks if isinstance(t.get("id"), str)]
    valid_set = set(valid_ids)

    if scope == "total":
        return list(valid_ids)

    if scope == "phase":
        if phase_index is None:
            raise SyncScopeError("scope='phase' requires phase_index")
        phases = specification.get("phases") or []
        if not isinstance(phases, list) or phase_index < 0 or phase_index >= len(phases):
            raise SyncScopeError(f"phase index {phase_index} out of range")
        phase_task_ids = phases[phase_index].get("tasks") or []
        if not isinstance(phase_task_ids, list):
            raise SyncScopeError(f"phases[{phase_index}].tasks must be a list of task ids")
        unknown = [tid for tid in phase_task_ids if tid not in valid_set]
        if unknown:
            raise SyncScopeError(f"phase references unknown task ids: {sorted(unknown)}")
        # Preserve flat-list order so dependencies precede dependents.
        wanted = set(phase_task_ids)
        return [tid for tid in valid_ids if tid in wanted]

    if scope == "selection":
        if task_ids is None:
            raise SyncScopeError("scope='selection' requires task_ids")
        wanted_list = list(task_ids)
        if not wanted_list:
            raise SyncScopeError("scope='selection' requires at least one task id")
        unknown = [tid for tid in wanted_list if tid not in valid_set]
        if unknown:
            raise SyncScopeError(f"selection references unknown task ids: {sorted(unknown)}")
        wanted = set(wanted_list)
        return [tid for tid in valid_ids if tid in wanted]

    raise SyncScopeError(f"unknown scope {scope!r}")


async def sync_plan_to_kanban(
    session: AsyncSession,
    plan: Plan,
    *,
    scope: str,
    phase_index: int | None = None,
    task_ids: Iterable[str] | None = None,
) -> SyncResult:
    """Materialise plan tasks into the Kanban. See module docstring.

    The router has already loaded the plan under the tenant session
    (so RLS is in force) and verified it is writable. We only need to
    pick the spec ids, look up which are already materialised, and
    create the rest plus their dependency rows.
    """
    spec = plan.specification or {}
    selected_ids = select_spec_ids(spec, scope=scope, phase_index=phase_index, task_ids=task_ids)
    if not selected_ids:
        return SyncResult()

    spec_by_id = {
        t["id"]: t
        for t in (spec.get("tasks") or [])
        if isinstance(t, dict) and isinstance(t.get("id"), str)
    }

    # Resolve the team's per-role agents ONCE so each task's spec `role`
    # (planning_llm) lands on the right implementer (and reviewer). Empty
    # when the project has no team — tasks then materialise unassigned and
    # the orchestrator's load policy decides (Track 2 / ADR 0090-assignment).
    project = (
        await session.execute(select(Project).where(Project.id == plan.project_id))
    ).scalar_one_or_none()
    role_agents = await team_role_agents(session, project)

    # Look up *all* tasks already materialised for this plan — not just
    # the ones in this scope. A spec task outside the scope may already
    # exist from a previous sync and we still need its UUID to wire
    # dependencies for tasks inside the scope.
    existing = await _load_existing_materialised(session, plan)

    result = SyncResult()
    for spec_id in selected_ids:
        if spec_id in existing:
            result.skipped_task_ids[spec_id] = existing[spec_id]
            continue
        spec_task = spec_by_id[spec_id]
        task = _build_task(plan, spec_id, spec_task, role_agents=role_agents)
        session.add(task)
        await session.flush()  # populate task.id before its dependency rows
        existing[spec_id] = task.id
        result.created_task_ids[spec_id] = task.id

    # Wire dependencies for the newly-created tasks. We skip already-
    # materialised tasks (idempotency: a previous sync persisted theirs).
    deps_created = 0
    for spec_id, new_task_id in result.created_task_ids.items():
        spec_task = spec_by_id[spec_id]
        depends_on = spec_task.get("depends_on") or []
        if not isinstance(depends_on, list):
            continue
        for dep_spec_id in depends_on:
            if not isinstance(dep_spec_id, str):
                continue
            dep_task_id = existing.get(dep_spec_id)
            if dep_task_id is None:
                # Dependency hasn't been materialised yet (out of scope).
                # We skip the link — the orchestrator's DAG check (
                # task_03_30) is what blocks transitions until the dep
                # is brought in. The caller can re-run with a wider
                # scope to wire it up.
                continue
            session.add(TaskDependency(task_id=new_task_id, depends_on_task_id=dep_task_id))
            deps_created += 1

    if deps_created:
        await session.flush()
    return SyncResult(
        created_task_ids=result.created_task_ids,
        skipped_task_ids=result.skipped_task_ids,
        dependencies_created=deps_created,
    )


async def _load_existing_materialised(session: AsyncSession, plan: Plan) -> dict[str, UUID]:
    """Return ``{spec_id: task_id}`` for every Task already created from
    this plan (regardless of status)."""
    result = await session.execute(
        select(Task).where(Task.plan_id == plan.id, Task.project_id == plan.project_id)
    )
    out: dict[str, UUID] = {}
    for task in result.scalars().all():
        spec_id = (task.inputs or {}).get(PLAN_TASK_SPEC_ID_KEY)
        if isinstance(spec_id, str):
            out[spec_id] = task.id
    return out


def _resolve_assignment(
    spec_task: dict[str, Any], role_agents: dict[PlanningRole, UUID] | None
) -> tuple[UUID | None, UUID | None]:
    """Resolve ``(assigned_agent_id, reviewer_agent_id)`` from the spec ``role``.

    The implementer is the team's agent of the task's ``role``; the reviewer is
    the team's ``reviewer`` role agent — but NEVER the implementer itself
    (reviewer != implementer invariant). An unknown role, a role with no team
    agent, or no team at all leaves the slot ``None`` so the dispatcher's load
    policy decides instead of forcing an arbitrary agent.
    """
    if not role_agents:
        return None, None
    assigned: UUID | None = None
    role_str = str(spec_task.get("role") or "").strip()
    if role_str:
        try:
            assigned = role_agents.get(PlanningRole(role_str))
        except ValueError:
            assigned = None
    reviewer = role_agents.get(PlanningRole.REVIEWER)
    if reviewer is not None and reviewer == assigned:
        reviewer = None
    return assigned, reviewer


def _build_task(
    plan: Plan,
    spec_id: str,
    spec_task: dict[str, Any],
    role_agents: dict[PlanningRole, UUID] | None = None,
) -> Task:
    """Translate one spec task into a `Task` ORM row.

    All materialised tasks start in `backlog`. The orchestrator (Plan
    02) is what promotes dependency-free ones to `ready`. When the plan
    spec carries a ``role`` and the project has a team, the task is
    pre-assigned to that role's agent (Track 2) — the dispatcher honours
    the preset instead of load-balancing the implementation onto, say, the PM.
    """
    assigned_agent_id, reviewer_agent_id = _resolve_assignment(spec_task, role_agents)
    title = spec_task.get("title")
    if not isinstance(title, str) or not title.strip():
        title = spec_id  # fall back to the id if no title — never empty
    description = spec_task.get("description")
    complexity_raw = spec_task.get("complexity")
    complexity = (
        complexity_raw
        if isinstance(complexity_raw, str) and complexity_raw in {"xs", "s", "m", "l", "xl"}
        else _DEFAULT_COMPLEXITY
    )
    acceptance = spec_task.get("acceptance_criteria")
    if not isinstance(acceptance, list):
        acceptance = []
    return Task(
        tenant_id=plan.tenant_id,
        project_id=plan.project_id,
        plan_id=plan.id,
        title=title.strip()[:200],
        description=description if isinstance(description, str) else None,
        status=TaskStatus.BACKLOG.value,
        priority="medium",
        acceptance_criteria=acceptance,
        # We stash the spec id under `inputs` so future syncs match.
        inputs={PLAN_TASK_SPEC_ID_KEY: spec_id},
        estimated_complexity=complexity,
        assigned_agent_id=assigned_agent_id,
        reviewer_agent_id=reviewer_agent_id,
    )


__all__ = [
    "PLAN_TASK_SPEC_ID_KEY",
    "SyncResult",
    "SyncScopeError",
    "select_spec_ids",
    "sync_plan_to_kanban",
]
