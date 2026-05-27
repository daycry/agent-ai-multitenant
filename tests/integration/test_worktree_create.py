"""Integration tests: WorktreeManager.add (Plan 06 task_06_18)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


def _layout_with_seed(tmp_path: Path) -> object:
    from workers.git_repos import BareRepoLayout, BareRepoManager

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    bare = mgr.ensure_repo("backend")
    seed_bare_repo(bare)  # Init proper main + one commit.
    return layout


def test_add_creates_worktree_on_new_branch(tmp_path: Path) -> None:
    from workers.git_repos import WorktreeManager

    layout = _layout_with_seed(tmp_path)
    wt_mgr = WorktreeManager(layout, "backend")  # type: ignore[arg-type]
    path = wt_mgr.add("task-1", branch="plan/06-foo")

    assert path.exists()
    assert path.is_dir()
    # The worktree contains the seed file checked out.
    assert (path / "README.md").is_file()


def test_add_is_idempotent_for_same_task(tmp_path: Path) -> None:
    from workers.git_repos import WorktreeManager

    layout = _layout_with_seed(tmp_path)
    wt_mgr = WorktreeManager(layout, "backend")  # type: ignore[arg-type]
    p1 = wt_mgr.add("task-1", branch="plan/06-foo")
    (p1 / "marker").write_text("survives")
    p2 = wt_mgr.add("task-1", branch="plan/06-foo")
    assert p1 == p2
    assert (p2 / "marker").read_text() == "survives"


def test_add_reuses_existing_branch(tmp_path: Path) -> None:
    """When the plan branch already exists (because a sibling task
    created it earlier), the new worktree just checks it out — it
    must NOT create a fresh branch from HEAD."""
    from workers.git_repos import WorktreeManager

    from tests.integration._git_helpers import commit_to_branch

    layout = _layout_with_seed(tmp_path)
    bare = layout.bare_repo_path("backend")  # type: ignore[attr-defined]
    # Sibling pushed a commit on the plan branch.
    sha = commit_to_branch(bare, "plan/06-foo", filename="from_sibling.txt", content="x")

    wt_mgr = WorktreeManager(layout, "backend")  # type: ignore[arg-type]
    path = wt_mgr.add("task-2", branch="plan/06-foo")
    # Worktree HEAD must match the sibling's commit.
    import subprocess

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(path),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == sha
    assert (path / "from_sibling.txt").is_file()


def test_add_missing_bare_repo_raises(tmp_path: Path) -> None:
    from workers.git_repos import BareRepoLayout, GitCommandError, WorktreeManager

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    with pytest.raises(GitCommandError, match="missing"):
        WorktreeManager(layout, "no-such-repo")


def test_list_worktrees_returns_per_task_entries(tmp_path: Path) -> None:
    from workers.git_repos import WorktreeManager

    layout = _layout_with_seed(tmp_path)
    wt_mgr = WorktreeManager(layout, "backend")  # type: ignore[arg-type]
    wt_mgr.add("task-1", branch="plan/06-foo-1")
    wt_mgr.add("task-2", branch="plan/06-foo-2")

    infos = wt_mgr.list_worktrees()
    task_ids = {info.task_id for info in infos}
    assert task_ids == {"task-1", "task-2"}
    # Worktrees are created in detached HEAD mode (so siblings can
    # share the plan branch) — git's porcelain output reports
    # ``detached`` instead of a branch ref. The contract callers
    # depend on is that ``head`` is set.
    for info in infos:
        assert info.head is not None
