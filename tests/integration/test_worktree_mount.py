"""Integration tests: worktree mount path is what the worker hands to
agent-runtime / test-runtime (Plan 06 task_06_19 — mount part).

This file pins the path-side contract: ``WorktreeManager.add`` returns
an absolute, bind-mountable path that contains the checked-out files.
The actual Docker mount happens in workers.test_runtime
(test_test_runtime_launch.py covers the kwargs).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


def _make_manager(tmp_path: Path) -> object:
    from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend")
    seed_bare_repo(bare)
    return WorktreeManager(layout, "backend")


def test_worktree_path_is_absolute(tmp_path: Path) -> None:
    wt_mgr = _make_manager(tmp_path)
    path = wt_mgr.add("task-1", branch="plan/x")  # type: ignore[attr-defined]
    assert path.is_absolute()


def test_worktree_path_lives_under_project_worktrees(tmp_path: Path) -> None:
    """Critical for the Docker bind: the worker mounts the path
    returned by ``add(...)`` into /workspace; that path MUST be under
    the project's ``worktrees/`` so a misbehaving template (or a
    bug) can't trick the worker into mounting ``/`` or ``/etc``."""
    wt_mgr = _make_manager(tmp_path)
    path = wt_mgr.add("task-1", branch="plan/x")  # type: ignore[attr-defined]
    assert (tmp_path / "projects" / "t" / "p" / "worktrees" / "task-1") == path


def test_worktree_contains_checked_out_files(tmp_path: Path) -> None:
    """The worker bind-mounts the worktree into /workspace and expects
    the test-runtime to see the project tree. Sanity-check the
    seed README is on disk after add()."""
    wt_mgr = _make_manager(tmp_path)
    path = wt_mgr.add("task-1", branch="plan/x")  # type: ignore[attr-defined]
    assert (path / "README.md").is_file()


def test_worktrees_for_different_tasks_are_disjoint(tmp_path: Path) -> None:
    """Two tasks of the same plan run in parallel — their worktrees
    must be independent directories so a write in one doesn't appear
    in the other (until sync_to_head re-reads the bare)."""
    wt_mgr = _make_manager(tmp_path)
    p1 = wt_mgr.add("task-1", branch="plan/x")  # type: ignore[attr-defined]
    p2 = wt_mgr.add("task-2", branch="plan/x")  # type: ignore[attr-defined]
    assert p1 != p2
    (p1 / "private-to-task-1").write_text("only here")
    assert not (p2 / "private-to-task-1").exists()
