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

import contextlib
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

_log = structlog.get_logger("workers.git_repos")

# Default TTL after which an idle worktree gets pruned. Matches Plan 06
# task_06_20 ("Cleanup de worktrees a los 30 días sin actividad").
DEFAULT_WORKTREE_TTL_S = 30 * 24 * 60 * 60

# Git's well-known empty-tree object id — used to seed an empty ROOT commit in a
# fresh local bare repo (prod-18) so worktrees can branch off a valid HEAD.
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Espera acotada del PERDEDOR de la carrera de `git init --bare` (TOCTOU
# 2026-07-03): dos tasks raíz del mismo plan provisionan el MISMO bare a la vez;
# el perdedor espera a que el ganador termine de inicializarlo (ventana de ms).
_INIT_RACE_WAIT_ATTEMPTS = 20
_INIT_RACE_WAIT_DELAY_S = 0.25
# Identity for platform-authored git ops with no human author (the seed commit).
_PLATFORM_GIT_NAME = "Agentic Platform"
_PLATFORM_GIT_EMAIL = "platform@agentic.local"


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

    Also injects ``safe.bareRepository=all`` (prod-18): the platform operates on
    its OWN bare repos under ``data_root`` (``git -C <repo>.git branch``…), but
    modern git defaults ``safe.bareRepository=explicit`` for some setups, which
    rejects bare-repo operations with "cannot use bare repository". Allowing it is
    safe — the bare repos are the platform's, not untrusted clones — and it makes
    worktree provisioning work regardless of the host's git config.
    """
    import os

    # Append our config to any inherited GIT_CONFIG_PARAMETERS (don't clobber).
    inherited = os.environ.get("GIT_CONFIG_PARAMETERS", "")
    config_params = (inherited + " " if inherited else "") + "'safe.bareRepository=all'"
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_PARAMETERS": config_params,
    }
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
            try:
                _run_git("init", "--bare", str(path))
                _log.info("bare_repo.init", tenant=self._layout.tenant_slug, repo=repo_name)
            except GitCommandError as exc:
                # TOCTOU (2026-07-03): dos tasks RAÍZ del mismo plan provisionan
                # el MISMO bare a la vez; la perdedora del `git init` recibe
                # rc=128 «cannot mkdir …: File exists» y su run moría
                # `workspace_unavailable` (reset del plan CI4). Que el repo
                # exista ES el estado deseado — esperar a que el ganador termine
                # de inicializarlo en vez de fallar.
                if "exists" not in str(exc).lower():
                    raise
                self._wait_repo_valid(path)
                _log.info(
                    "bare_repo.init_race_recovered",
                    tenant=self._layout.tenant_slug,
                    repo=repo_name,
                )
        if remote_url is not None:
            self._set_remote(path, remote_url)
        return path

    @staticmethod
    def _wait_repo_valid(path: Path) -> None:
        """Espera acotada a que ``path`` sea un repo git válido (el ganador de la
        carrera de init puede seguir inicializándolo). Si nunca lo es (basura
        previa, init abortada), re-lanza — mejor fallar alto que devolver un
        path corrupto."""
        last_exc: GitCommandError | None = None
        for _ in range(_INIT_RACE_WAIT_ATTEMPTS):
            try:
                _run_git("-C", str(path), "rev-parse", "--is-bare-repository")
                return
            except GitCommandError as exc:
                last_exc = exc
                time.sleep(_INIT_RACE_WAIT_DELAY_S)
        raise GitCommandError(
            f"bare repo at {path} exists but never became valid (init race?): {last_exc}"
        )

    def seed_initial_commit_if_empty(self, repo_name: str) -> bool:
        """Ensure the bare repo has a commit so worktrees can branch off it (prod-18).

        A fresh LOCAL bare (``git init --bare``, no remote/clone) is empty: HEAD is
        unborn and ``git worktree add … HEAD`` fails ("not a valid object name").
        Seed an empty ROOT commit (the well-known empty tree) on the bare's current
        HEAD branch, with a platform git identity. No-op if the repo already has
        commits (e.g. it was cloned from a remote). Returns ``True`` iff it seeded."""
        path = self._layout.bare_repo_path(repo_name)
        try:
            _run_git("-C", str(path), "rev-parse", "--verify", "HEAD")
            return False  # already has at least one commit
        except GitCommandError:
            pass
        ident = {
            "GIT_AUTHOR_NAME": _PLATFORM_GIT_NAME,
            "GIT_AUTHOR_EMAIL": _PLATFORM_GIT_EMAIL,
            "GIT_COMMITTER_NAME": _PLATFORM_GIT_NAME,
            "GIT_COMMITTER_EMAIL": _PLATFORM_GIT_EMAIL,
        }
        sha = _run_git(
            "-C", str(path), "commit-tree", _EMPTY_TREE_SHA, "-m", "Initial commit", env_extra=ident
        ).strip()
        # Point the bare's current (unborn) HEAD branch at the seed commit.
        head_ref = _run_git("-C", str(path), "symbolic-ref", "HEAD").strip()
        _run_git("-C", str(path), "update-ref", head_ref, sha)
        _log.info("bare_repo.seed_initial_commit", tenant=self._layout.tenant_slug, repo=repo_name)
        return True

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

    def fetch_remote(self, repo_name: str, *, auth_env: dict[str, str] | None = None) -> None:
        """Run ``git fetch --prune origin`` against the bare repo.

        Invoked from ``clone_project_repo`` — on the manual "Sincronizar" action
        (``POST /projects/{id}/git/sync``) and when the git config is (re)saved
        (``PUT /projects/{id}/git``). A periodic beat task and a webhook receiver
        with signature verification are NOT wired yet (audit 2026-07-03 P5/T6:
        this docstring used to claim they were — gated → ADR 0098). Idempotent —
        fetching twice in a row is cheap.

        ``auth_env`` (ADR 0072): variables de entorno de autenticación
        (GIT_ASKPASS/GIT_SSH_COMMAND) construidas por ``git_auth`` para
        autenticarse contra el remoto. ``None`` = sin auth (remoto local o
        ya autenticable por el host).
        """
        path = self._layout.bare_repo_path(repo_name)
        if not path.exists():
            raise GitCommandError(f"bare repo {repo_name!r} does not exist at {path}")
        _run_git("fetch", "--prune", "origin", cwd=path, env_extra=auth_env)

    def align_default_branch(self, repo_name: str, branch: str) -> str:
        """Alinea la rama default LOCAL con ``origin/<branch>`` tras un fetch.

        El clone inicial solo materializa ``refs/remotes/origin/*``; sin este
        paso la rama default local no existe y el primer worktree la SIEMBRA
        con una raíz sintética — si el remoto tiene (o gana después) su propia
        historia, el PR final choca con «no history in common» (visto en vivo
        con el plan CI4, 2026-07-09). Semántica conservadora:

          * ``created``        — no había local: se crea apuntando al remoto
            (y HEAD del bare pasa a esa rama).
          * ``fast_forwarded`` — la local iba estrictamente por detrás.
          * ``up_to_date``     — ya coinciden.
          * ``remote_empty``   — el remoto no tiene esa rama; NO se inventa
            nada (el caller decide si sembrar y avisa).
          * ``diverged``       — historias sin ancestro común o local por
            delante: NUNCA se reescribe trabajo local; se reporta.
        """
        path = self._layout.bare_repo_path(repo_name)
        if not path.exists():
            raise GitCommandError(f"bare repo {repo_name!r} does not exist at {path}")
        remote_ref = f"refs/remotes/origin/{branch}"
        local_ref = f"refs/heads/{branch}"
        try:
            remote_sha = _run_git("rev-parse", "--verify", remote_ref, cwd=path).strip()
        except GitCommandError:
            return "remote_empty"
        try:
            local_sha = _run_git("rev-parse", "--verify", local_ref, cwd=path).strip()
        except GitCommandError:
            _run_git("update-ref", local_ref, remote_sha, cwd=path)
            _run_git("symbolic-ref", "HEAD", local_ref, cwd=path)
            _log.info(
                "bare.default_branch_created",
                repo=repo_name,
                branch=branch,
                sha=remote_sha[:12],
            )
            return "created"
        if local_sha == remote_sha:
            return "up_to_date"
        try:
            # fast-forward SOLO si la local es ancestro estricto del remoto.
            _run_git("merge-base", "--is-ancestor", local_sha, remote_sha, cwd=path)
        except GitCommandError:
            _log.warning(
                "bare.default_branch_diverged",
                repo=repo_name,
                branch=branch,
                local=local_sha[:12],
                remote=remote_sha[:12],
            )
            return "diverged"
        _run_git("update-ref", local_ref, remote_sha, local_sha, cwd=path)
        return "fast_forwarded"


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
        # Recuperación (2026-07-03): si el directorio del worktree desapareció
        # SIN pasar por git (wipe parcial, borrado manual), el bare conserva una
        # registración huérfana y `git worktree add` rechaza re-crearlo («missing
        # but already registered worktree»). Podar antes es barato e idempotente.
        with contextlib.suppress(GitCommandError):
            _run_git("worktree", "prune", cwd=self._repo_path)

        if not self.branch_exists(branch):
            base_ref = base or "HEAD"
            try:
                _run_git("branch", branch, base_ref, cwd=self._repo_path)
            except GitCommandError as exc:
                # TOCTOU (auditoría 2026-07-02): dos tasks hermanas promovidas a
                # la vez provisionan el MISMO plan branch; la perdedora del
                # `git branch` recibía rc=128 «already exists» y su run moría
                # `workspace_unavailable` (con el fail-fast F0.2). Que el branch
                # ya exista ES el estado deseado — éxito idempotente.
                if "already exists" not in str(exc):
                    raise

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

    def branch_exists(self, branch: str) -> bool:
        """¿Existe ``refs/heads/<branch>`` en el bare? Público porque la guarda
        ``repo_history_lost`` (execution.py) lo consulta antes de materializar
        el worktree de un plan con tareas ya completadas."""
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

    def prune_by_policy(
        self,
        policy: dict[str, str],
        *,
        ttl_closed_s: int = 48 * 3600,
        ttl_default_s: int = DEFAULT_WORKTREE_TTL_S,
        now: float | None = None,
    ) -> list[Path]:
        """Poda por ESTADO (G-07): la poda ciega por mtime conservaba durante un
        mes worktrees de planes ya cerrados y podía borrar el único resto de un
        ``rebase_conflict`` (un commit fuera de rama).

        ``policy`` mapea el nombre del worktree (task_id) a:
          - ``"closed"``: el plan del worktree está cerrado → TTL 48h;
          - ``"keep"``: la tarea está bloqueada → NUNCA se poda (es la escena
            del crimen que el operador necesita inspeccionar);
          - ``"default"`` (o sin entrada): TTL clásico de 30 días.

        Red de rescate: antes de borrar, si el HEAD del worktree no está
        contenido en NINGUNA rama del bare, se crea ``refs/rescue/{task_id}``
        apuntándolo — el commit sobrevive al borrado y ``git gc`` no lo
        recolecta. Tras la pasada corre ``git worktree prune`` (registros
        stale del bare). Devuelve los paths borrados.
        """
        moment = now if now is not None else time.time()
        removed: list[Path] = []
        root = self._layout.worktrees_root
        if not root.exists():
            return removed

        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            verdict = policy.get(entry.name, "default")
            if verdict == "keep":
                continue
            ttl = ttl_closed_s if verdict == "closed" else ttl_default_s
            try:
                mtime = entry.stat().st_mtime
            except FileNotFoundError:
                continue
            if mtime >= moment - ttl:
                continue
            self._rescue_unmerged_head(entry)
            self._remove_worktree(entry)
            removed.append(entry)

        with contextlib.suppress(GitCommandError):
            _run_git("worktree", "prune", cwd=self._repo_path)
        return removed

    def _rescue_unmerged_head(self, worktree: Path) -> None:
        """Si el HEAD del worktree no está contenido en ninguna rama del bare,
        créale ``refs/rescue/{task_id}`` para que sobreviva a la poda. Best-
        effort: un worktree corrupto no debe frenar la limpieza del resto."""
        try:
            head = _run_git("rev-parse", "HEAD", cwd=worktree).strip()
            containing = _run_git("branch", "--contains", head, cwd=self._repo_path).strip()
            if not containing:
                _run_git("update-ref", f"refs/rescue/{worktree.name}", head, cwd=self._repo_path)
                _log.warning(
                    "worktree.rescue_ref_created",
                    worktree=worktree.name,
                    head=head,
                    repo=str(self._repo_path),
                )
        except GitCommandError as exc:
            _log.warning("worktree.rescue_check_failed", worktree=worktree.name, error=str(exc))

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
