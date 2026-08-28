"""Static meta-tests that pin CI workflow invariants (Plan prod-02).

The audit (2026-06-10) found the CI harness functionally dead: the three
workflows triggered on ``main`` while the repo's default branch is
``master``, so real PRs ran *no* CI at all (finding tests-1), and the
pipeline had been red for ~19 consecutive runs yet kept being merged
(tests-2). These tests guard against silent re-degradation of the harness:
they parse the workflow YAML directly, so they run on any machine without a
GitHub runner.

Grown incrementally by prod-02:
- task_prod_02_01 (this commit): trigger branches + manual dispatch.
- task_prod_02_06: coverage gate (``--cov-fail-under``) present.
- task_prod_02_11: every job declares ``timeout-minutes``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# Decisión clave 4 del plan prod-02: los workflows disparan solo sobre la rama
# por defecto (master) y las ramas de plan (plan/**). Mantener `main` solo
# conservaría una rama muerta como falsa señal de CI.
DEFAULT_BRANCH = "master"
ALLOWED_TRIGGER_BRANCHES = {"master", "plan/**"}


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name}: top-level YAML is not a mapping"
    # YAML 1.1 (PyYAML's resolver) parses the bare key ``on:`` as the boolean
    # True. Normalise it back to the string key the rest of the test expects.
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


def _branch_lists(on_block: Any) -> list[tuple[str, list[str]]]:
    """Return (event, branches) for each push/pull_request trigger present."""
    out: list[tuple[str, list[str]]] = []
    if not isinstance(on_block, dict):
        return out
    for event in ("push", "pull_request"):
        ev = on_block.get(event)
        if isinstance(ev, dict) and "branches" in ev:
            branches = ev["branches"]
            if isinstance(branches, str):
                branches = [branches]
            out.append((event, list(branches)))
    return out


def _on(data: dict[str, Any]) -> Any:
    return data.get("on")


@pytest.fixture(scope="module")
def workflows() -> dict[str, dict[str, Any]]:
    files = _workflow_files()
    assert files, f"no workflow files found under {WORKFLOWS_DIR}"
    return {p.name: _load(p) for p in files}


def test_triggers_have_no_stale_branches(workflows: dict[str, dict[str, Any]]) -> None:
    """No workflow may trigger on a branch outside the allowlist (catches the
    `main` → `master` regression that left CI dead for 12 days)."""
    problems: list[str] = []
    for name, data in workflows.items():
        for event, branches in _branch_lists(_on(data)):
            for branch in branches:
                if branch not in ALLOWED_TRIGGER_BRANCHES:
                    problems.append(
                        f"{name}: {event} triggers on '{branch}' — not in "
                        f"{sorted(ALLOWED_TRIGGER_BRANCHES)} (stale branch?)"
                    )
    assert not problems, "Stale CI trigger branches:\n" + "\n".join(problems)


def test_triggers_target_default_branch(workflows: dict[str, dict[str, Any]]) -> None:
    """Every workflow with push/pull_request triggers must fire on the default
    branch (master), or real PRs run no CI."""
    problems: list[str] = []
    for name, data in workflows.items():
        branch_lists = _branch_lists(_on(data))
        if not branch_lists:
            continue  # workflow not branch-triggered (e.g. schedule only)
        covers_default = any(DEFAULT_BRANCH in branches for _event, branches in branch_lists)
        if not covers_default:
            problems.append(
                f"{name}: no push/pull_request trigger includes "
                f"'{DEFAULT_BRANCH}' — PRs to the default branch run no CI"
            )
    assert not problems, "Workflows that ignore the default branch:\n" + "\n".join(problems)


def test_triggers_allow_manual_dispatch(workflows: dict[str, dict[str, Any]]) -> None:
    """Every workflow must be manually triggerable (workflow_dispatch) so an
    operator can re-run a gate without pushing a commit."""
    problems: list[str] = []
    for name, data in workflows.items():
        on_block = _on(data)
        if not isinstance(on_block, dict) or "workflow_dispatch" not in on_block:
            problems.append(f"{name}: missing 'workflow_dispatch' trigger")
    assert not problems, "Workflows without manual dispatch:\n" + "\n".join(problems)


def test_integration_job_loads_apparmor_profile() -> None:
    """The test-integration job must load the agentic-default AppArmor profile
    before `docker compose up`, or the stack aborts on the runner with
    "AppArmor profile agentic-default not found" — the regression that left the
    cross-tenant gate unexecuted for 12 days (finding tests-3)."""
    ci = _load(WORKFLOWS_DIR / "ci.yml")
    job = ci.get("jobs", {}).get("test-integration")
    assert job is not None, "ci.yml has no 'test-integration' job"
    run_blocks = "\n".join(
        step.get("run", "") for step in job.get("steps", []) if isinstance(step, dict)
    )
    assert "apparmor_parser" in run_blocks and "agentic-default" in run_blocks, (
        "test-integration must load the agentic-default AppArmor profile "
        "(apparmor_parser -r -W docker/apparmor/agentic-default.profile) before "
        "`docker compose up`, or the integration stack will not start in CI"
    )


def test_every_job_declares_a_timeout(workflows: dict[str, dict[str, Any]]) -> None:
    """Every job must set `timeout-minutes`. GitHub's default is 6h, so a hung
    job (deadlocked test, stuck `docker compose up --wait`) would burn a runner
    for hours before being killed (finding tests-8)."""
    problems: list[str] = []
    for name, data in workflows.items():
        for job_name, job in (data.get("jobs") or {}).items():
            if isinstance(job, dict) and "timeout-minutes" not in job:
                problems.append(f"{name}:{job_name}")
    assert not problems, "jobs without timeout-minutes (default 6h): " + ", ".join(problems)


def test_unit_job_enforces_a_coverage_floor() -> None:
    """The unit-test job must run pytest with `--cov-fail-under` so coverage
    cannot silently rot (findings tests-5 / quality-6). Pins that the gate EXISTS
    AND that its ratchet floor never drops below the measured baseline (M9): the
    old test only checked the string existed, which let the floor sit frozen at 19
    for a month ~11 points below reality. The floor is a ratchet raised over time
    toward conventions.md (70%/80%); this guard makes lowering it a red test."""
    import re

    ci = _load(WORKFLOWS_DIR / "ci.yml")
    job = ci.get("jobs", {}).get("test-unit")
    assert job is not None, "ci.yml has no 'test-unit' job"
    run_blocks = "\n".join(
        step.get("run", "") for step in job.get("steps", []) if isinstance(step, dict)
    )
    match = re.search(r"--cov-fail-under=(\d+)", run_blocks)
    assert match is not None, (
        "the test-unit job must gate coverage with pytest --cov-fail-under=<floor>"
    )
    # Ratchet floor: 30.4% (2026-07-07) → 31%+ (2026-07-09, tras los tests puros de
    # detect_outliers, hallazgo #8). Never lower — raise toward conventions.md (70/80).
    assert int(match.group(1)) >= 31, (
        f"coverage ratchet floor {match.group(1)} is below the 31 baseline — "
        "raise it toward conventions.md (70%/80%), never lower it"
    )


def test_unit_job_runs_the_agent_runtime_suite() -> None:
    """Los tests del agent-runtime (docker/agent-runtimes/agent-runtime/tests) deben
    correr en CI (hallazgo #6, PASO 0): viven fuera de ``tests/unit`` — el job los
    ignoraba, así que el contrato de review (tag <verdict>, F32, wire-format) y la
    señal de truncado del claude_sdk (#10c) quedaban SIN protección. Este guard hace
    de olvidarlos un test rojo. El paquete ya se instala editable en el job."""
    ci = _load(WORKFLOWS_DIR / "ci.yml")
    job = ci.get("jobs", {}).get("test-unit")
    assert job is not None, "ci.yml has no 'test-unit' job"
    run_blocks = "\n".join(
        step.get("run", "") for step in job.get("steps", []) if isinstance(step, dict)
    )
    assert "docker/agent-runtimes/agent-runtime/tests" in run_blocks, (
        "the test-unit job must run the agent-runtime suite "
        "(pytest docker/agent-runtimes/agent-runtime/tests) — those tests pin the "
        "review verdict contract and F32/truncation signals and live outside tests/unit"
    )


# ---------------------------------------------------------------------------
# El nocturno de instalación (2026-08-27). Los tres tests de arriba exigen
# forma —dispatch, timeout, ramas—; ninguno exige que un job EJECUTE algo, que
# es justo lo que le faltaba a este e2e: existe desde el 2026-06-17 y nunca se
# ha ejecutado, porque su gate (`E2E_INSTALL=1`) no lo ponía nadie y el skip se
# leía como aprobado. El precio fueron las once rutas que el compose generado
# montaba y nadie creaba, invisibles durante meses.
# ---------------------------------------------------------------------------
_INSTALL_E2E = "install-e2e.yml"
_E2E_MODULE = "tests/e2e/test_install_from_scratch.py"
_E2E_GATE_SCRIPT = "scripts/check_e2e_install_report.py"


def _install_e2e_steps(workflows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    data = workflows.get(_INSTALL_E2E)
    assert data is not None, (
        f".github/workflows/{_INSTALL_E2E} no existe: sin él, el e2e de instalación "
        "vuelve a no ejecutarse en ninguna parte"
    )
    jobs = data.get("jobs") or {}
    steps = [
        step
        for job in jobs.values()
        if isinstance(job, dict)
        for step in (job.get("steps") or [])
        if isinstance(step, dict)
    ]
    assert len(steps) >= 5, f"{_INSTALL_E2E} sólo declara {len(steps)} pasos: ¿se vació?"
    return steps


def test_install_e2e_actually_turns_the_gate_on(workflows: dict[str, dict[str, Any]]) -> None:
    """El workflow exporta `E2E_INSTALL=1` y corre el módulo del e2e.

    Sin la variable, las fixtures de `tests/e2e/conftest.py` hacen `pytest.skip`
    en el SETUP: los cuatro casos se recolectan, se saltan y pytest sale 0. El
    valor importa, no sólo el nombre — un `E2E_INSTALL=0` dejaría el workflow
    con toda la pinta de estar cableado.
    """
    runs = "\n".join(step.get("run", "") for step in _install_e2e_steps(workflows))

    assert "E2E_INSTALL=1" in runs, (
        f"{_INSTALL_E2E} no exporta E2E_INSTALL=1 en ningún paso: el e2e de "
        "instalación se saltaría en verde, que es el defecto que este workflow cierra"
    )
    assert _E2E_MODULE in runs, (
        f"{_INSTALL_E2E} no invoca {_E2E_MODULE}: el workflow existiría sin correr "
        "el único test que prueba que la instalación funciona"
    )


def test_install_e2e_verdict_is_checked_against_the_machine_report(
    workflows: dict[str, dict[str, Any]],
) -> None:
    """pytest sale 0 tanto si los cuatro casos pasaron como si se saltaron, así
    que el veredicto lo da el informe JUnit. Hacen falta las dos mitades: que el
    XML se emita y que alguien lo lea."""
    steps = _install_e2e_steps(workflows)
    runs = "\n".join(step.get("run", "") for step in steps)

    assert "--junitxml" in runs, (
        f"{_INSTALL_E2E} no emite informe JUnit: sin él no hay forma máquina de "
        "distinguir «los cuatro casos pasaron» de «los cuatro se saltaron»"
    )
    gate = [step for step in steps if _E2E_GATE_SCRIPT in step.get("run", "")]
    assert gate, (
        f"{_INSTALL_E2E} no invoca {_E2E_GATE_SCRIPT}: el gate anti-falso-verde "
        "existe pero no lo llama nadie"
    )
    assert any(str(step.get("if", "")).strip() == "always()" for step in gate), (
        f"el paso que corre {_E2E_GATE_SCRIPT} necesita `if: always()`. Sin él sólo "
        "hablaría cuando pytest ya salió 0 — y no distinguiría «ejecutó y falló» de "
        "«no ejecutó», que es la distinción entera"
    )


def test_install_e2e_has_no_escape_hatches(workflows: dict[str, dict[str, Any]]) -> None:
    """Un `continue-on-error` o un `|| true` en la cadena del veredicto devolvería
    el falso verde por la puerta de atrás, con mejor presentación."""
    data = workflows[_INSTALL_E2E]
    jobs = data.get("jobs") or {}
    culpables: list[str] = []
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        if job.get("continue-on-error"):
            culpables.append(f"job {job_name}")
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            nombre = step.get("name") or step.get("uses") or "(sin nombre)"
            if step.get("continue-on-error"):
                culpables.append(f"paso «{nombre}»: continue-on-error")
            run = step.get("run", "")
            en_la_cadena_del_veredicto = _E2E_MODULE in run or _E2E_GATE_SCRIPT in run
            if en_la_cadena_del_veredicto and ("|| true" in run or "|| echo" in run):
                culpables.append(f"paso «{nombre}»: escapatoria en el `run`")

    assert not culpables, (
        f"{_INSTALL_E2E} tiene escapatorias que anularían el veredicto: {culpables}"
    )


def test_install_e2e_still_runs_on_a_schedule(workflows: dict[str, dict[str, Any]]) -> None:
    """Sin `schedule` el nocturno deja de ser nocturno y pasa a depender de que
    alguien se acuerde de pulsarlo — que es la forma lenta de volver al punto de
    partida, sólo que esta vez con un fichero YAML que aparenta lo contrario."""
    on_block = _on(workflows[_INSTALL_E2E])
    assert isinstance(on_block, dict) and on_block.get("schedule"), (
        f"{_INSTALL_E2E} ya no tiene disparador `schedule`"
    )
