"""Integration tests: prune idle worktrees (Plan 06 task_06_20)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


def _make_manager(tmp_path: Path) -> object:
    from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend")
    seed_bare_repo(bare)
    return WorktreeManager(layout, "backend"), layout


def _backdate(path: Path, age_seconds: float) -> None:
    past = time.time() - age_seconds
    os.utime(path, (past, past))


def test_prune_removes_old_worktrees(tmp_path: Path) -> None:
    wt_mgr, _layout = _make_manager(tmp_path)
    p1 = wt_mgr.add("task-old", branch="plan/old")  # type: ignore[attr-defined]
    p2 = wt_mgr.add("task-new", branch="plan/new")  # type: ignore[attr-defined]
    _backdate(p1, 60 * 60 * 24 * 45)  # 45 days
    _backdate(p2, 60 * 60 * 24 * 5)  # 5 days

    removed = wt_mgr.prune_idle()  # type: ignore[attr-defined]
    assert p1 in removed
    assert p2 not in removed
    assert not p1.exists()
    assert p2.exists()


def test_prune_respects_custom_ttl(tmp_path: Path) -> None:
    wt_mgr, _layout = _make_manager(tmp_path)
    p = wt_mgr.add("task-1", branch="plan/x")  # type: ignore[attr-defined]
    _backdate(p, 60 * 60 * 24 * 10)  # 10 days

    # ttl=30 days → keep.
    assert wt_mgr.prune_idle(ttl_seconds=60 * 60 * 24 * 30) == []  # type: ignore[attr-defined]
    assert p.exists()

    # ttl=5 days → purge.
    removed = wt_mgr.prune_idle(ttl_seconds=60 * 60 * 24 * 5)  # type: ignore[attr-defined]
    assert p in removed
    assert not p.exists()


def test_prune_updates_git_worktree_metadata(tmp_path: Path) -> None:
    """After prune, ``git worktree list`` must not show the removed
    worktree (otherwise the bare's metadata leaks stale entries)."""
    wt_mgr, _layout = _make_manager(tmp_path)
    p = wt_mgr.add("task-stale", branch="plan/stale")  # type: ignore[attr-defined]
    _backdate(p, 60 * 60 * 24 * 45)

    wt_mgr.prune_idle()  # type: ignore[attr-defined]
    infos = wt_mgr.list_worktrees()  # type: ignore[attr-defined]
    task_ids = {info.task_id for info in infos}
    assert "task-stale" not in task_ids


def test_prune_now_override(tmp_path: Path) -> None:
    wt_mgr, _layout = _make_manager(tmp_path)
    p = wt_mgr.add("task-x", branch="plan/x")  # type: ignore[attr-defined]
    mtime = p.stat().st_mtime
    # Fake now 100 days after the worktree's mtime, TTL 50 days.
    fake_now = mtime + 60 * 60 * 24 * 100
    removed = wt_mgr.prune_idle(  # type: ignore[attr-defined]
        ttl_seconds=60 * 60 * 24 * 50, now=fake_now
    )
    assert p in removed


def test_prune_empty_root_does_nothing(tmp_path: Path) -> None:
    wt_mgr, _layout = _make_manager(tmp_path)
    # No worktrees added.
    assert wt_mgr.prune_idle() == []  # type: ignore[attr-defined]
