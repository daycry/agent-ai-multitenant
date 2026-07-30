"""G-07 (ciclo 2): el beat `workers.prune_worktrees` poda por ESTADO real.

La policy sale de la DB (task → plan): plan cerrado → TTL 48h, task blocked →
conservar siempre, resto → TTL 30d. Si la DB no responde, cae a la poda
clásica (policy vacía = default 30d) — la limpieza nunca se detiene.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration

_DAY = 86400.0


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


def _age(path: Path, *, days: float) -> None:
    stamp = time.time() - days * _DAY
    os.utime(path, (stamp, stamp))


async def _seed(dsn: str) -> dict[str, object]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "plan_closed": uuid4(),
        "plan_live": uuid4(),
        "task_closed": uuid4(),
        "task_blocked": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE tasks, plans, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)",
            ids["tenant"],
            f"wt-{ids['tenant'].hex[:8]}",  # type: ignore[union-attr]
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, slug)"
            " VALUES ($1, $2, 'P', 'active', 'p')",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status) VALUES"
            " ($1, $2, $3, 'Cerrado', 'completed'), ($4, $2, $3, 'Vivo', 'in_progress')",
            ids["plan_closed"],
            ids["tenant"],
            ids["project"],
            ids["plan_live"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority)"
            " VALUES ($1, $2, $3, $4, 'done', 'done', 'medium'),"
            " ($5, $2, $3, $6, 'atascada', 'blocked', 'medium')",
            ids["task_closed"],
            ids["tenant"],
            ids["project"],
            ids["plan_closed"],
            ids["task_blocked"],
            ids["plan_live"],
        )
    finally:
        await conn.close()
    return ids


def test_beat_prunes_by_db_policy(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    import asyncio

    from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager
    from workers.maintenance.cleanup import prune_worktrees

    ids = asyncio.run(_seed(migrations_pg_dsn))

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("p")
    seed_bare_repo(bare)
    mgr = WorktreeManager(layout, "p")
    wt_closed = mgr.add(str(ids["task_closed"]), branch="plan/aaaa-cerrado")
    wt_blocked = mgr.add(str(ids["task_blocked"]), branch="plan/bbbb-vivo")
    # Ambos con 3 días: la ciega (30d) no tocaría ninguno; la policy poda el
    # del plan cerrado (48h) y conserva el blocked aunque tuviera 90 días.
    _age(wt_closed, days=3)
    _age(wt_blocked, days=90)

    result = prune_worktrees()

    assert result["pruned"] == 1
    assert not wt_closed.exists()
    assert wt_blocked.exists()
