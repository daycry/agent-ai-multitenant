"""Integration tests: push_review_to_bare (Plan 06 task_06_23 — step 1).

Worktree → bare-repo step that fires after a passing auto-review.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


def _setup(tmp_path: Path) -> tuple[Path, Path, str]:
    """Build (worktree, bare, plan_branch) ready for commit + push."""
    from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend")
    seed_bare_repo(bare)
    wt_mgr = WorktreeManager(layout, "backend")
    plan_branch = "plan/abc-foo"
    wt = wt_mgr.add("task-1", branch=plan_branch)
    return wt, bare, plan_branch


def test_push_after_commit_advances_bare_branch(tmp_path: Path) -> None:
    from workers.plan_git import CommitTrailers, PlanGitPolicies, PlanGitWorkflow, commit_task

    wt, bare, branch = _setup(tmp_path)

    # Snapshot before.
    proc = subprocess.run(
        ["git", "rev-parse", f"refs/heads/{branch}"],
        cwd=str(bare),
        check=True,
        capture_output=True,
        text=True,
    )
    sha_before = proc.stdout.strip()

    # Agent writes + commits.
    (wt / "new.py").write_text("print(1)")
    new_sha = commit_task(
        wt,
        message="task: implement",
        trailers=CommitTrailers(plan_id="p", task_id="t", execution_id="e"),
    )

    # Workflow pushes back to the bare's branch.
    wf = PlanGitWorkflow(
        bare_repo_path=bare,
        plan_branch=branch,
        policies=PlanGitPolicies(),
    )
    new_tip = wf.push_review_to_bare(wt)

    assert new_tip == new_sha
    assert new_tip != sha_before
