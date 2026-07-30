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

import structlog
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.chat.planning_graph import PlanningRole
from api_server.chat.responder import team_role_agents
from api_server.db.domain import Plan, Project, Task, TaskDependency, TaskStatus

_log = structlog.get_logger("chat.sync_to_kanban")

# Key under which we stash the spec id on the materialised Task row.
# Living inside the JSONB `inputs` column keeps the schema migration-free.
PLAN_TASK_SPEC_ID_KEY = "plan_task_spec_id"

# Default complexity stored on the materialised task when the spec
# doesn't carry one. Matches the cost calculator default (`m`).
_DEFAULT_COMPLEXITY = "m"


class ReplanInFlightError(RuntimeError):
    """Un replan que tocaría tareas EN VUELO (ADR 0132, decisión (b)).

    No se cancela nada por nuestra cuenta: cancelar automáticamente tira
    trabajo —y dinero de tokens— por un cambio que el operador podría no haber
    querido aplicar a esa tarea. Se rechaza nombrando las tareas para que las
    pare desde su ficha y reintente.
    """

    def __init__(self, task_ids: list[str], titles: list[str]) -> None:
        self.task_ids = task_ids
        self.titles = titles
        super().__init__(
            "estas tareas están en vuelo y el replan las tocaría: " + ", ".join(titles)
        )


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
    # ADR 0132 / `task_wf_45`: la reconciliación deja de ser solo aditiva.
    # `updated` son tareas ya materializadas cuyo spec cambió y que aún no
    # habían empezado; `cancelled` son las que desaparecieron del spec;
    # `frozen` son las que el spec cambió pero YA ESTÁN HECHAS — no se
    # reescribe la historia, se avisa.
    updated_task_ids: dict[str, UUID] = field(default_factory=dict)
    cancelled_task_ids: dict[str, UUID] = field(default_factory=dict)
    frozen_task_ids: dict[str, UUID] = field(default_factory=dict)
    dependencies_removed: int = 0


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


def _classify(
    spec_id: str,
    row: Task,
    result: SyncResult,
    in_flight: list[Task],
    on_actionable: Any,
) -> None:
    """Encaja UNA tarea ya materializada en las tres vías del ADR 0132.

    Terminal → se congela y se avisa (lo hecho es historia). No empezada → la
    acción que toque (actualizar o cancelar). En vuelo → a la lista de rechazo:
    pararla es decisión humana, no efecto colateral de guardar un spec.
    """
    if row.status in _REPLAN_TERMINAL:
        result.frozen_task_ids[spec_id] = row.id
    elif row.status in _REPLAN_EDITABLE:
        on_actionable()
    else:
        in_flight.append(row)


async def _reconcile_existing(
    session: AsyncSession,
    result: SyncResult,
    *,
    selected_ids: list[str],
    scope: str,
    spec_by_id: dict[str, Any],
    existing_rows: dict[str, Task],
    role_agents: dict[PlanningRole, UUID] | None,
) -> None:
    """Aplica al tablero lo que el spec dice AHORA (ADR 0132 / `task_wf_45`).

    Antes esto no existía: `sync_to_kanban` era estrictamente aditivo, así que
    editar o borrar una tarea del spec **no llegaba nunca al tablero**. El
    operador creía haber replanificado y el equipo seguía ejecutando el plan
    anterior, sin un solo aviso.

    El rechazo por trabajo EN VUELO va primero, antes de tocar nada: o se
    aplica el replan entero o no se aplica ninguno. Un replan a medias deja el
    tablero en un estado que no es ni el plan viejo ni el nuevo, y nadie sabría
    cuál de los dos está mirando.

    Raises:
        ReplanInFlightError: alguna tarea afectada está corriendo o en review.
    """
    to_update: list[tuple[str, Task, dict[str, Any]]] = []
    to_cancel: list[tuple[str, Task]] = []
    in_flight: list[Task] = []

    for spec_id in selected_ids:
        row = existing_rows.get(spec_id)
        if row is None or not _spec_fields_changed(row, spec_by_id[spec_id]):
            continue
        _classify(
            spec_id,
            row,
            result,
            in_flight,
            lambda sid=spec_id, r=row: to_update.append((sid, r, spec_by_id[sid])),
        )

    # Huérfanas: materializadas y ya NO en el spec. Solo con el plan entero en
    # el scope — con un scope parcial, «no está en la selección» no significa
    # «se ha borrado del plan», y cancelarla sería destruir trabajo por una
    # ambigüedad.
    if scope == "all":
        for spec_id, row in existing_rows.items():
            if spec_id in spec_by_id:
                continue
            _classify(
                spec_id,
                row,
                result,
                in_flight,
                lambda sid=spec_id, r=row: to_cancel.append((sid, r)),
            )

    if in_flight:
        raise ReplanInFlightError(
            [str(t.id) for t in in_flight], [str(t.title or t.id) for t in in_flight]
        )

    for spec_id, row, spec_task in to_update:
        _apply_spec_to_task(row, spec_task, role_agents)
        result.updated_task_ids[spec_id] = row.id
    for spec_id, row in to_cancel:
        row.status = TaskStatus.CANCELLED.value
        result.cancelled_task_ids[spec_id] = row.id
    if to_update or to_cancel:
        await session.flush()


