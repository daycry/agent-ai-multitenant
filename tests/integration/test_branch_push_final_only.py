"""Integration tests: branch_push_mode='final_only' (Plan 06 task_06_23 — step 3)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    from workers.git_repos import BareRepoLayout, BareRepoManager

    remote_bare = tmp_path / "remote" / "backend.git"
    seed_bare_repo(remote_bare)

    layout = BareRepoLayout(data_root=tmp_path / "local", tenant_slug="t", project_slug="p")
    local_bare = BareRepoManager(layout).ensure_repo("backend", remote_url=str(remote_bare))
    from workers.git_repos import _run_git

    _run_git("fetch", "origin", cwd=local_bare)
    _run_git(
        "update-ref",
        "refs/heads/plan/p1-foo",
        "refs/remotes/origin/main",
        cwd=local_bare,
    )
    return local_bare, remote_bare


def test_final_only_skips_per_task_push(tmp_path: Path) -> None:
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    local_bare, remote_bare = _setup(tmp_path)
    wf = PlanGitWorkflow(
        bare_repo_path=local_bare,
        plan_branch="plan/p1-foo",
        policies=PlanGitPolicies(branch_push_mode="final_only"),
    )
    # First push call (per-task) is a no-op.
    assert wf.push_branch_to_remote() is False
    # Remote bare doesn't see plan/p1-foo yet.
    proc = subprocess.run(
        ["git", "branch", "--list", "plan/p1-foo"],
        cwd=str(remote_bare),
        check=True,
        capture_output=True,
        text=True,
    )
    assert proc.stdout.strip() == ""


def test_final_only_pushes_when_forced_at_close(tmp_path: Path) -> None:
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    local_bare, remote_bare = _setup(tmp_path)
    wf = PlanGitWorkflow(
        bare_repo_path=local_bare,
        plan_branch="plan/p1-foo",
        policies=PlanGitPolicies(branch_push_mode="final_only"),
    )
    # End-of-plan: force=True bypasses the policy.
    assert wf.push_branch_to_remote(force=True) is True
    proc = subprocess.run(
        ["git", "rev-parse", "refs/heads/plan/p1-foo"],
        cwd=str(remote_bare),
        check=True,
        capture_output=True,
        text=True,
    )
    assert proc.stdout.strip()
