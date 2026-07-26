"""Semáforo de solo-lectura antes de aprobar un plan (`task_wf_72`).

Aprobar un plan era un acto de fe: los problemas —una tarea con un rol que el
equipo no tiene, otra sin criterios de aceptación, un camino crítico que hace
serie todo el trabajo— aparecían DESPUÉS, cuando el plan ya estaba corriendo y
arreglarlo costaba desbloquear tareas una a una.

Este módulo no valida nada nuevo: **compone resolvedores que ya existen** en
modo seco. La asignación por rol es la misma de `sync_to_kanban`, el DAG el
mismo de `chat.dag`, el coste el mismo de `chat.cost`. Que sean los mismos es el
punto: un preflight que dijera algo distinto de lo que luego hace el sistema
sería peor que no tenerlo.

Es PURO — recibe el spec y el mapa de roles del equipo, y no escribe nada. El
router se limita a leer y llamar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from api_server.chat.dag import DAGCycleError, validate_dag
from api_server.chat.planning_graph import PlanningRole

# Severidades. `blocker` no impide aprobar —la decisión sigue siendo del
# humano— pero es lo que le va a costar una intervención manual después.
BLOCKER = "blocker"
WARNING = "warning"


@dataclass(frozen=True)
class PreflightFinding:
    """Un problema detectado, con las tareas concretas a las que señala."""

    code: str
    severity: str
    message: str
    task_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "task_ids": list(self.task_ids),
        }


@dataclass
class PreflightReport:
    findings: list[PreflightFinding] = field(default_factory=list)
    task_count: int = 0
    critical_path: tuple[str, ...] = ()
    max_parallelism: int = 0

    @property
    def blockers(self) -> int:
        return sum(1 for f in self.findings if f.severity == BLOCKER)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_count": self.task_count,
            "blockers": self.blockers,
            "warnings": len(self.findings) - self.blockers,
            "critical_path": list(self.critical_path),
            "critical_path_length": len(self.critical_path),
            "max_parallelism": self.max_parallelism,
            "findings": [f.as_dict() for f in self.findings],
        }


def _spec_tasks(specification: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (specification or {}).get("tasks") or []
    return [t for t in raw if isinstance(t, dict) and t.get("id")]


def _critical_path(tasks: list[dict[str, Any]]) -> tuple[str, ...]:
    """La cadena de dependencias más LARGA (en número de tareas).

    Es el suelo de la duración del plan: por muchos agentes que haya, esas
    tareas van en serie. Se calcula en número de tareas y no en horas a
    propósito — las horas estimadas son poco fiables (`task_wf_33` lo tratará),
    y una cadena de 8 ya dice lo que hay que decir.
    """
    by_id = {str(t["id"]): t for t in tasks}
    memo: dict[str, tuple[str, ...]] = {}
    visiting: set[str] = set()

    def longest(task_id: str) -> tuple[str, ...]:
        if task_id in memo:
            return memo[task_id]
        if task_id in visiting:  # ciclo: lo reporta `validate_dag`, aquí se corta
            return ()
        visiting.add(task_id)
        best: tuple[str, ...] = ()
        for dep in by_id.get(task_id, {}).get("depends_on") or []:
            if str(dep) in by_id:
                candidate = longest(str(dep))
                if len(candidate) > len(best):
                    best = candidate
        visiting.discard(task_id)
        memo[task_id] = (*best, task_id)
        return memo[task_id]

    paths = [longest(tid) for tid in by_id]
    return max(paths, key=len) if paths else ()


def _max_parallelism(tasks: list[dict[str, Any]]) -> int:
    """Cuántas tareas pueden correr a la vez en el mejor momento del plan.

    Por niveles del DAG: el nivel de una tarea es 1 + el máximo de sus
    dependencias. Un plan cuyo máximo es 1 es una fila india — se puede aprobar,
    pero conviene saberlo ANTES de esperar a que N agentes lo hagan rápido.
    """
    by_id = {str(t["id"]): t for t in tasks}
    level: dict[str, int] = {}

    def depth(task_id: str, seen: frozenset[str] = frozenset()) -> int:
        if task_id in level:
            return level[task_id]
        if task_id in seen:  # ciclo
            return 0
        deps = [str(d) for d in (by_id.get(task_id, {}).get("depends_on") or []) if str(d) in by_id]
        value = 1 + max((depth(d, seen | {task_id}) for d in deps), default=0)
        level[task_id] = value
        return value

    for tid in by_id:
        depth(tid)
    if not level:
        return 0
    counts: dict[int, int] = {}
    for value in level.values():
        counts[value] = counts.get(value, 0) + 1
    return max(counts.values())


def run_plan_preflight(
    specification: dict[str, Any] | None,
    *,
    role_agents: dict[PlanningRole, UUID] | None,
) -> PreflightReport:
    """El informe de preflight de un plan. No muta nada.

    ``role_agents`` es el mapa rol→agente del equipo del proyecto, el MISMO que
    usa `sync_to_kanban` al materializar: si aquí dijera que un rol resuelve y
    allí no, el preflight mentiría.
    """
    tasks = _spec_tasks(specification)
    report = PreflightReport(task_count=len(tasks))

    if not tasks:
        report.findings.append(
            PreflightFinding(
                code="no_tasks",
                severity=BLOCKER,
                message="El plan no tiene ninguna tarea: aprobarlo no pondría a nadie a trabajar.",
            )
        )
        return report

    # --- DAG ----------------------------------------------------------
    try:
        validate_dag(tasks)
    except DAGCycleError as exc:
        report.findings.append(
            PreflightFinding(
                code="dag_cycle",
                severity=BLOCKER,
                message=(
                    "Hay una dependencia circular: "
                    f"{' → '.join(exc.cycle)}. Ninguna de esas tareas podría empezar nunca."
                ),
                task_ids=tuple(exc.cycle),
            )
        )
    else:
        report.critical_path = _critical_path(tasks)
        report.max_parallelism = _max_parallelism(tasks)
        if len(tasks) > 1 and report.max_parallelism == 1:
            report.findings.append(
                PreflightFinding(
                    code="no_parallelism",
                    severity=WARNING,
                    message=(
                        f"Las {len(tasks)} tareas van en fila india: cada una espera a la "
                        "anterior. El plan tardará lo mismo con un agente que con diez."
                    ),
                )
            )

    # --- Asignación por rol --------------------------------------------
    unassignable: list[str] = []
    roleless: list[str] = []
    for task in tasks:
        role_str = str(task.get("role") or "").strip()
        if not role_str:
            roleless.append(str(task["id"]))
            continue
        try:
            role = PlanningRole(role_str)
        except ValueError:
            unassignable.append(str(task["id"]))
            continue
        if not role_agents or role_agents.get(role) is None:
            unassignable.append(str(task["id"]))
    if unassignable:
        report.findings.append(
            PreflightFinding(
                code="role_without_agent",
                severity=BLOCKER,
                message=(
                    f"{len(unassignable)} tarea(s) piden un rol que el equipo del proyecto no "
                    "tiene: se materializarán sin agente y las repartirá la política de carga, "
                    "no el rol que pediste."
                ),
                task_ids=tuple(unassignable),
            )
        )
    if roleless:
        report.findings.append(
            PreflightFinding(
                code="task_without_role",
                severity=WARNING,
                message=(f"{len(roleless)} tarea(s) sin rol: las asignará la política de carga."),
                task_ids=tuple(roleless),
            )
        )

    # --- Criterios de aceptación ---------------------------------------
    no_criteria = [
        str(task["id"])
        for task in tasks
        if not [c for c in (task.get("acceptance_criteria") or []) if str(c).strip()]
    ]
    if no_criteria:
        report.findings.append(
            PreflightFinding(
                code="task_without_criteria",
                severity=WARNING,
                message=(
                    f"{len(no_criteria)} tarea(s) sin criterios de aceptación. El reviewer "
                    "certifica contra ellos: sin criterios juzga contra la descripción, que "
                    "es más ambigua y produce rechazos en bucle."
                ),
                task_ids=tuple(no_criteria),
            )
        )

    return report
