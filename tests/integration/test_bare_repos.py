"""Integration tests: bare repos layout + init (Plan 06 task_06_16)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_layout_paths(tmp_path: Path) -> None:
    from workers.git_repos import BareRepoLayout

    layout = BareRepoLayout(
        data_root=tmp_path,
        tenant_slug="acme",
        project_slug="api",
    )
    assert layout.project_root == tmp_path / "projects" / "acme" / "api"
    assert layout.repos_root == layout.project_root / "repos"
    assert layout.worktrees_root == layout.project_root / "worktrees"
    assert layout.bare_repo_path("backend").name == "backend.git"
    assert layout.worktree_path("task-42").name == "task-42"


def test_ensure_repo_initialises_bare(tmp_path: Path) -> None:
    from workers.git_repos import BareRepoLayout, BareRepoManager

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    repo_path = mgr.ensure_repo("backend")

    assert repo_path.exists()
    assert (repo_path / "HEAD").is_file()
    # Bare repos have HEAD but no working tree.
    assert not (repo_path / ".git").exists()


def test_ensure_repo_is_idempotent(tmp_path: Path) -> None:
    from workers.git_repos import BareRepoLayout, BareRepoManager

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    p1 = mgr.ensure_repo("backend")
    (p1 / "marker").write_text("survives")
    p2 = mgr.ensure_repo("backend")
    assert p1 == p2
    assert (p2 / "marker").read_text() == "survives"


def test_ensure_repo_with_remote_url_configures_origin(tmp_path: Path) -> None:
    from workers.git_repos import BareRepoLayout, BareRepoManager, _run_git

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    repo_path = mgr.ensure_repo("backend", remote_url="https://example.com/backend.git")

    url = _run_git("remote", "get-url", "origin", cwd=repo_path).strip()
    assert url == "https://example.com/backend.git"


def test_ensure_repo_updates_remote_url_when_changed(tmp_path: Path) -> None:
    from workers.git_repos import BareRepoLayout, BareRepoManager, _run_git

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    mgr.ensure_repo("backend", remote_url="https://old.example.com/backend.git")
    repo_path = mgr.ensure_repo("backend", remote_url="https://new.example.com/backend.git")

    url = _run_git("remote", "get-url", "origin", cwd=repo_path).strip()
    assert url == "https://new.example.com/backend.git"


def test_list_repos_returns_names_without_suffix(tmp_path: Path) -> None:
    from workers.git_repos import BareRepoLayout, BareRepoManager

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    mgr.ensure_repo("backend")
    mgr.ensure_repo("frontend")
    mgr.ensure_repo("mobile")

    assert mgr.list_repos() == ("backend", "frontend", "mobile")


def test_list_repos_empty_for_unused_project(tmp_path: Path) -> None:
    from workers.git_repos import BareRepoLayout, BareRepoManager

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    assert mgr.list_repos() == ()
