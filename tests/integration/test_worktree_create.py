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


def test_ensure_repo_tolerates_concurrent_init_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TOCTOU (2026-07-03, plan CI4 reset): dos tasks RAÍZ del mismo plan
    provisionan el MISMO bare a la vez; la perdedora de `git init --bare` recibe
    rc=128 «cannot mkdir …: File exists» y su run moría `workspace_unavailable`.
    Que el repo exista ES el estado deseado: ensure_repo debe esperar a que el
    ganador termine la init y devolver el path."""
    from workers import git_repos
    from workers.git_repos import BareRepoLayout, BareRepoManager, GitCommandError

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    real_run_git = git_repos._run_git

    def racing(*args: str, cwd: Path | None = None, env_extra: dict | None = None) -> str:
        if args[0] == "init":
            # El hermano gana la carrera: el repo aparece y NUESTRO init pierde.
            real_run_git(*args, cwd=cwd)
            raise GitCommandError(
                f"git init --bare {args[-1]} failed (rc=128): "
                f"fatal: cannot mkdir {args[-1]}: File exists"
            )
        return real_run_git(*args, cwd=cwd, env_extra=env_extra)

    monkeypatch.setattr(git_repos, "_run_git", racing)
    path = mgr.ensure_repo("backend")
    assert (path / "HEAD").is_file()  # bare válido, sin excepción


def test_ensure_repo_init_race_with_invalid_dir_still_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si tras el «File exists» el directorio NUNCA llega a ser un repo válido
    (init del hermano abortada, basura previa), ensure_repo debe fallar alto —
    no devolver un path corrupto."""
    from workers import git_repos
    from workers.git_repos import BareRepoLayout, BareRepoManager, GitCommandError

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)

    def broken_init(*args: str, cwd: Path | None = None, env_extra: dict | None = None) -> str:
        if args[0] == "init":
            path = Path(args[-1])
            path.mkdir(parents=True, exist_ok=True)  # basura: dir sin repo dentro
            raise GitCommandError(f"fatal: cannot mkdir {path}: File exists")
        raise GitCommandError("not a git repository")

    monkeypatch.setattr(git_repos, "_run_git", broken_init)
    monkeypatch.setattr(git_repos, "_INIT_RACE_WAIT_ATTEMPTS", 2)
    monkeypatch.setattr(git_repos, "_INIT_RACE_WAIT_DELAY_S", 0.01)
    with pytest.raises(GitCommandError, match=r"init race|never became valid"):
        mgr.ensure_repo("backend")


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


def test_add_recovers_after_worktree_dir_deleted_out_of_band(tmp_path: Path) -> None:
    """Recuperación (2026-07-03): si el directorio del worktree desaparece SIN
    pasar por git (wipe parcial, borrado manual), el bare conserva una
    registración huérfana y `git worktree add` rechazaba re-crearlo («missing
    but already registered worktree»). add() debe podar y re-materializar."""
    import shutil

    from workers.git_repos import WorktreeManager

    layout = _layout_with_seed(tmp_path)
    wt_mgr = WorktreeManager(layout, "backend")  # type: ignore[arg-type]
    p1 = wt_mgr.add("task-1", branch="plan/06-foo")
    shutil.rmtree(p1)  # bypassea git — deja la registración huérfana en el bare

    p2 = wt_mgr.add("task-1", branch="plan/06-foo")
    assert p2 == p1
    assert (p2 / "README.md").is_file()


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
