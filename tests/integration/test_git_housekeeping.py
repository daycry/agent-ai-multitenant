"""G-08 (auditoría proyecto 2026-07-17): higiene programada del bare repo.

Sin `git gc` programado los objetos prune-packables crecen sin límite (590 en
dev), los locks huérfanos de operaciones abortadas bloquean para siempre y las
ramas `plan/*` de planes ya cerrados (con su PR abierto en el remoto) se
acumulan. `workers.git_housekeeping` (mensual): gc ligero + borra locks >24h +
poda ramas de planes completed/archived con PR, con ref de rescate.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(
    monkeypatch: pytest.MonkeyPatch,
    migrations_pg_dsn: str,
    test_redis_url: str,
    tmp_path: Path,
):
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    monkeypatch.setenv("WORKERS_EVENTS_REDIS_URL", test_redis_url)
    monkeypatch.setenv("WORKERS_DATA_ROOT", str(tmp_path))
    from workers.config import get_settings, reset_settings_cache

    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


def _git_out(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


async def _seed(dsn: str, *, plan_done: object, plan_live: object) -> dict[str, object]:
    tenant, project = uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE plans, projects, organizations RESTART IDENTITY CASCADE")
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)",
            tenant,
            f"hk-{tenant.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, slug)"
            " VALUES ($1, $2, 'P', 'active', 'p')",
            project,
            tenant,
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, slug, pr_url) VALUES"
            " ($1, $2, $3, 'Hecho', 'completed', 'hecho', 'https://git.example/pr/1'),"
            " ($4, $2, $3, 'Vivo', 'in_progress', 'vivo', NULL)",
            plan_done,
            tenant,
            project,
            plan_live,
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "project": project}


def test_housekeeping_gc_locks_and_merged_plan_branches(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    import asyncio

    from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager
    from workers.maintenance.cleanup import git_housekeeping
    from workers.plan_git import make_plan_branch_name

    plan_done, plan_live = uuid4(), uuid4()
    asyncio.run(_seed(migrations_pg_dsn, plan_done=plan_done, plan_live=plan_live))

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("p")
    seed_bare_repo(bare)
    mgr = WorktreeManager(layout, "p")
    branch_done = make_plan_branch_name(str(plan_done), "hecho")
    branch_live = make_plan_branch_name(str(plan_live), "vivo")
    # Crear las ramas y soltar los worktrees (solo importan las ramas).
    for task, branch in (("t-done", branch_done), ("t-live", branch_live)):
        mgr.add(task, branch=branch)
    tip_done = _git_out("rev-parse", branch_done, cwd=bare)

    # Lock huérfano (>24h) + lock fresco (se conserva).
    stale_lock = bare / "refs.lock"
    stale_lock.write_text("stale", encoding="utf-8")
    old = time.time() - 3 * 86400
    os.utime(stale_lock, (old, old))
    # Fresco → se conserva. (shallow.lock: real pero inerte fuera de un fetch
    # shallow; packed-refs.lock bloquearía el propio `branch -D` del task.)
    fresh_lock = bare / "shallow.lock"
    fresh_lock.write_text("fresh", encoding="utf-8")

    result = git_housekeeping()

    assert result["repos"] == 1
    assert result["locks_removed"] == 1
    assert result["branches_pruned"] == 1
    assert not stale_lock.exists()
    assert fresh_lock.exists()
    branches = _git_out("branch", "--format=%(refname:short)", cwd=bare)
    assert branch_done not in branches.split()
    assert branch_live in branches.split()
    # La rama podada dejó ref de rescate apuntando a su tip.
    assert _git_out("rev-parse", f"refs/rescue/{branch_done}", cwd=bare) == tip_done
