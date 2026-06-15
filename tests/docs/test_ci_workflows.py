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
