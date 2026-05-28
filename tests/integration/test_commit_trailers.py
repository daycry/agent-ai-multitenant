"""Integration tests: commits with mandatory trailers (Plan 06 task_06_22)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


def _worktree(tmp_path: Path) -> Path:
    from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend")
    seed_bare_repo(bare)
    wt_mgr = WorktreeManager(layout, "backend")
    return wt_mgr.add("task-1", branch="plan/abc-foo")


def test_commit_carries_all_four_trailers(tmp_path: Path) -> None:
    from workers.plan_git import CommitTrailers, commit_task

    wt = _worktree(tmp_path)
    (wt / "new.txt").write_text("hello")

    sha = commit_task(
        wt,
        message="task: do the thing",
        trailers=CommitTrailers(
            plan_id="11111111",
            task_id="task-1",
            execution_id="exec-9",
        ),
    )
    assert sha

    proc = subprocess.run(
        ["git", "log", "-1", "--format=%B", sha],
        cwd=str(wt),
        check=True,
        capture_output=True,
        text=True,
    )
    body = proc.stdout
    assert "task: do the thing" in body
    assert "Plan-Id: 11111111" in body
    assert "Task-Id: task-1" in body
    assert "Execution-Id: exec-9" in body
    assert "Generated-By: agentic-platform" in body


def test_commit_with_custom_generated_by(tmp_path: Path) -> None:
    from workers.plan_git import CommitTrailers, commit_task

    wt = _worktree(tmp_path)
    (wt / "x.txt").write_text("x")

    commit_task(
        wt,
        message="custom signer",
        trailers=CommitTrailers(
            plan_id="p",
            task_id="t",
            execution_id="e",
            generated_by="agent-runtime/test",
        ),
    )
    proc = subprocess.run(
        ["git", "log", "-1", "--format=%B"],
        cwd=str(wt),
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Generated-By: agent-runtime/test" in proc.stdout


def test_commit_on_clean_worktree_raises(tmp_path: Path) -> None:
    """No file changes → nothing to commit. The worker treats this as
    "task produced no code change" and shouldn't push anything."""
    from workers.git_repos import GitCommandError
    from workers.plan_git import CommitTrailers, commit_task

    wt = _worktree(tmp_path)
    with pytest.raises(GitCommandError, match="clean"):
        commit_task(
            wt,
            message="nothing",
            trailers=CommitTrailers(plan_id="p", task_id="t", execution_id="e"),
        )


def test_commit_author_can_be_overridden(tmp_path: Path) -> None:
    from workers.plan_git import CommitTrailers, commit_task

    wt = _worktree(tmp_path)
    (wt / "f.txt").write_text("x")
    commit_task(
        wt,
        message="m",
        trailers=CommitTrailers(plan_id="p", task_id="t", execution_id="e"),
        author_name="Backend Bot",
        author_email="backend@bots.test",
    )
    proc = subprocess.run(
        ["git", "log", "-1", "--format=%an <%ae>"],
        cwd=str(wt),
        check=True,
        capture_output=True,
        text=True,
    )
    assert proc.stdout.strip() == "Backend Bot <backend@bots.test>"
