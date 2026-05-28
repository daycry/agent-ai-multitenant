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


def test_direct_to_default_fast_forwards_main(tmp_path: Path) -> None:
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    bare = _bare_with_plan_branch(tmp_path)
    plan_tip = subprocess.run(
        ["git", "rev-parse", "refs/heads/plan/m1-x"],
        cwd=str(bare),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    wf = PlanGitWorkflow(
        bare_repo_path=bare,
        plan_branch="plan/m1-x",
        policies=PlanGitPolicies(push_policy="direct_to_default_allowed"),
    )
    action = wf.apply_push_policy()
    assert action == "merged_to_default"

    main_tip = subprocess.run(
        ["git", "rev-parse", "refs/heads/main"],
        cwd=str(bare),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert main_tip == plan_tip
