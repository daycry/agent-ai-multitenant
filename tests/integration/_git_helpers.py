"""Fixtures shared by Plan 06 Fase E tests.

Real ``git`` invocations against ``tmp_path`` — no remote, no
network. Each test gets its own bare repo + a tiny seed commit so
worktree-add has something to check out.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(*args: str, cwd: Path) -> None:
    """Run git or raise. Kept private to this helper module."""
    subprocess.run(  # — explicit args, no shell
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        # Don't let local config (user.email/user.name) leak in.
        env={
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@e",
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": __import__("os").environ.get("PATH", ""),
        },
        timeout=30,
    )


def seed_bare_repo(bare_path: Path, *, default_branch: str = "main") -> str:
    """Init a bare repo at ``bare_path`` and push one seed commit.

    Returns the sha of the seed commit. Caller is responsible for
    creating the parent directory.
    """
    bare_path.parent.mkdir(parents=True, exist_ok=True)
    _git("init", "--bare", "--initial-branch", default_branch, str(bare_path), cwd=bare_path.parent)

    # Build the seed in a scratch clone so the bare gets a real commit.
    scratch = bare_path.parent / f"{bare_path.name}.seed"
    _git("clone", str(bare_path), str(scratch), cwd=bare_path.parent)
    (scratch / "README.md").write_text("seed\n")
    _git("add", "README.md", cwd=scratch)
    _git("commit", "-m", "seed", cwd=scratch)
    _git("branch", "-M", default_branch, cwd=scratch)
    _git("push", "-u", "origin", default_branch, cwd=scratch)

    # Capture the sha.
    proc = subprocess.run(
        ["git", "rev-parse", default_branch],
        cwd=str(scratch),
        check=True,
        capture_output=True,
        text=True,
    )
    sha = proc.stdout.strip()

    # Set HEAD on the bare so worktree add HEAD works.
    _git("symbolic-ref", "HEAD", f"refs/heads/{default_branch}", cwd=bare_path)

    import shutil

    shutil.rmtree(scratch, ignore_errors=True)
    return sha


def commit_to_branch(
    bare_path: Path,
    branch: str,
    *,
    filename: str,
    content: str,
    base: str = "main",
) -> str:
    """Add a commit on ``branch`` to the bare repo (clones, commits, pushes).

    Used by sync/cleanup tests to simulate "a sibling task pushed
    something to the plan branch since this worktree was created".
    """
    scratch = bare_path.parent / f"{bare_path.name}.tmp-{branch}"
    _git("clone", str(bare_path), str(scratch), cwd=bare_path.parent)
    # Check out the branch (create from base if it doesn't exist yet).
    try:
        _git("checkout", branch, cwd=scratch)
    except subprocess.CalledProcessError:
        _git("checkout", "-b", branch, base, cwd=scratch)
    (scratch / filename).write_text(content)
    _git("add", filename, cwd=scratch)
    _git("commit", "-m", f"add {filename}", cwd=scratch)
    _git("push", "-u", "origin", branch, cwd=scratch)
    proc = subprocess.run(
        ["git", "rev-parse", branch],
        cwd=str(scratch),
        check=True,
        capture_output=True,
        text=True,
    )
    sha = proc.stdout.strip()
    import shutil

    shutil.rmtree(scratch, ignore_errors=True)
    return sha
