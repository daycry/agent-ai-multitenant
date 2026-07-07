"""Integration test — backfill de worktrees con diff sin empujar (M4).

No-atomicidad DB↔git: `finalize_execution` + `transition_task_after_run` avanzan la
TAREA (in_review/done) en una txn que commitea, y `_commit_and_push_worktree` corre
DESPUÉS. Un crash en esa ventana deja la tarea avanzada con el diff NUNCA en el bare
→ el PR final del plan sale incompleto. Además `commit_task` sobre árbol limpio salta
el push (sub-ventana commiteado-pero-no-empujado). El reconciler no tenía pasada del
lado git. Esta 4ª pasada localiza el worktree (superviviente en disco, TTL 30d),
commitea lo pendiente y SIEMPRE empuja a la rama del plan, idempotente por el trailer
``Execution-Id``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.git_repos import _run_git
from workers.plan_git import make_plan_branch_name

pytestmark = pytest.mark.integration

_ORG_SLUG = "acme-m4"
_PROJECT_SLUG = "api-m4"
_PLAN_SLUG = "p-m4"


class _FakeRedis:
    """Async Redis mínimo: SET NX EX + GET + el Lua de release (CAS-del)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key: str):
        return self.store.get(key)

    async def eval(self, _script: str, _n: int, key: str, token: str) -> int:
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def settings_with_data_root(
    monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str, tmp_path: Path
):
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    monkeypatch.setenv("WORKERS_DATA_ROOT", str(tmp_path))
    from workers.config import get_settings, reset_settings_cache

    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


async def _seed(dsn: str, *, exec_status: str = "done") -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "plan": uuid4(),
        "task": uuid4(),
        "exec": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, plans, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T m4', $2)",
            ids["tenant"],
            _ORG_SLUG,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, slug, status, is_template)"
            " VALUES ($1, $2, 'P', $3, 'active', false)",
            ids["project"],
            ids["tenant"],
            _PROJECT_SLUG,
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, slug, status)"
            " VALUES ($1, $2, $3, 'Plan', $4, 'in_progress')",
            ids["plan"],
            ids["tenant"],
            ids["project"],
            _PLAN_SLUG,
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority)"
            " VALUES ($1, $2, $3, $4, 'task', 'in_review', 'medium')",
            ids["task"],
            ids["tenant"],
            ids["project"],
            ids["plan"],
        )
        # Ejecución terminal, asentada hace 30 min (> settle de 5 min).
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, started_at, completed_at)"
            f" VALUES ($1, $2, $3, '{exec_status}', now() - interval '1 hour',"
            " now() - interval '30 minutes')",
            ids["exec"],
            ids["tenant"],
            ids["task"],
        )
        return ids
    finally:
        await conn.close()


async def _provision(settings: Any, ids: dict[str, UUID]) -> str:
    from workers.execution import _provision_worktree

    wt = await _provision_worktree(
        settings,
        tenant_slug=_ORG_SLUG,
        project_slug=_PROJECT_SLUG,
        plan_id=str(ids["plan"]),
        plan_slug=_PLAN_SLUG,
        task_id=str(ids["task"]),
    )
    assert wt is not None
    return wt


def _bare_branch_body(settings: Any, ids: dict[str, UUID]) -> str:
    bare = str(
        Path(settings.data_root)
        / "projects"
        / _ORG_SLUG
        / _PROJECT_SLUG
        / "repos"
        / f"{_PROJECT_SLUG}.git"
    )
    branch = make_plan_branch_name(str(ids["plan"]), _PLAN_SLUG)
    return _run_git("-C", bare, "log", "--format=%B", branch)


@pytest.mark.asyncio
async def test_backfills_uncommitted_worktree_and_is_idempotent(
    _migrated: None, settings_with_data_root: Any, migrations_pg_dsn: str
) -> None:
    from workers.maintenance import _reconcile_unpushed_worktrees

    settings = settings_with_data_root
    ids = await _seed(migrations_pg_dsn)
    wt = await _provision(settings, ids)
    # El agente escribió el fichero pero el crash ocurrió antes del commit/push.
    (Path(wt) / "deliverable.txt").write_text("work that must not be lost\n", encoding="utf-8")

    engine = create_async_engine(settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        n = await _reconcile_unpushed_worktrees(
            settings, sm, _FakeRedis(), now=datetime.now(UTC), min_age=timedelta(minutes=5)
        )
        assert n == 1
        body = _bare_branch_body(settings, ids)
        assert f"Execution-Id: {ids['exec']}" in body
        assert "Task-Id:" in body

        # 2ª pasada consecutiva = no-op (el trailer ya está en la rama).
        n2 = await _reconcile_unpushed_worktrees(
            settings, sm, _FakeRedis(), now=datetime.now(UTC), min_age=timedelta(minutes=5)
        )
        assert n2 == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_noop_when_worktree_clean_and_no_change(
    _migrated: None, settings_with_data_root: Any, migrations_pg_dsn: str
) -> None:
    """Una ejecución `done` cuyo agente no produjo cambio (worktree limpio, sin commit
    con su Execution-Id) NO se fuerza a la rama: no hay nada que backfillear."""
    from workers.maintenance import _reconcile_unpushed_worktrees

    settings = settings_with_data_root
    ids = await _seed(migrations_pg_dsn)
    await _provision(settings, ids)  # worktree limpio, sin escribir nada

    engine = create_async_engine(settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        n = await _reconcile_unpushed_worktrees(
            settings, sm, _FakeRedis(), now=datetime.now(UTC), min_age=timedelta(minutes=5)
        )
        assert n == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_skips_recent_execution_within_settle(
    _migrated: None, settings_with_data_root: Any, migrations_pg_dsn: str
) -> None:
    """Un run que ACABA de terminar (dentro del settle de 5 min) se deja al worker,
    que aún puede estar en post-proceso (commit/push)."""
    from workers.maintenance import _reconcile_unpushed_worktrees

    settings = settings_with_data_root
    ids = await _seed(migrations_pg_dsn)
    wt = await _provision(settings, ids)
    (Path(wt) / "deliverable.txt").write_text("x\n", encoding="utf-8")
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE executions SET completed_at = now() - interval '1 minute' WHERE id=$1",
            ids["exec"],
        )
    finally:
        await conn.close()

    engine = create_async_engine(settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        n = await _reconcile_unpushed_worktrees(
            settings, sm, _FakeRedis(), now=datetime.now(UTC), min_age=timedelta(minutes=5)
        )
        assert n == 0  # dentro del settle → no se toca
    finally:
        await engine.dispose()
