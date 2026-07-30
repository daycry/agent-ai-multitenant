"""Los commits que la plataforma firma llevan UNA sola identidad (causa raíz A).

Prueba de comportamiento con git real: el commit raíz sintético del bare
(`seed_initial_commit_if_empty`) y el commit de tarea (`commit_task`) conviven en el
MISMO historial. Antes iban firmados con dos emails distintos
(``platform@agentic.local`` vs ``noreply@agentic.local``), así que el proveedor
atribuía el historial de un plan a dos autores.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _authors(repo: Path, ref: str) -> list[str]:
    out = subprocess.run(
        ["git", "log", "--format=%ae%n%ce", ref],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def test_seed_and_task_commits_share_one_identity(tmp_path: Path) -> None:
    from workers.git_identity import PLATFORM_GIT_EMAIL
    from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager
    from workers.plan_git import CommitTrailers, commit_task

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    bare = mgr.ensure_repo("backend")
    assert mgr.seed_initial_commit_if_empty("backend", default_branch="main") is True

    worktree = WorktreeManager(layout, "backend").add("task-1", branch="plan/aaaa1111-x")
    (Path(worktree) / "app.py").write_text("print('hi')\n", encoding="utf-8")
    commit_task(
        Path(worktree),
        message="task 1",
        trailers=CommitTrailers(plan_id="p1", task_id="t1", execution_id="e1"),
    )
    subprocess.run(
        ["git", "push", str(bare), "HEAD:refs/heads/plan/aaaa1111-x"],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=True,
    )

    identities = _authors(bare, "refs/heads/plan/aaaa1111-x")
    assert len(identities) >= 4, identities  # 2 commits × (autor + committer)
    assert set(identities) == {PLATFORM_GIT_EMAIL}, identities
