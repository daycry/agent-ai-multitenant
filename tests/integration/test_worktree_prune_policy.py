"""G-07 (auditoría proyecto 2026-07-17): poda de worktrees por ESTADO, con red
de rescate.

La poda anterior era ciega (solo mtime, TTL 30d): conservaba 2.9GB de
worktrees de planes cerrados durante un mes y, peor, podía borrar el ÚNICO
resto de un `rebase_conflict` (un commit que no está en ninguna rama). Ahora:

- política por worktree: ``closed`` (plan cerrado → TTL 48h), ``keep`` (task
  blocked → nunca se poda), ``default`` (TTL 30d, como antes);
- antes de borrar, si el HEAD del worktree NO está contenido en ninguna rama
  del bare, se crea ``refs/rescue/{task_id}`` — el commit sobrevive al borrado.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration

_DAY = 86400.0


def _age(path: Path, *, days: float) -> None:
    """Set the worktree dir's mtime `days` into the past."""
    stamp = time.time() - days * _DAY
    os.utime(path, (stamp, stamp))


def _setup(tmp_path: Path):
    from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend")
    seed_bare_repo(bare)
    return layout, bare, WorktreeManager(layout, "backend")


def _git_out(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def test_closed_plan_worktrees_prune_after_48h(tmp_path: Path) -> None:
    _, _bare, mgr = _setup(tmp_path)
    fresh = mgr.add("task-fresh", branch="plan/aaaa-x")
    old = mgr.add("task-old", branch="plan/bbbb-y")
    _age(old, days=3)

    removed = mgr.prune_by_policy({"task-fresh": "closed", "task-old": "closed"})

    assert old in removed
    assert fresh not in removed
    assert fresh.exists() and not old.exists()


def test_keep_worktrees_survive_any_age(tmp_path: Path) -> None:
    _, _bare, mgr = _setup(tmp_path)
    blocked = mgr.add("task-blocked", branch="plan/cccc-z")
    _age(blocked, days=90)

    removed = mgr.prune_by_policy({"task-blocked": "keep"})

    assert removed == []
    assert blocked.exists()


def test_default_policy_keeps_30d_ttl(tmp_path: Path) -> None:
    _, _bare, mgr = _setup(tmp_path)
    young = mgr.add("task-young", branch="plan/dddd-a")
    stale = mgr.add("task-stale", branch="plan/eeee-b")
    _age(young, days=10)
    _age(stale, days=40)

    removed = mgr.prune_by_policy({})  # sin entrada → default

    assert stale in removed
    assert young not in removed


def test_unmerged_head_gets_rescue_ref(tmp_path: Path) -> None:
    """Un commit del worktree que NO está en ninguna rama del bare (p.ej. el
    resto de un rebase_conflict) recibe refs/rescue/{task} antes de la poda."""
    _, bare, mgr = _setup(tmp_path)
    wt = mgr.add("task-rescue", branch="plan/ffff-c")
    # Commit en el worktree con HEAD detached — no pertenece a ninguna rama.
    (wt / "obra.txt").write_text("trabajo no mergeado", encoding="utf-8")
    env = {
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@e",
        "PATH": os.environ.get("PATH", ""),
    }
    subprocess.run(
        ["git", "checkout", "--detach"], cwd=str(wt), check=True, capture_output=True, timeout=30
    )
    subprocess.run(["git", "add", "-A"], cwd=str(wt), check=True, capture_output=True, timeout=30)
    subprocess.run(
        ["git", "commit", "-m", "wip"],
        cwd=str(wt),
        check=True,
        capture_output=True,
        env=env,
        timeout=30,
    )
    head = _git_out("rev-parse", "HEAD", cwd=wt)
    _age(wt, days=3)

    removed = mgr.prune_by_policy({"task-rescue": "closed"})

    assert wt in removed and not wt.exists()
    rescued = _git_out("rev-parse", "refs/rescue/task-rescue", cwd=bare)
    assert rescued == head


def test_merged_head_needs_no_rescue(tmp_path: Path) -> None:
    """Un worktree cuyo HEAD ya está en su rama se poda sin ref de rescate."""
    _, bare, mgr = _setup(tmp_path)
    wt = mgr.add("task-clean", branch="plan/gggg-d")
    _age(wt, days=3)

    removed = mgr.prune_by_policy({"task-clean": "closed"})

    assert wt in removed
    refs = _git_out("for-each-ref", "refs/rescue", cwd=bare)
    assert refs == ""


def test_a_locked_worktree_is_pruned_cleanly(tmp_path: Path) -> None:
    """Un lock huérfano (ADR 0163: worker muerto con el puntero oculto) no puede
    convertir el worktree en un fantasma (auditoría 2026-09-01).

    Medido con git real ANTES del arreglo: `git worktree remove --force` se
    niega sobre un worktree bloqueado, el `rmtree` de respaldo borra el disco, y
    `git worktree prune` respeta el lock — quedaba un registro `locked`
    permanente en el bare y un `worktree add` del mismo id fallaba con rc=128
    («missing but locked worktree»).
    """
    _, bare, mgr = _setup(tmp_path)
    wt = mgr.add("task-locked", branch="plan/hhhh-e")
    subprocess.run(
        ["git", "--git-dir", str(bare), "worktree", "lock", str(wt), "--reason", "run muerto"],
        check=True,
        capture_output=True,
        timeout=30,
    )
    _age(wt, days=3)

    removed = mgr.prune_by_policy({"task-locked": "closed"})

    assert wt in removed and not wt.exists()
    registrados = _git_out("worktree", "list", "--porcelain", cwd=bare)
    assert "task-locked" not in registrados, (
        "queda un registro fantasma bloqueado en el bare:\n" + registrados
    )
    # Y el mismo task_id se puede volver a provisionar (reintento tras la poda).
    assert mgr.add("task-locked", branch="plan/hhhh-e").exists()
