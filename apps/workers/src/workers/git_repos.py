"""Bare repos + git worktrees on the worker host (Plan 06 Fase E).

The platform stores every project's code in *bare repos* on the
worker host filesystem, layout::

    /data/agent-platform/projects/<tenant_slug>/<project_slug>/repos/
        <repo_name>.git/                ← bare repo (no working tree)
        worktrees/
            <task_id>/                  ← per-task worktree (git worktree add)
        ...

Why bare repos + worktrees:

  * One clone per (tenant, project) repo, not per task — saves disk
    and avoids transferring objects on every task run.
  * ``git worktree add`` is cheap (a few KiB of metadata + a
    materialised tree), so each parallel task gets its own filesystem
    isolation without object duplication.
  * Bare repos are the natural target for ``git push`` from inside an
    agent-runtime container — the worker doesn't have to choreograph
    "push to a real remote" until the plan's PR step (Fase F).

Five tasks of Fase E live here:

  * :class:`BareRepoLayout` (06_16) — pure path helpers.
  * :class:`BareRepoManager` (06_16 + 06_17) — init, fetch, list.
  * :class:`WorktreeManager` (06_18 + 06_19 + 06_20) — add, sync
    (fetch + reset --hard to the plan's HEAD), list, prune by age.

These classes are *pure-filesystem* — they shell out to ``git``.
That keeps mypy strict (no fragile pygit2 stubs), makes test
fixtures trivial (every test uses real ``git init --bare``), and
matches the path the worker takes inside the container (``git`` is
always on PATH in the test-runtime images).
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

_log = structlog.get_logger("workers.git_repos")

# Default TTL after which an idle worktree gets pruned. Matches Plan 06
# task_06_20 ("Cleanup de worktrees a los 30 días sin actividad").
DEFAULT_WORKTREE_TTL_S = 30 * 24 * 60 * 60


# ---------------------------------------------------------------------------
# task_06_16 — Layout
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BareRepoLayout:
    """Path resolver for the on-host bare repo + worktrees tree.

    Slugs (``tenant_slug``, ``project_slug``) come from the Project
    row; ``repo_name`` is the human name the project gave to the
    repo. We never embed UUIDs in paths — slugs are stable and ops-
    friendly when an admin needs to ``ls`` the tree by hand.
    """

    data_root: Path
    tenant_slug: str
    project_slug: str

    @property
    def project_root(self) -> Path:
        return self.data_root / "projects" / self.tenant_slug / self.project_slug

    @property
    def repos_root(self) -> Path:
        return self.project_root / "repos"

    @property
    def worktrees_root(self) -> Path:
        return self.project_root / "worktrees"

    def bare_repo_path(self, repo_name: str) -> Path:
        return self.repos_root / f"{repo_name}.git"

    def worktree_path(self, task_id: str) -> Path:
        return self.worktrees_root / task_id


# ---------------------------------------------------------------------------
# task_06_16 / 06_17 — Bare repos
# ---------------------------------------------------------------------------


class GitCommandError(RuntimeError):
    """Raised when a ``git`` invocation returns non-zero."""


def _run_git(*args: str, cwd: Path | None = None, env_extra: dict[str, str] | None = None) -> str:
    """Run ``git ARGS`` in ``cwd``, return stdout. Raises on non-zero rc.

    Sets ``GIT_TERMINAL_PROMPT=0`` so a misconfigured remote that
    would normally ask for a password fails loudly instead of
    hanging the worker forever. Each caller adds a ``timeout=`` to
    bound wall-clock too.
    """
    import os

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(  # — explicit args, no shell
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise GitCommandError(
            f"git {' '.join(args)} failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


class BareRepoManager:
    """Init / fetch / list bare repos for one (tenant, project)."""

    def __init__(self, layout: BareRepoLayout) -> None:
        self._layout = layout

    @property
    def layout(self) -> BareRepoLayout:
        return self._layout

    # --- task_06_16 -----------------------------------------------------

    def ensure_repo(self, repo_name: str, *, remote_url: str | None = None) -> Path:
        """Create the bare repo if missing; return its on-disk path.

        When ``remote_url`` is given, configure it as the ``origin``
        remote so a later ``fetch_remote`` can pull from it. The same
        URL gets used for the push at PR time (Fase F).
        """
        path = self._layout.bare_repo_path(repo_name)
        if not path.exists():
            self._layout.repos_root.mkdir(parents=True, exist_ok=True)
            _run_git("init", "--bare", str(path))
            _log.info("bare_repo.init", tenant=self._layout.tenant_slug, repo=repo_name)
        if remote_url is not None:
            self._set_remote(path, remote_url)
        return path

    def _set_remote(self, repo_path: Path, url: str) -> None:
        """Configure (or update) ``origin`` to point at ``url``."""
        try:
            current = _run_git("remote", "get-url", "origin", cwd=repo_path).strip()
        except GitCommandError:
            _run_git("remote", "add", "origin", url, cwd=repo_path)
            return
        if current != url:
            _run_git("remote", "set-url", "origin", url, cwd=repo_path)

    def list_repos(self) -> tuple[str, ...]:
        """Return the names of bare repos in this project (no ``.git``)."""
        root = self._layout.repos_root
        if not root.exists():
            return ()
        names = []
        for entry in root.iterdir():
            if entry.is_dir() and entry.name.endswith(".git"):
                names.append(entry.name[:-4])
        return tuple(sorted(names))

    # --- task_06_17 — periodic fetch + webhook hook --------------------

    def fetch_remote(self, repo_name: str) -> None:
        """Run ``git fetch origin`` against the bare repo.

        Called both periodically (scheduled celery beat task) and from
        the webhook receiver when a push lands at the remote (e.g.
        GitHub / Azure DevOps webhook). Idempotent — fetching twice in
        a row is cheap.
        """
        path = self._layout.bare_repo_path(repo_name)
        if not path.exists():
            raise GitCommandError(f"bare repo {repo_name!r} does not exist at {path}")
        _run_git("fetch", "--prune", "origin", cwd=path)


# ---------------------------------------------------------------------------
# task_06_18 / 06_19 / 06_20 — Worktrees
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorktreeInfo:
    """One worktree as reported by ``git worktree list --porcelain``."""

    task_id: str
    path: Path
    branch: str | None
    head: str | None


class WorktreeManager:
    """Manage per-task worktrees against a bare repo."""

    def __init__(self, layout: BareRepoLayout, repo_name: str) -> None:
        self._layout = layout
        self._repo_name = repo_name
        self._repo_path = layout.bare_repo_path(repo_name)
        if not self._repo_path.exists():
            raise GitCommandError(
                f"bare repo {repo_name!r} missing at {self._repo_path}; "
                "call BareRepoManager.ensure_repo first"
            )

    # --- task_06_18 -----------------------------------------------------

    def add(self, task_id: str, *, branch: str, base: str | None = None) -> Path:
        """Create a *detached-HEAD* worktree for ``task_id``.

        The plan model allows several sibling tasks to share one git
        branch (the plan branch). Git refuses to check out the same
        branch in two worktrees at once, so each worktree is created
        with **detached HEAD** pointing at the branch's current sha.
        Commits in the worktree get pushed back to the branch later
        (Plan 06 Fase F's ``commit + push`` machinery).

        When the branch doesn't exist yet (first task of a fresh plan
        branch), create it as a normal ref pointing at ``base``
        (default ``HEAD``) without ever checking it out; then detach.

        An existing worktree at the task's path is reused — sync is
        :meth:`sync_to_head`'s job.
        """
        wt_path = self._layout.worktree_path(task_id)
        if wt_path.exists():
            return wt_path
        wt_path.parent.mkdir(parents=True, exist_ok=True)

        if not self._branch_exists(branch):
            base_ref = base or "HEAD"
            _run_git("branch", branch, base_ref, cwd=self._repo_path)

        _run_git(
            "worktree",
            "add",
            "--detach",
            str(wt_path),
            f"refs/heads/{branch}",
            cwd=self._repo_path,
        )
        _log.info(
            "worktree.add",
            tenant=self._layout.tenant_slug,
            project=self._layout.project_slug,
            repo=self._repo_name,
            task=task_id,
            branch=branch,
        )
        return wt_path

    def _branch_exists(self, branch: str) -> bool:
        try:
            _run_git(
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{branch}",
                cwd=self._repo_path,
            )
        except GitCommandError:
            return False
        return True

    # --- task_06_19 — sync worktree to the plan branch HEAD ------------

    def sync_to_head(self, task_id: str, *, branch: str) -> None:
        """Bring the worktree to ``branch``'s current HEAD.

        Called by the worker BEFORE handing control to the agent — so
        sibling tasks that committed to the plan branch since this
        worktree was last touched are visible. The sequence:
            git -C <wt> fetch <bare-path> <branch>
            git -C <wt> reset --hard FETCH_HEAD
            git -C <wt> clean -fdx
        ``clean -fdx`` removes any leftover artifacts from a previous
        run (build outputs, untracked __pycache__) so the agent starts
        from a deterministic state.
        """
        wt = self._layout.worktree_path(task_id)
        if not wt.exists():
            raise GitCommandError(f"worktree {task_id!r} not found at {wt}")
        # Fetch the bare repo's branch into FETCH_HEAD; this works
        # whether the bare is a real remote or a local on-disk repo.
        _run_git("fetch", str(self._repo_path), branch, cwd=wt)
        _run_git("reset", "--hard", "FETCH_HEAD", cwd=wt)
        _run_git("clean", "-fdx", cwd=wt)
        _log.info(
            "worktree.sync",
            tenant=self._layout.tenant_slug,
            repo=self._repo_name,
            task=task_id,
            branch=branch,
        )

    # --- task_06_20 — listing + cleanup --------------------------------

    def list_worktrees(self) -> tuple[WorktreeInfo, ...]:
        """Parse ``git worktree list --porcelain`` into a structured tuple."""
        output = _run_git("worktree", "list", "--porcelain", cwd=self._repo_path)
        return tuple(self._parse_porcelain(output))

    def _parse_porcelain(self, output: str) -> list[WorktreeInfo]:
        """Each worktree block is separated by blank lines; first key
        is ``worktree <path>``, then ``HEAD <sha>``, ``branch <ref>``."""
        infos: list[WorktreeInfo] = []
        current: dict[str, str] = {}
        for raw in [*output.splitlines(), ""]:
            line = raw.strip()
            if not line:
                if current.get("worktree"):
                    path = Path(current["worktree"])
                    # Exclude the bare itself.
                    if path != self._repo_path:
                        infos.append(
                            WorktreeInfo(
                                task_id=path.name,
                                path=path,
                                branch=current.get("branch", "").removeprefix("refs/heads/")
                                or None,
                                head=current.get("HEAD"),
                            )
                        )
                current = {}
                continue
            if " " in line:
                key, _, value = line.partition(" ")
                current[key] = value
            else:
                current[line] = ""
        return infos

    def prune_idle(
        self,
        *,
        ttl_seconds: int = DEFAULT_WORKTREE_TTL_S,
        now: float | None = None,
    ) -> list[Path]:
        """Remove worktrees whose mtime is older than ``ttl_seconds``.

        Worker calls this periodically (celery beat). Each removed
        worktree is also unregistered from the bare via
        ``git worktree remove --force`` so ``git worktree list`` stays
        consistent. ``now`` is overridable for tests.
        """
        threshold = (now if now is not None else time.time()) - ttl_seconds
        removed: list[Path] = []
        root = self._layout.worktrees_root
        if not root.exists():
            return removed

        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            try:
                mtime = entry.stat().st_mtime
            except FileNotFoundError:
                continue
            if mtime >= threshold:
                continue
            self._remove_worktree(entry)
            removed.append(entry)
        return removed

    def _remove_worktree(self, path: Path) -> None:
        """Best-effort worktree removal.

        ``git worktree remove`` is the right way (it cleans the bare
        repo's metadata), but it can refuse if the worktree has
        uncommitted changes. We use ``--force`` and fall back to a
        plain ``shutil.rmtree`` + ``git worktree prune`` if git still
        refuses.
        """
        import shutil

        try:
            _run_git("worktree", "remove", "--force", str(path), cwd=self._repo_path)
        except GitCommandError:
            shutil.rmtree(path, ignore_errors=True)
            # Ask git to forget the now-missing entry.
            import contextlib

            with contextlib.suppress(GitCommandError):
                _run_git("worktree", "prune", cwd=self._repo_path)


__all__ = [
    "BareRepoLayout",
    "BareRepoManager",
    "DEFAULT_WORKTREE_TTL_S",
    "GitCommandError",
    "WorktreeInfo",
    "WorktreeManager",
]
