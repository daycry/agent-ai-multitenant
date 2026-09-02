"""Integration tests: push_policy at merge time (Plan 06 task_06_25)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


def _bare_with_plan_branch(tmp_path: Path) -> Path:
    from workers.git_repos import BareRepoLayout, BareRepoManager, _run_git

    from tests.integration._git_helpers import commit_to_branch

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend")
    seed_bare_repo(bare)
    # Plan branch with one commit on top of main.
    sha = commit_to_branch(bare, "plan/m1-x", filename="feat.py", content="x")
    _ = sha  # - kept for symmetry
    _run_git("update-ref", "refs/heads/plan/m1-x", "refs/heads/plan/m1-x", cwd=bare)
    return bare


def test_forbidden_returns_forbidden_and_does_nothing(tmp_path: Path) -> None:
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    bare = _bare_with_plan_branch(tmp_path)
    main_before = subprocess.run(
        ["git", "rev-parse", "refs/heads/main"],
        cwd=str(bare),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    wf = PlanGitWorkflow(
        bare_repo_path=bare,
        plan_branch="plan/m1-x",
        policies=PlanGitPolicies(push_policy="forbidden"),
    )
    action = wf.apply_push_policy()
    assert action == "forbidden"
    main_after = subprocess.run(
        ["git", "rev-parse", "refs/heads/main"],
        cwd=str(bare),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert main_after == main_before  # default branch unchanged


def test_pr_required_returns_pr_required_no_merge(tmp_path: Path) -> None:
    """branch_only_pr_required keeps the PR open for the human; the
    bare's default branch must not advance."""
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    bare = _bare_with_plan_branch(tmp_path)
    main_before = subprocess.run(
        ["git", "rev-parse", "refs/heads/main"],
        cwd=str(bare),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    wf = PlanGitWorkflow(
        bare_repo_path=bare,
        plan_branch="plan/m1-x",
        policies=PlanGitPolicies(push_policy="branch_only_pr_required"),
    )
    action = wf.apply_push_policy()
    assert action == "pr_required"
    main_after = subprocess.run(
        ["git", "rev-parse", "refs/heads/main"],
        cwd=str(bare),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert main_after == main_before


def test_direct_to_default_was_retired(tmp_path: Path) -> None:
    """`task_cv_45` (G-03, auditoría 2026-09-01): `direct_to_default_allowed` era
    una política fantasma —sin llamantes en producción— cuyo `update-ref` sin
    guard fast-forward habría retrocedido `main`. Un plan acaba siempre en PR
    (principio rector 5); la política se rechaza al construirla."""
    from workers.plan_git import PlanGitPolicies

    with pytest.raises(ValueError, match="direct_to_default_allowed"):
        PlanGitPolicies(push_policy="direct_to_default_allowed")  # type: ignore[arg-type]