async def _prune_stale_edges(
    session: AsyncSession,
    existing: dict[str, UUID],
    spec_by_id: dict[str, Any],
    existing_edges: set[tuple[UUID, UUID]],
) -> int:
    """Borra las dependencias que el spec YA NO declara (ADR 0132).

    Hasta ahora las aristas solo se añadían, así que soltar una dependencia en
    el spec no la soltaba en el tablero: la tarea seguía esperando a algo de lo
    que el plan ya no dice que dependa, y el operador no tenía forma de verlo.

    Solo con el plan entero en el scope: con un scope parcial no se puede
    distinguir «esta dependencia se ha quitado» de «esta dependencia está fuera
    de la selección».
    """
    wanted: set[tuple[UUID, UUID]] = set()
    for spec_id, task_id in existing.items():
        spec_entry = spec_by_id.get(spec_id)
        if spec_entry is None:
            continue
        for dep_spec_id in spec_entry.get("depends_on") or []:
            dep_task_id = existing.get(str(dep_spec_id))
            if dep_task_id is not None:
                wanted.add((task_id, dep_task_id))
    stale = existing_edges - wanted
    for task_id, dep_task_id in stale:
        await session.execute(
            delete(TaskDependency).where(
                TaskDependency.task_id == task_id,
                TaskDependency.depends_on_task_id == dep_task_id,
            )
        )
    return len(stale)


async def sync_plan_to_kanban(  # noqa: PLR0912 - reconciliación + alta + aristas, secuencia lineal
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

    # PROY2-09: dos syncs CONCURRENTES del mismo plan (doble clic, evento
    # duplicado) leían ambos `existing` vacío y materializaban duplicados
    # (read-then-insert sin lock). El advisory lock transaccional por plan
    # serializa: el segundo espera y ve las tareas del primero como existing.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:plan_key, 0))"),
        {"plan_key": f"sync_to_kanban:{plan.id}"},
    )

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
    existing_rows = await _load_existing_rows(session, plan)
    existing = {sid: row.id for sid, row in existing_rows.items()}

    result = SyncResult()

    await _reconcile_existing(
        session,
        result,
        selected_ids=selected_ids,
        scope=scope,
        spec_by_id=spec_by_id,
        existing_rows=existing_rows,
        role_agents=role_agents,
    )

    for spec_id in selected_ids:
        if spec_id in existing:
            # Una tarea que ACABA de actualizarse no es «saltada»: decir lo
            # contrario en la respuesta haría creer al operador que su cambio
            # no se aplicó.
            if spec_id not in result.updated_task_ids:
                result.skipped_task_ids[spec_id] = existing[spec_id]
            continue
        spec_task = spec_by_id[spec_id]
        task = _build_task(plan, spec_id, spec_task, role_agents=role_agents)
        session.add(task)
        await session.flush()  # populate task.id before its dependency rows
        existing[spec_id] = task.id
        result.created_task_ids[spec_id] = task.id

    # Wire dependencies for EVERY materialised spec task, not just the newly-
    # created ones. PROY2-10: un re-sync con scope más ancho materializa la
    # dependencia que antes quedó fuera — la tarea PREEXISTENTE debe recibir
    # ahora su arista, o se pierde para siempre. Idempotente: se cargan las
    # aristas ya persistidas y solo se insertan las que faltan.
    existing_edges: set[tuple[UUID, UUID]] = set()
    if existing:
        edge_rows = await session.execute(
            select(TaskDependency.task_id, TaskDependency.depends_on_task_id).where(
                TaskDependency.task_id.in_(list(existing.values()))
            )
        )
        existing_edges = set(edge_rows.tuples().all())

    deps_created = 0
    for spec_id, task_id in existing.items():
        spec_entry = spec_by_id.get(spec_id)
        if spec_entry is None:
            continue
        depends_on = spec_entry.get("depends_on") or []
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
            if (task_id, dep_task_id) in existing_edges:
                continue
            session.add(TaskDependency(task_id=task_id, depends_on_task_id=dep_task_id))
            deps_created += 1

    deps_removed = (
        await _prune_stale_edges(session, existing, spec_by_id, existing_edges)
        if scope == "all"
        else 0
    )

    if deps_created or deps_removed:
        await session.flush()
    return SyncResult(
        created_task_ids=result.created_task_ids,
        skipped_task_ids=result.skipped_task_ids,
        dependencies_created=deps_created,
        updated_task_ids=result.updated_task_ids,
        cancelled_task_ids=result.cancelled_task_ids,
        frozen_task_ids=result.frozen_task_ids,
        dependencies_removed=deps_removed,
    )


