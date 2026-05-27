"""Integration tests: branch_push_mode='incremental' (Plan 06 task_06_23 — step 2)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


def _setup_with_origin(tmp_path: Path) -> tuple[Path, Path]:
    """Build local bare with origin pointing at a second 'remote' bare."""
    from workers.git_repos import BareRepoLayout, BareRepoManager

    # "Remote" bare with a main branch.
    remote_bare = tmp_path / "remote" / "backend.git"
    seed_bare_repo(remote_bare)

    layout = BareRepoLayout(data_root=tmp_path / "local", tenant_slug="t", project_slug="p")
    local_bare = BareRepoManager(layout).ensure_repo("backend", remote_url=str(remote_bare))
    return local_bare, remote_bare


def test_incremental_pushes_each_task_to_origin(tmp_path: Path) -> None:
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    local_bare, remote_bare = _setup_with_origin(tmp_path)
    # Fetch so the local bare knows about main.
    from workers.git_repos import _run_git

    _run_git("fetch", "origin", cwd=local_bare)

    # Create the plan branch on the local bare from origin/main.
    _run_git("update-ref", "refs/heads/plan/p1-foo", "refs/remotes/origin/main", cwd=local_bare)

    wf = PlanGitWorkflow(
        bare_repo_path=local_bare,
        plan_branch="plan/p1-foo",
        policies=PlanGitPolicies(branch_push_mode="incremental"),
    )
    did_push = wf.push_branch_to_remote()
    assert did_push is True

    # Remote bare now has plan/p1-foo.
    proc = subprocess.run(
        ["git", "rev-parse", "refs/heads/plan/p1-foo"],
        cwd=str(remote_bare),
        check=True,
        capture_output=True,
        text=True,
    )
    assert proc.stdout.strip()


def test_incremental_is_no_op_without_origin(tmp_path: Path) -> None:
    """Local-only project: incremental push is a no-op (no error)."""
    from workers.git_repos import BareRepoLayout, BareRepoManager
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend")
    seed_bare_repo(bare)

    wf = PlanGitWorkflow(
        bare_repo_path=bare,
        plan_branch="plan/p1-foo",
        policies=PlanGitPolicies(branch_push_mode="incremental"),
    )
    assert wf.push_branch_to_remote() is False
