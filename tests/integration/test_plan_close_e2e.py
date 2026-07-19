"""cadena-pr T9 — e2e de cierre: el auto-PR apunta a la rama con los commits.

Ata las piezas del plan cadena-pr end-to-end contra un remoto `file://`:
  (a) el PR se abre para la rama de FUENTE ÚNICA (`plan_git_identity`), la misma que
      lleva los commits de las tareas — no una re-slugificada del título (P1);
  (b) `plan.pr_url` + `plan.pr_branch` quedan PERSISTIDOS (P6/T4), no solo en logs;
  (c) la rama existe en el remoto tras el push del cierre (P3).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

from tests.integration._git_helpers import commit_to_branch, seed_bare_repo

pytestmark = pytest.mark.integration

_ORG_SLUG = "org-t9close"


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(dsn: str, remote_url: str) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "plan": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE plans, projects, organizations RESTART IDENTITY CASCADE")
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Org', $2)",
            ids["tenant"],
            _ORG_SLUG,
        )
        git_config = json.dumps(
            {
                "remote_url": remote_url,
                "provider": "generic",
                "auth_mode": "pat",
                "default_branch": "main",
            }
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, slug, status, is_template, git_config)"
            " VALUES ($1, $2, 'Backend', 'backend', 'active', false, $3::jsonb)",
            ids["project"],
            ids["tenant"],
            git_config,
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, slug, status)"
            " VALUES ($1, $2, $3, 'My plan', 'my-plan', 'in_progress')",
            ids["plan"],
            ids["tenant"],
            ids["project"],
        )
    finally:
        await conn.close()
    return ids


@pytest.mark.asyncio
async def test_plan_close_opens_pr_against_branch_with_commits(
    _migrated: None, migrations_pg_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workers import plan_pr
    from workers.git_repos import BareRepoLayout, BareRepoManager
    from workers.plan_git import plan_git_identity

    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Fake remote + the project's bare (single source, name = project.slug) carrying
    # the plan branch with a task commit.
    remote_bare = tmp_path / "remote" / "backend.git"
    seed_bare_repo(remote_bare)
    ids = await _seed(migrations_pg_dsn, str(remote_bare))
    plan_id = str(ids["plan"])
    identity = plan_git_identity(plan_id, "my-plan", "backend")

    data_root = tmp_path / "local"
    layout = BareRepoLayout(data_root=data_root, tenant_slug=_ORG_SLUG, project_slug="backend")
    bare = BareRepoManager(layout).ensure_repo("backend", remote_url=str(remote_bare))
    # La main local debe COMPARTIR HISTORIA con la del remoto: el guard de
    # ancestro del auto-PR (P6) rechaza una base local sembrada sintética
    # (era exactamente este fixture: dos seed_bare_repo independientes con
    # commits iniciales distintos → merge-base imposible → PR skipped).
    subprocess.run(
        ["git", "fetch", str(remote_bare), "main:refs/heads/main"],
        cwd=str(bare),
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
        cwd=str(bare),
        capture_output=True,
        text=True,
        check=True,
    )
    commit_to_branch(bare, identity.plan_branch, filename="app.py", content="print('hi')\n")

    # A PAT project so the PR opener is built; stub the Vault secret + the opener
    # (a file:// remote has no PR REST API) and record the head it targets.
    monkeypatch.setattr(plan_pr, "_resolve_git_secret", lambda *_a, **_k: ("user", "tok", None))
    recorded: dict[str, str] = {}

    def _fake_build_pr_opener(*, provider, remote_url, token, head, base):
        recorded["head"] = head
        recorded["base"] = base
        return lambda _title, _body: "https://fake.test/owner/backend/pull/7"

    monkeypatch.setattr(plan_pr, "build_pr_opener", _fake_build_pr_opener)

    settings = SimpleNamespace(database_url=async_dsn, data_root=str(data_root))

    # (c) INCREMENTAL (the default): the per-task push (T3) mirrors the branch to the
    # remote BEFORE close — that is where the branch reaches origin in this mode
    # (open_plan_pr only force-pushes for final_only). This exercises the T3 wiring.
    push_status = await plan_pr.push_plan_branch_to_remote(
        settings,
        project_id=ids["project"],
        plan_id=plan_id,
        plan_slug="my-plan",
        tenant_slug=_ORG_SLUG,
        project_slug="backend",
    )
    assert push_status == "pushed"
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{identity.plan_branch}"],
        cwd=str(remote_bare),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0 and proc.stdout.strip(), "branch must be on the remote pre-close"

    # Close the plan → the auto-PR.
    result = await plan_pr._open_plan_pr_async(
        ids["project"], plan_id, title="My plan", body="body", settings=settings
    )

    # (a) the PR head is the SINGLE-SOURCE plan branch (the one with the commits) —
    # not a re-slugified title (P1).
    assert recorded["head"] == identity.plan_branch
    assert recorded["base"] == "main"
    assert result["url"] == "https://fake.test/owner/backend/pull/7"

    # (b) pr_url + pr_branch PERSISTED on the plan (P6/T4) — not just in logs.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT pr_url, pr_branch, pr_error FROM plans WHERE id = $1", ids["plan"]
        )
    finally:
        await conn.close()
    assert row["pr_url"] == "https://fake.test/owner/backend/pull/7"
    assert row["pr_branch"] == identity.plan_branch
    assert row["pr_error"] is None