async def _load_existing_rows(session: AsyncSession, plan: Plan) -> dict[str, Task]:
    """``{spec_id: Task}`` de todas las tareas ya materializadas de este plan.

    Devuelve las FILAS y no solo los ids porque la reconciliación (ADR 0132)
    necesita su estado para decidir qué se puede tocar."""
    result = await session.execute(
        select(Task).where(Task.plan_id == plan.id, Task.project_id == plan.project_id)
    )
    out: dict[str, Task] = {}
    for task in result.scalars().all():
        spec_id = (task.inputs or {}).get(PLAN_TASK_SPEC_ID_KEY)
        if isinstance(spec_id, str):
            out[spec_id] = task
    return out


async def _load_existing_materialised(session: AsyncSession, plan: Plan) -> dict[str, UUID]:
    """``{spec_id: task_id}`` — la vista solo-ids, para los callers que no
    reconcilian."""
    return {sid: task.id for sid, task in (await _load_existing_rows(session, plan)).items()}


# ADR 0132 / `task_wf_45`: qué se puede tocar de una tarea ya materializada.
#
#   * NO EMPEZADA  → se actualiza / se cancela. Replanificar es exactamente esto.
#   * EN VUELO     → se RECHAZA. Pararla es una decisión humana, no un efecto
#                    colateral de guardar un spec.
#   * TERMINAL     → se ignora y se avisa. Lo que ya se hizo es historia.
_REPLAN_EDITABLE = frozenset({TaskStatus.BACKLOG.value, TaskStatus.READY.value})
_REPLAN_TERMINAL = frozenset({TaskStatus.DONE.value, TaskStatus.CANCELLED.value})


def _spec_fields_changed(task: Task, spec_task: dict[str, Any]) -> bool:
    """¿El spec dice algo distinto de lo que la tarea tiene persistido?"""
    title = str(spec_task.get("title") or "").strip()[:200] or None
    description = spec_task.get("description")
    acceptance = spec_task.get("acceptance_criteria")
    return (
        (title is not None and title != task.title)
        or (isinstance(description, str) and description != task.description)
        or (isinstance(acceptance, list) and acceptance != (task.acceptance_criteria or []))
    )


def _apply_spec_to_task(
    task: Task, spec_task: dict[str, Any], role_agents: dict[PlanningRole, UUID] | None
) -> None:
    """Vuelca sobre una tarea NO EMPEZADA lo que dice el spec ahora."""
    title = str(spec_task.get("title") or "").strip()[:200]
    if title:
        task.title = title
    description = spec_task.get("description")
    if isinstance(description, str):
        task.description = description
    acceptance = spec_task.get("acceptance_criteria")
    if isinstance(acceptance, list):
        task.acceptance_criteria = acceptance
    complexity = spec_task.get("complexity")
    if isinstance(complexity, str) and complexity in {"xs", "s", "m", "l", "xl"}:
        task.estimated_complexity = complexity
    # El rol puede haber cambiado: reasignar por la MISMA vía que la creación,
    # o el replan dejaría la tarea con el agente del rol anterior.
    assigned, reviewer = _resolve_assignment(spec_task, role_agents)
    task.assigned_agent_id = assigned
    task.reviewer_agent_id = reviewer


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
        # NULL slot on an unresolved role is intentional (ADR 0091 D1 — the
        # dispatcher's load policy decides), but a typo shouldn't silently lose
        # its preset: leave an observable trace (c7).
        try:
            role = PlanningRole(role_str)
        except ValueError:
            _log.warning("sync_to_kanban.role_unknown", role=role_str, task=spec_task.get("id"))
        else:
            assigned = role_agents.get(role)
            if assigned is None:
                _log.warning(
                    "sync_to_kanban.role_without_team_agent",
                    role=role_str,
                    task=spec_task.get("id"),
                )
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
