"""Base git del proyecto: alineación con el remoto + guard de ancestro en el PR.

Visto en vivo (plan CI4, 2026-07-09): el clone inicial no dejó la rama default
LOCAL apuntando a la historia del remoto (el remoto estaba vacío o su master
apareció después), la plataforma sembró una raíz sintética, los agentes
construyeron encima y el PR final murió con el 422 crudo de GitHub («no
history in common»). Dos defensas:

  (a) ``BareRepoManager.align_default_branch``: tras el fetch del clone/sync,
      la rama default local se crea/avanza (solo fast-forward) desde
      ``origin/<default>``; un remoto vacío o una divergencia se REPORTAN en
      vez de ocultarse.
  (b) ``PlanGitWorkflow.open_plan_pr`` con ``base_branch``: antes de llamar a
      la API del proveedor, re-fetch de la base + ``merge-base`` contra la
      rama del plan; sin ancestro común → skip con un motivo ACCIONABLE (el
      422 de GitHub deja de ser el primer aviso).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration._git_helpers import commit_to_branch, seed_bare_repo

pytestmark = pytest.mark.integration


def _rev(path: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", "--verify", ref],
        cwd=str(path),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


# ===========================================================================
# (a) align_default_branch
# ===========================================================================
def _local_with_remote(tmp_path: Path, *, seed_remote: bool = True):
    from workers.git_repos import BareRepoLayout, BareRepoManager

    remote_bare = tmp_path / "remote" / "backend.git"
    if seed_remote:
        seed_bare_repo(remote_bare)
    else:
        remote_bare.mkdir(parents=True)
        subprocess.run(["git", "init", "--bare", str(remote_bare)], check=True, capture_output=True)
    layout = BareRepoLayout(data_root=tmp_path / "data", tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    local = mgr.ensure_repo("backend", remote_url=str(remote_bare))
    mgr.fetch_remote("backend")
    return mgr, local, remote_bare


def test_align_creates_local_default_from_origin(tmp_path: Path) -> None:
    mgr, local, remote = _local_with_remote(tmp_path)
    status = mgr.align_default_branch("backend", "main")
    assert status == "created"
    assert _rev(local, "refs/heads/main") == _rev(remote, "refs/heads/main")


def test_align_reports_empty_remote(tmp_path: Path) -> None:
    mgr, local, _remote = _local_with_remote(tmp_path, seed_remote=False)
    status = mgr.align_default_branch("backend", "main")
    assert status == "remote_empty"
    with pytest.raises(subprocess.CalledProcessError):
        _rev(local, "refs/heads/main")  # no inventa una raíz sintética


def test_align_fast_forwards_local_behind_origin(tmp_path: Path) -> None:
    mgr, local, remote = _local_with_remote(tmp_path)
    assert mgr.align_default_branch("backend", "main") == "created"
    commit_to_branch(remote, "main", filename="f.txt", content="v2\n")
    mgr.fetch_remote("backend")
    status = mgr.align_default_branch("backend", "main")
    assert status == "fast_forwarded"
    assert _rev(local, "refs/heads/main") == _rev(remote, "refs/heads/main")


def test_align_never_rewrites_diverged_local(tmp_path: Path) -> None:
    # El caso api-ci: la base local tiene su propia raíz (sembrada) y el remoto
    # otra — NO se pisa el trabajo local; se reporta la divergencia.
    from workers.git_repos import BareRepoLayout, BareRepoManager

    remote_bare = tmp_path / "remote" / "backend.git"
    seed_bare_repo(remote_bare)
    layout = BareRepoLayout(data_root=tmp_path / "data", tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    local = mgr.ensure_repo("backend", remote_url=str(remote_bare))
    # El seed siembra en la rama HEAD del bare; fíjala a `main` para que el
    # choque de nombres sea el del caso real (local main vs origin/main).
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
        cwd=str(local),
        check=True,
        capture_output=True,
    )
    mgr.seed_initial_commit_if_empty("backend")  # raíz sintética local
    local_before = _rev(local, "refs/heads/main")
    mgr.fetch_remote("backend")
    status = mgr.align_default_branch("backend", "main")
    assert status == "diverged"
    assert _rev(local, "refs/heads/main") == local_before  # intacta


# ===========================================================================
# (a2) has_commits + seed nombrando la rama configurada (fix 2026-07-23):
# el gate que evita sembrar una raíz sintética antes de alinear desde el remoto,
# y el endurecimiento master-vs-main del seed.
# ===========================================================================
def test_has_commits_false_until_seeded(tmp_path: Path) -> None:
    from workers.git_repos import BareRepoLayout, BareRepoManager

    layout = BareRepoLayout(data_root=tmp_path / "data", tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    assert mgr.has_commits("backend") is False  # ni existe el bare
    mgr.ensure_repo("backend")
    assert mgr.has_commits("backend") is False  # bare vacío (HEAD sin nacer)
    mgr.seed_initial_commit_if_empty("backend")
    assert mgr.has_commits("backend") is True


def test_seed_names_branch_from_configured_default(tmp_path: Path) -> None:
    from workers.git_repos import BareRepoLayout, BareRepoManager

    layout = BareRepoLayout(data_root=tmp_path / "data", tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    local = mgr.ensure_repo("backend")
    assert mgr.seed_initial_commit_if_empty("backend", default_branch="main") is True
    # El seed cayó en `main` (no en el `master` que `git init` pudo dejar en HEAD).
    assert _rev(local, "refs/heads/main")
    head = subprocess.run(
        ["git", "symbolic-ref", "HEAD"], cwd=str(local), check=True, capture_output=True, text=True
    ).stdout.strip()
    assert head == "refs/heads/main"


def test_aligned_base_makes_plan_branch_share_history_with_origin(tmp_path: Path) -> None:
    # El caso real (GitHub «Add README» → el remoto tiene su PROPIA raíz): un bare
    # local vacío alineado DESDE el remoto y una rama de plan creada sobre esa base
    # SÍ comparte historia con origin → el PR final ya no da «no history in common».
    mgr, local, _remote = _local_with_remote(tmp_path)
    assert mgr.align_default_branch("backend", "main") == "created"
    subprocess.run(
        ["git", "branch", "plan/x-demo", "main"], cwd=str(local), check=True, capture_output=True
    )
    commit_to_branch(local, "plan/x-demo", filename="w.txt", content="x\n")
    mb = subprocess.run(
        ["git", "merge-base", "plan/x-demo", "refs/remotes/origin/main"],
        cwd=str(local),
        capture_output=True,
        text=True,
        check=False,
    )
    assert mb.returncode == 0 and mb.stdout.strip()  # ancestro común ⇒ PR viable


# ===========================================================================
# (b) open_plan_pr con guard de ancestro
# ===========================================================================
def _workflow(local_bare: Path, plan_branch: str, *, base_branch: str | None, opener):
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    return PlanGitWorkflow(
        bare_repo_path=local_bare,
        plan_branch=plan_branch,
        policies=PlanGitPolicies(),
        pr_opener=opener,
        base_branch=base_branch,
    )


def _bare_with_plan_branch(tmp_path: Path, *, shared_history: bool):
    """Local bare con rama de plan + remoto cuyo main comparte (o no) la raíz."""
    from workers.git_repos import BareRepoLayout, BareRepoManager

    remote_bare = tmp_path / "remote" / "backend.git"
    seed_bare_repo(remote_bare)

    layout = BareRepoLayout(data_root=tmp_path / "data", tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    local = mgr.ensure_repo("backend", remote_url=str(remote_bare))
    if shared_history:
        mgr.fetch_remote("backend")
        mgr.align_default_branch("backend", "main")
    else:
        # Raíz local propia ≠ remoto, con el MISMO nombre de rama (main).
        subprocess.run(
            ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
            cwd=str(local),
            check=True,
            capture_output=True,
        )
        mgr.seed_initial_commit_if_empty("backend")
    # Rama del plan desde la base local + un commit de trabajo.
    subprocess.run(
        ["git", "branch", "plan/x-demo", "main"], cwd=str(local), check=True, capture_output=True
    )
    commit_to_branch(local, "plan/x-demo", filename="work.txt", content="hecho\n")
    return local


def test_pr_skips_with_actionable_reason_when_histories_diverge(tmp_path: Path) -> None:
    local = _bare_with_plan_branch(tmp_path, shared_history=False)
    calls: list[str] = []
    wf = _workflow(local, "plan/x-demo", base_branch="main", opener=lambda t, b: calls.append(t))
    info = wf.open_plan_pr(title="t", body="b")
    assert calls == []  # NUNCA llegó a la API del proveedor
    assert info.url is None
    assert info.skipped_reason and "no comparte historia" in info.skipped_reason


def test_pr_proceeds_when_base_shares_history(tmp_path: Path) -> None:
    local = _bare_with_plan_branch(tmp_path, shared_history=True)
    wf = _workflow(local, "plan/x-demo", base_branch="main", opener=lambda t, b: "http://pr/1")
    info = wf.open_plan_pr(title="t", body="b")
    assert info.url == "http://pr/1"
    assert info.skipped_reason is None


def test_pr_skips_when_remote_lacks_base_branch(tmp_path: Path) -> None:
    local = _bare_with_plan_branch(tmp_path, shared_history=True)
    wf = _workflow(local, "plan/x-demo", base_branch="develop", opener=lambda t, b: "http://pr/1")
    info = wf.open_plan_pr(title="t", body="b")
    assert info.url is None
    assert info.skipped_reason and "develop" in info.skipped_reason


def test_pr_without_base_branch_keeps_legacy_behaviour(tmp_path: Path) -> None:
    local = _bare_with_plan_branch(tmp_path, shared_history=False)
    wf = _workflow(local, "plan/x-demo", base_branch=None, opener=lambda t, b: "http://pr/1")
    info = wf.open_plan_pr(title="t", body="b")
    assert info.url == "http://pr/1"  # sin guard: contrato anterior intacto
