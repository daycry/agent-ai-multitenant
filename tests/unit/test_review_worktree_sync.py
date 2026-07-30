"""El worktree de la app-preview se sincroniza a la punta de la rama del plan.

Visto en vivo (plan CI4, 2026-07-09): tras el ciclo de correcciones (ADR 0107)
la sesión de review nueva sirvió CÓDIGO VIEJO — el caller pasó el
``worktree_host_path`` explícito (del spec de una sesión anterior) y
``_resolve_review_worktree_host_path`` lo devolvía VERBATIM sin sincronizar,
así que el validador vio el bug ya corregido como si los agentes no hubieran
hecho nada. Ese camino existe en producción (el re-run del sweep reusa
``row.spec``).

Contrato nuevo:
  - ruta explícita → fetch + reset --hard a la punta de la rama del plan,
    SIN ``clean`` (los artefactos no trackeados tipo ``vendor/`` deben
    sobrevivir: la app de preview los necesita y el contenedor no tiene red
    para reinstalarlos);
  - la sincronización es best-effort: si no se puede derivar la rama/repo,
    la ruta explícita se devuelve tal cual (mejor una preview desfasada que
    ninguna).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager
from workers.plan_git import make_plan_branch_name
from workers.tasks.review_runtime_task import _resolve_review_worktree_host_path

pytestmark = pytest.mark.unit

_PLAN_ID = "019f1397-afaf-7ed3-8bdc-40d60f5e10dd"
_PLAN_SLUG = "demo-plan"


class _Settings:
    def __init__(self, data_root: Path) -> None:
        self.data_root = str(data_root)


def _git(*args: str, cwd: Path) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _request(worktree: Path | None = None) -> dict[str, Any]:
    req: dict[str, Any] = {
        "plan_id": _PLAN_ID,
        "plan_slug": _PLAN_SLUG,
        "tenant_slug": "demo",
        "project_slug": "api-ci",
        "repo_name": "api-ci",
    }
    if worktree is not None:
        req["worktree_host_path"] = str(worktree)
    return req


def _provision(tmp_path: Path) -> tuple[Path, Path, str]:
    """Bare repo + worktree de review en la rama del plan; devuelve
    (worktree_path, bare_path, branch)."""
    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="demo", project_slug="api-ci")
    branch = make_plan_branch_name(_PLAN_ID, _PLAN_SLUG)
    mgr = BareRepoManager(layout)
    mgr.ensure_repo("api-ci")
    mgr.seed_initial_commit_if_empty("api-ci")
    wt = WorktreeManager(layout, "api-ci")
    path = wt.add(f"review-{_PLAN_ID[:8]}", branch=branch)
    return Path(path), layout.bare_repo_path("api-ci"), branch


def _advance_branch(bare: Path, branch: str, tmp_path: Path) -> str:
    """Commit nuevo en la rama del plan (simula el trabajo de las correcciones)."""
    scratch = tmp_path / "scratch"
    _git("clone", "--branch", branch, str(bare), str(scratch), cwd=tmp_path)
    (scratch / "fix.txt").write_text("filtro acotado a api/v1", encoding="utf-8")
    _git("add", "fix.txt", cwd=scratch)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "fix", cwd=scratch)
    _git("push", "origin", branch, cwd=scratch)
    return _git("rev-parse", branch, cwd=bare)


def test_explicit_path_is_synced_to_branch_tip_preserving_untracked(tmp_path: Path) -> None:
    worktree, bare, branch = _provision(tmp_path)
    # Artefacto runtime no trackeado (p.ej. vendor/ de composer): debe SOBREVIVIR.
    vendor = worktree / "vendor"
    vendor.mkdir()
    (vendor / "autoload.php").write_text("<?php", encoding="utf-8")

    tip = _advance_branch(bare, branch, tmp_path)
    assert _git("rev-parse", "HEAD", cwd=worktree) != tip  # desfasado a propósito

    resolved = _resolve_review_worktree_host_path(_request(worktree), _Settings(tmp_path))

    assert resolved == str(worktree)
    assert _git("rev-parse", "HEAD", cwd=worktree) == tip  # sincronizado
    assert (worktree / "fix.txt").exists()  # el fix de las correcciones se sirve
    assert (vendor / "autoload.php").exists()  # vendor/ intacto (sin clean)


def test_explicit_path_returned_verbatim_when_sync_cannot_derive_repo(tmp_path: Path) -> None:
    # Sin slugs no hay forma de derivar la rama/bare: best-effort → ruta tal cual.
    somewhere = tmp_path / "wt"
    somewhere.mkdir()
    request = {"plan_id": _PLAN_ID, "worktree_host_path": str(somewhere)}
    resolved = _resolve_review_worktree_host_path(request, _Settings(tmp_path))
    assert resolved == str(somewhere)


def test_provisioned_path_still_syncs_to_head(tmp_path: Path) -> None:
    # El camino SIN ruta explícita conserva su contrato (aprovisiona + sync).
    _worktree, bare, branch = _provision(tmp_path)
    tip = _advance_branch(bare, branch, tmp_path)
    resolved = _resolve_review_worktree_host_path(_request(), _Settings(tmp_path))
    assert resolved
    assert _git("rev-parse", "HEAD", cwd=Path(resolved)) == tip
