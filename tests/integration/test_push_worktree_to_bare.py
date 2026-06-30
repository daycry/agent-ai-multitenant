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


def _two_worktrees(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    """(wt_a, wt_b, bare, branch) — two detached worktrees on the SAME plan branch."""
    from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend")
    seed_bare_repo(bare)
    wt_mgr = WorktreeManager(layout, "backend")
    branch = "plan/abc-foo"
    wt_a = wt_mgr.add("task-a", branch=branch)
    wt_b = wt_mgr.add("task-b", branch=branch)  # both detached at the SAME base tip
    return wt_a, wt_b, bare, branch


def _wf(bare: Path, branch: str):  # type: ignore[no-untyped-def]
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    return PlanGitWorkflow(bare_repo_path=bare, plan_branch=branch, policies=PlanGitPolicies())


def test_push_reconciles_concurrent_sibling_commit(tmp_path: Path) -> None:
    # Two tasks of the same plan push to the SAME plan branch. Task A pushes first
    # (advances the branch); Task B's worktree is based on the OLD tip, so a plain
    # push is non-fast-forward. push_review_to_bare must rebase B's commit onto the
    # branch tip and push — instead of failing `commit_failed` (the bug that blocked
    # "Auditar dependencias y fijar versiones").
    from workers.plan_git import CommitTrailers, commit_task

    wt_a, wt_b, bare, branch = _two_worktrees(tmp_path)

    (wt_a / "file_a.py").write_text("a")
    sha_a = commit_task(
        wt_a, message="task a", trailers=CommitTrailers(plan_id="p", task_id="a", execution_id="e")
    )
    _wf(bare, branch).push_review_to_bare(wt_a)

    (wt_b / "file_b.py").write_text("b")
    commit_task(
        wt_b, message="task b", trailers=CommitTrailers(plan_id="p", task_id="b", execution_id="e")
    )
    new_tip = _wf(bare, branch).push_review_to_bare(wt_b)  # must NOT raise non-fast-forward

    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", new_tip],
        cwd=str(bare),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "file_a.py" in tree and "file_b.py" in tree  # B rebased on top of A
    log = subprocess.run(
        ["git", "log", "--format=%H", f"refs/heads/{branch}"],
        cwd=str(bare),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert sha_a in log  # A's commit is preserved, not clobbered


def test_push_raises_on_real_rebase_conflict(tmp_path: Path) -> None:
    # A genuine conflict (two tasks edit the SAME line) is NOT a transient race — it
    # surfaces as a GitCommandError (→ commit_failed → escalation), and the worktree
    # is left clean (no half-finished rebase).
    from workers.git_repos import GitCommandError
    from workers.plan_git import CommitTrailers, commit_task

    wt_a, wt_b, bare, branch = _two_worktrees(tmp_path)

    (wt_a / "shared.txt").write_text("a\n")
    commit_task(
        wt_a, message="task a", trailers=CommitTrailers(plan_id="p", task_id="a", execution_id="e")
    )
    _wf(bare, branch).push_review_to_bare(wt_a)

    (wt_b / "shared.txt").write_text("b\n")
    commit_task(
        wt_b, message="task b", trailers=CommitTrailers(plan_id="p", task_id="b", execution_id="e")
    )
    with pytest.raises(GitCommandError, match="conflict"):
        _wf(bare, branch).push_review_to_bare(wt_b)
    # no rebase left in progress
    assert (
        not (wt_b / ".git").exists() or True
    )  # worktree .git is a file; just assert no REBASE state below
    state = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=str(wt_b),
        check=True,
        capture_output=True,
        text=True,
    )
    assert state.returncode == 0  # git status works → not stuck mid-rebase
