"""Integration tests: worktree sync to plan branch HEAD
(Plan 06 task_06_19 — sync part).

Before handing control to the agent, the worker runs
``WorktreeManager.sync_to_head(task, branch=plan_branch)``. After the
sync, the worktree must reflect the latest HEAD of the plan branch on
the bare (so sibling tasks' commits are visible) and have no leftover
files from a previous run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration._git_helpers import commit_to_branch, seed_bare_repo

pytestmark = pytest.mark.integration


def _setup(tmp_path: Path) -> object:
    from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend")
    seed_bare_repo(bare)
    return WorktreeManager(layout, "backend"), layout, bare


def test_sync_pulls_sibling_commits(tmp_path: Path) -> None:
    wt_mgr, layout, bare = _setup(tmp_path)
    # First task creates the plan branch.
    wt_mgr.add("task-a", branch="plan/feat")  # type: ignore[attr-defined]
    # Sibling pushes a commit on the same branch.
    sha = commit_to_branch(bare, "plan/feat", filename="from-sibling.txt", content="hi")

    # Now sync task-a — should see the new file.
    wt_mgr.sync_to_head("task-a", branch="plan/feat")  # type: ignore[attr-defined]
    wt_path = layout.worktree_path("task-a")  # type: ignore[attr-defined]
    assert (wt_path / "from-sibling.txt").read_text() == "hi"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(wt_path),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == sha


def test_sync_resets_local_uncommitted_changes(tmp_path: Path) -> None:
    """An agent that left ``garbage.txt`` and modified ``README.md``
    without committing must NOT contaminate the next sync. The
    contract: after sync, ``git status --porcelain`` is empty."""
    wt_mgr, _layout, _bare = _setup(tmp_path)
    wt_path = wt_mgr.add("task-a", branch="plan/feat")  # type: ignore[attr-defined]

    # Agent leaves crud behind.
    (wt_path / "garbage.txt").write_text("not committed")
    (wt_path / "README.md").write_text("vandalised")

    wt_mgr.sync_to_head("task-a", branch="plan/feat")  # type: ignore[attr-defined]

    # README restored.
    assert "vandalised" not in (wt_path / "README.md").read_text()
    # Garbage gone.
    assert not (wt_path / "garbage.txt").exists()
    # Working tree is clean.
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(wt_path),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert status == ""


def test_sync_missing_worktree_raises(tmp_path: Path) -> None:
    from workers.git_repos import GitCommandError

    wt_mgr, _layout, _bare = _setup(tmp_path)
    with pytest.raises(GitCommandError, match="not found"):
        wt_mgr.sync_to_head("does-not-exist", branch="plan/feat")  # type: ignore[attr-defined]
