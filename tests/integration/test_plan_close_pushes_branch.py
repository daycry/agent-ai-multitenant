"""cadena-pr, residuo de P3: el CIERRE garantiza la rama —y su punta— en el remoto.

T3 cableó el push incremental por-tarea, pero `PlanGitWorkflow.open_plan_pr` solo
forzaba el push cuando `branch_push_mode == 'final_only'`. En `incremental` (el
DEFAULT) el commit que añade el propio cierre —la documentación de cierre, que
`plan_docs.write_plan_docs_to_branch` commitea al bare justo ANTES de abrir el PR
para «que el PR contenga su propio changelog»— NO llegaba nunca al remoto. El PR se
abría contra una rama remota sin su changelog y, si algún push incremental se saltó
(proyecto sin `remote_url` cuando corrieron las tareas) o falló (best-effort, nunca
lanza), sin los commits de las tareas — o directamente contra una rama que el
remoto no tiene.

Se pinea aquí:
  * `incremental` → el cierre empuja la punta (el commit de cierre llega al remoto);
  * `push_policy='forbidden'` → el cierre NO empuja nada (ni en `final_only`, donde
    el push iba ANTES de comprobar la política que dice «este proyecto nunca empuja»);
  * un push rechazado → motivo accionable en `skipped_reason` y NUNCA una llamada a
    la API del proveedor (un PR contra una rama remota incompleta es el defecto que
    este plan existe para matar).

Git real contra un remoto `file://` en `tmp_path`, sin red.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.integration._git_helpers import commit_to_branch, seed_bare_repo

if TYPE_CHECKING:
    from workers.plan_git import PlanGitWorkflow

pytestmark = pytest.mark.integration

_PLAN_BRANCH = "plan/abcd1234-cierre"


def _sha(repo: Path, ref: str) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", ref],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def _local_bare_with_plan_branch(tmp_path: Path) -> tuple[Path, Path]:
    """`(bare local con origin→remoto, remoto)`; la rama del plan sale de la main
    del REMOTO (historia compartida) y lleva un commit de tarea."""
    from workers.git_repos import BareRepoLayout, BareRepoManager, _run_git

    remote_bare = tmp_path / "remote" / "backend.git"
    seed_bare_repo(remote_bare)

    layout = BareRepoLayout(data_root=tmp_path / "local", tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend", remote_url=str(remote_bare))
    _run_git("fetch", "origin", cwd=bare)
    _run_git("update-ref", "refs/heads/main", "refs/remotes/origin/main", cwd=bare)
    _run_git("symbolic-ref", "HEAD", "refs/heads/main", cwd=bare)
    commit_to_branch(bare, _PLAN_BRANCH, filename="app.py", content="print('task')\n")
    return bare, remote_bare


def _advance_branch(bare: Path, branch: str, *, message: str) -> str:
    """Añade un commit a la punta de ``branch`` DENTRO del bare — lo que hace el
    cierre cuando `plan_docs` commitea el changelog y lo empuja al bare.

    Plumbing (`commit-tree` + `update-ref`) en vez de clonar: `commit_to_branch`
    no es re-entrante en Windows para la misma rama (su `rmtree(ignore_errors)`
    no puede borrar los objetos read-only del clon anterior).
    """
    from workers.git_repos import _run_git

    ident = {
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    tree = _run_git("rev-parse", f"refs/heads/{branch}^{{tree}}", cwd=bare).strip()
    parent = _run_git("rev-parse", f"refs/heads/{branch}", cwd=bare).strip()
    sha = _run_git(
        "commit-tree", tree, "-p", parent, "-m", message, cwd=bare, env_extra=ident
    ).strip()
    _run_git("update-ref", f"refs/heads/{branch}", sha, cwd=bare)
    return sha


def _workflow(bare: Path, *, opener: object, **policy_kwargs: str) -> PlanGitWorkflow:
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    return PlanGitWorkflow(
        bare_repo_path=bare,
        plan_branch=_PLAN_BRANCH,
        policies=PlanGitPolicies(**policy_kwargs),  # type: ignore[arg-type]
        pr_opener=opener,
        base_branch="main",
    )


def test_close_pushes_the_closure_commit_in_incremental_mode(tmp_path: Path) -> None:
    """El commit que el cierre añade a la rama (la doc de cierre) llega al remoto."""
    from workers.git_repos import _run_git

    bare, remote_bare = _local_bare_with_plan_branch(tmp_path)
    # El push incremental por-tarea (T3) ya dejó la rama en el remoto…
    _run_git("push", "origin", f"refs/heads/{_PLAN_BRANCH}:refs/heads/{_PLAN_BRANCH}", cwd=bare)
    task_sha = _sha(remote_bare, f"refs/heads/{_PLAN_BRANCH}")
    assert task_sha is not None

    # …y AHORA el cierre commitea la documentación al bare (plan_docs), sin empujar.
    closure_sha = _advance_branch(bare, _PLAN_BRANCH, message="docs(changelog): cierre")
    assert closure_sha != task_sha, "precondición: la punta avanzó"
    assert _sha(remote_bare, f"refs/heads/{_PLAN_BRANCH}") == task_sha

    info = _workflow(
        bare, opener=lambda _t, _b: "https://pr.test/1", branch_push_mode="incremental"
    ).open_plan_pr(title="Plan X", body="body")

    assert info.url == "https://pr.test/1", info
    assert _sha(remote_bare, f"refs/heads/{_PLAN_BRANCH}") == closure_sha, (
        "el cierre debe empujar la punta de la rama (con la doc de cierre) al remoto"
    )


def test_close_does_not_push_when_push_forbidden(tmp_path: Path) -> None:
    """`push_policy='forbidden'` = «el proyecto nunca empuja», también en final_only
    (donde el push iba ANTES de mirar la política)."""
    bare, remote_bare = _local_bare_with_plan_branch(tmp_path)
    called: list[str] = []

    info = _workflow(
        bare,
        opener=lambda t, _b: called.append(t) or "nope",
        branch_push_mode="final_only",
        push_policy="forbidden",
    ).open_plan_pr(title="Plan X", body="body")

    assert info.skipped_reason == "push_policy=forbidden"
    assert called == []
    assert _sha(remote_bare, f"refs/heads/{_PLAN_BRANCH}") is None, (
        "un proyecto con push prohibido no debe dejar la rama en el remoto"
    )


def test_close_reports_actionable_reason_when_the_push_is_rejected(tmp_path: Path) -> None:
    """Si el remoto rechaza el push (rama divergente), no se abre un PR contra una
    rama remota incompleta: se reporta el motivo."""
    bare, remote_bare = _local_bare_with_plan_branch(tmp_path)
    # El remoto tiene su propia punta en la MISMA rama → push non-fast-forward.
    commit_to_branch(remote_bare, _PLAN_BRANCH, filename="other.py", content="# ajeno\n")
    remote_sha = _sha(remote_bare, f"refs/heads/{_PLAN_BRANCH}")
    called: list[str] = []

    info = _workflow(
        bare,
        opener=lambda t, _b: called.append(t) or "nope",
        branch_push_mode="incremental",
    ).open_plan_pr(title="Plan X", body="body")

    assert info.url is None
    assert info.skipped_reason and "empujar" in info.skipped_reason, info
    assert called == [], "NUNCA se llama a la API del proveedor con la rama sin empujar"
    assert _sha(remote_bare, f"refs/heads/{_PLAN_BRANCH}") == remote_sha
