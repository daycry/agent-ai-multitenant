"""Integration tests: fetch_remote pulls new objects (Plan 06 task_06_17).

We simulate a remote by setting one bare repo's origin to a *second*
bare repo on disk. A push into the second bare → ``fetch_remote``
sees the new objects.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration._git_helpers import commit_to_branch, seed_bare_repo

pytestmark = pytest.mark.integration


def test_fetch_remote_pulls_new_objects(tmp_path: Path) -> None:
    from workers.git_repos import BareRepoLayout, BareRepoManager

    # "Remote" bare with a seed commit.
    remote_bare = tmp_path / "remote" / "backend.git"
    seed_bare_repo(remote_bare)

    # Local bare that we manage, with origin → the remote bare.
    layout = BareRepoLayout(data_root=tmp_path / "data", tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    local_bare = mgr.ensure_repo("backend", remote_url=str(remote_bare))

    # First fetch — pulls the seed commit.
    mgr.fetch_remote("backend")
    # Local bare now has refs/remotes/origin/main pointing at the seed.
    refs_before = subprocess.run(
        ["git", "rev-parse", "refs/remotes/origin/main"],
        cwd=str(local_bare),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert refs_before

    # Push a new commit to the remote bare.
    commit_to_branch(remote_bare, "main", filename="file.txt", content="hi\n")

    # Second fetch — must move origin/main forward.
    mgr.fetch_remote("backend")
    refs_after = subprocess.run(
        ["git", "rev-parse", "refs/remotes/origin/main"],
        cwd=str(local_bare),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert refs_after != refs_before


def test_fetch_remote_missing_repo_raises(tmp_path: Path) -> None:
    from workers.git_repos import BareRepoLayout, BareRepoManager, GitCommandError

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    with pytest.raises(GitCommandError, match="does not exist"):
        mgr.fetch_remote("nonexistent")


def test_fetch_remote_with_unreachable_origin_fails_loudly(tmp_path: Path) -> None:
    """Defense in depth: a misconfigured remote must fail, not hang.

    We point origin at a non-existent path; the git invocation should
    return non-zero (not block on a credentials prompt — that's what
    GIT_TERMINAL_PROMPT=0 guarantees)."""
    from workers.git_repos import BareRepoLayout, BareRepoManager, GitCommandError

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    mgr.ensure_repo("ghost", remote_url=str(tmp_path / "does-not-exist.git"))

    with pytest.raises(GitCommandError):
        mgr.fetch_remote("ghost")
