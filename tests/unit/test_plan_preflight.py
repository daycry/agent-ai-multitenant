"""Preflight del plan antes de aprobar (`task_wf_72`).

Aprobar era un acto de fe: una tarea con un rol que el equipo no tiene, otra sin
criterios, un camino crítico que serializa todo el trabajo… todo eso aparecía
DESPUÉS, con el plan corriendo y costando desbloquear tareas una a una.

El preflight no valida nada nuevo — compone en seco los resolvedores que ya
deciden en producción. Que sean los MISMOS es el punto: un preflight que dijera
algo distinto de lo que luego hace el sistema sería peor que no tenerlo.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from api_server.chat.planning_graph import PlanningRole
from api_server.plan_preflight import BLOCKER, WARNING, run_plan_preflight

pytestmark = pytest.mark.unit

_BACKEND = uuid4()
_TEAM = {PlanningRole.BACKEND_DEV: _BACKEND, PlanningRole.REVIEWER: uuid4()}


def _task(tid: str, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": tid,
        "title": f"Tarea {tid}",
        "role": "backend_dev",
        "acceptance_criteria": ["hace algo verificable"],
        "depends_on": [],
    }
    base.update(over)
    return base


def _codes(report: object) -> set[str]:
    return {f.code for f in report.findings}  # type: ignore[attr-defined]


def test_a_healthy_plan_reports_nothing() -> None:
    report = run_plan_preflight({"tasks": [_task("t1"), _task("t2")]}, role_agents=_TEAM)
    assert report.findings == []
    assert report.blockers == 0


def test_an_empty_plan_is_a_blocker() -> None:
    # Aprobarlo no pondría a nadie a trabajar: el plan diría «aprobado» y no
    # pasaría nada, que es el fallo más desconcertante de todos.
    report = run_plan_preflight({"tasks": []}, role_agents=_TEAM)
    assert _codes(report) == {"no_tasks"}
    assert report.blockers == 1


# ---------------------------------------------------------------------------
# Asignación por rol — el hallazgo que motiva la tarea
# ---------------------------------------------------------------------------
def test_a_role_the_team_does_not_have_is_a_blocker() -> None:
    # Es la sorpresa clásica: se aprueba, se materializa sin agente y lo reparte
    # la política de carga — no el rol que se pidió.
    report = run_plan_preflight(
        {"tasks": [_task("t1"), _task("t2", role="devops")]}, role_agents=_TEAM
    )
    assert "role_without_agent" in _codes(report)
    finding = next(f for f in report.findings if f.code == "role_without_agent")
    assert finding.severity == BLOCKER
    # Señala la tarea CONCRETA: un aviso sin diana obliga a buscarla a mano.
    assert finding.task_ids == ("t2",)


def test_an_unknown_role_string_counts_as_unassignable() -> None:
    report = run_plan_preflight({"tasks": [_task("t1", role="bakend_dev")]}, role_agents=_TEAM)
    assert "role_without_agent" in _codes(report)


def test_a_project_without_a_team_flags_every_task_with_a_role() -> None:
    report = run_plan_preflight({"tasks": [_task("t1"), _task("t2")]}, role_agents=None)
    finding = next(f for f in report.findings if f.code == "role_without_agent")
    assert set(finding.task_ids) == {"t1", "t2"}


def test_a_task_without_a_role_is_only_a_warning() -> None:
    # Sin rol la política de carga decide, que es un camino legítimo (ADR 0091):
    # avisa, no bloquea.
    report = run_plan_preflight({"tasks": [_task("t1", role="")]}, role_agents=_TEAM)
    finding = next(f for f in report.findings if f.code == "task_without_role")
    assert finding.severity == WARNING


# ---------------------------------------------------------------------------
# Criterios de aceptación
# ---------------------------------------------------------------------------
def test_a_task_without_criteria_is_flagged() -> None:
    # El reviewer certifica contra los criterios: sin ellos juzga contra la
    # descripción, que es más ambigua y produce rechazos en bucle.
    report = run_plan_preflight(
        {"tasks": [_task("t1"), _task("t2", acceptance_criteria=[])]}, role_agents=_TEAM
    )
    finding = next(f for f in report.findings if f.code == "task_without_criteria")
    assert finding.task_ids == ("t2",)


def test_blank_criteria_do_not_count_as_criteria() -> None:
    report = run_plan_preflight(
        {"tasks": [_task("t1", acceptance_criteria=["   ", ""])]}, role_agents=_TEAM
    )
    assert "task_without_criteria" in _codes(report)


# ---------------------------------------------------------------------------
# Forma del DAG
# ---------------------------------------------------------------------------
def test_a_cycle_is_a_blocker_and_names_the_chain() -> None:
    report = run_plan_preflight(
        {
            "tasks": [
                _task("t1", depends_on=["t2"]),
                _task("t2", depends_on=["t1"]),
            ]
        },
        role_agents=_TEAM,
    )
    finding = next(f for f in report.findings if f.code == "dag_cycle")
    assert finding.severity == BLOCKER
    assert "t1" in finding.task_ids


def test_the_critical_path_is_the_longest_chain() -> None:
    # Es el suelo de la duración del plan: por muchos agentes que haya, esas
    # tareas van en serie.
    report = run_plan_preflight(
        {
            "tasks": [
                _task("a"),
                _task("b", depends_on=["a"]),
                _task("c", depends_on=["b"]),
                _task("suelta"),
            ]
        },
        role_agents=_TEAM,
    )
    assert report.critical_path == ("a", "b", "c")


def test_a_single_file_plan_is_flagged_as_serial() -> None:
    # Un plan en fila india tarda lo mismo con un agente que con diez. Se puede
    # aprobar, pero conviene saberlo ANTES de esperar paralelismo.
    report = run_plan_preflight(
        {"tasks": [_task("a"), _task("b", depends_on=["a"]), _task("c", depends_on=["b"])]},
        role_agents=_TEAM,
    )
    assert "no_parallelism" in _codes(report)
    assert report.max_parallelism == 1


def test_independent_tasks_report_their_parallelism() -> None:
    report = run_plan_preflight(
        {"tasks": [_task("a"), _task("b"), _task("c", depends_on=["a"])]}, role_agents=_TEAM
    )
    assert report.max_parallelism == 2
    assert "no_parallelism" not in _codes(report)


def test_a_single_task_plan_is_not_flagged_as_serial() -> None:
    # Una sola tarea no puede paralelizarse con nada: avisar sería ruido.
    report = run_plan_preflight({"tasks": [_task("a")]}, role_agents=_TEAM)
    assert "no_parallelism" not in _codes(report)


# ---------------------------------------------------------------------------
# El preflight NO muta
# ---------------------------------------------------------------------------
def test_the_specification_is_left_untouched() -> None:
    spec = {"tasks": [_task("t1", role="devops", acceptance_criteria=[])]}
    import copy

    before = copy.deepcopy(spec)
    run_plan_preflight(spec, role_agents=_TEAM)
    assert spec == before
