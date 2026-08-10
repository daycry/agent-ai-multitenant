"""Celery worker robustness (Plan 06.14 task_06_14_04).

Covers the three safe hardening measures (no auto-retry of agent runs —
those are expensive and side-effecting):

  * workers-orchestrator-1 — idempotency: `supersede_running_executions`
    closes out an orphan `running` row so a re-delivered task (acks_late +
    worker crash) never accumulates duplicate live executions.
  * workers-orchestrator-2 — dead-letter: a failed `run_execution` lands
    on `dlq:executions` for operator visibility instead of vanishing.
  * workers-orchestrator-10 — backstop time limits on every task so a hung
    job cannot pin a worker slot forever.

None of these need Docker.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import Execution, ExecutionStatus, Project, Task
from api_server.db.execution_repo import (
    create_running_execution,
    list_executions_for_task,
    supersede_running_executions,
)
from api_server.db.models import Organization
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.execution import CrossTenantExecutionError

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_task(sm: async_sessionmaker) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, projects, organizations"
                " RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Idem tenant", slug="idem-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Idem project",
                status="active",
                is_template=False,
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="Idem task",
                status="in_progress",
                priority="medium",
            )
        )
    return ids


# ---------------------------------------------------------------------------
# Idempotency — supersede stale running rows
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_supersede_closes_running_rows_only(_migrated: None, admin_database_url: str) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task(sm)

        # Two orphan `running` rows (the crash-then-redeliver scenario) +
        # one already-terminal row that must be left untouched.
        async with sm() as s, s.begin():
            await create_running_execution(s, tenant_id=ids["tenant"], task_id=ids["task"])
            await create_running_execution(s, tenant_id=ids["tenant"], task_id=ids["task"])
            done = await create_running_execution(s, tenant_id=ids["tenant"], task_id=ids["task"])
            done.status = ExecutionStatus.DONE
            done.completed_at = datetime.now(UTC)

        async with sm() as s, s.begin():
            count = await supersede_running_executions(
                s, tenant_id=ids["tenant"], task_id=ids["task"]
            )
        assert count == 2

        async with sm() as s:
            rows = await list_executions_for_task(s, ids["task"])
        by_status: dict[str, int] = {}
        for r in rows:
            by_status[r.status] = by_status.get(r.status, 0) + 1
        # Two superseded → failed, one untouched done; no live `running` left.
        assert by_status.get(ExecutionStatus.RUNNING, 0) == 0
        assert by_status.get(ExecutionStatus.FAILED, 0) == 2
        assert by_status.get(ExecutionStatus.DONE, 0) == 1
        superseded = [r for r in rows if r.status == ExecutionStatus.FAILED]
        assert all(r.abort_code == "superseded" for r in superseded)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_supersede_leaves_an_audit_trail(_migrated: None, admin_database_url: str) -> None:
    """AUD16-21: los relanzamientos por re-entrega dejan task_audit_events —
    la cronología de una task debe ser reconstruible SOLO desde BD."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task(sm)
        async with sm() as s, s.begin():
            stale = await create_running_execution(s, tenant_id=ids["tenant"], task_id=ids["task"])
            stale_id = stale.id
        async with sm() as s, s.begin():
            await supersede_running_executions(s, tenant_id=ids["tenant"], task_id=ids["task"])

        async with sm() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT kind, actor, payload::text AS payload FROM task_audit_events"
                        " WHERE task_id = :t"
                    ),
                    {"t": ids["task"]},
                )
            ).all()
            sealed = await s.get(Execution, stale_id)
    finally:
        await engine.dispose()

    superseded_events = [r for r in rows if r.kind == "execution_superseded"]
    assert len(superseded_events) == 1
    assert superseded_events[0].actor == "system:redelivery_guard"
    assert str(stale_id) in superseded_events[0].payload
    assert sealed is not None and sealed.memorize_skip_reason == "administrative_finalize"


@pytest.mark.asyncio
async def test_supersede_noop_when_no_running_rows(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task(sm)
        async with sm() as s, s.begin():
            count = await supersede_running_executions(
                s, tenant_id=ids["tenant"], task_id=ids["task"]
            )
        assert count == 0
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Dead-letter — a failed run_execution is recorded, never auto-retried
# ---------------------------------------------------------------------------
def test_run_execution_dead_letters_on_failure(
    _migrated: None,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cross-tenant payload makes conduct_execution raise; run_execution
    records it on dlq:executions and re-raises (no silent loss, no retry)."""
    import asyncio

    from workers import tasks as worker_tasks
    from workers.config import reset_settings_cache

    monkeypatch.setenv("WORKERS_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("WORKERS_EVENTS_REDIS_URL", test_redis_url)
    reset_settings_cache()

    async def _setup() -> dict[str, UUID]:
        engine = create_async_engine(admin_database_url)
        try:
            sm = async_sessionmaker(engine, expire_on_commit=False)
            ids = await _seed_task(sm)
        finally:
            await engine.dispose()
        redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
        try:
            await redis.delete("dlq:executions")
        finally:
            await redis.aclose()
        return ids

    async def _read_dlq() -> list[tuple[str, dict[str, str]]]:
        redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
        try:
            return await redis.xrange("dlq:executions")
        finally:
            await redis.aclose()

    try:
        ids = asyncio.run(_setup())
        foreign_tenant = uuid4()
        request = {
            "tenant_id": str(foreign_tenant),
            "task_id": str(ids["task"]),
            "agent_id": None,
            "task": {"id": str(ids["task"]), "title": "x", "description": "y"},
            "model": {"kind": "scripted", "decisions": [{"kind": "finish", "output": "x"}]},
        }
        with pytest.raises(CrossTenantExecutionError):
            worker_tasks.run_execution(request)

        entries = asyncio.run(_read_dlq())
        assert len(entries) == 1
        _entry_id, fields = entries[0]
        assert fields["task"] == "workers.run_execution"
        assert fields["task_id"] == str(ids["task"])
        assert "CrossTenantExecutionError" in fields["error"]
    finally:
        reset_settings_cache()


# ---------------------------------------------------------------------------
# Backstop time limits — operator-tunable platform setting (UI-editable),
# applied per-dispatch (not a hardcoded Celery global).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_execution_time_limits_default_override_and_clamp(
    _migrated: None, admin_database_url: str
) -> None:
    from api_server.db.models import PlatformSetting
    from api_server.db.platform_settings import (
        DEFAULT_EXECUTION_HARD_TIME_LIMIT_S,
        DEFAULT_EXECUTION_SOFT_TIME_LIMIT_S,
        EXECUTION_HARD_TIME_LIMIT_KEY,
        EXECUTION_SOFT_TIME_LIMIT_KEY,
        get_execution_time_limits,
        invalidate_platform_setting_cache,
    )

    async def _escrito_por_fuera() -> None:
        """Invalida como haría la escritura de verdad.

        Este test añade las filas con `session.add(PlatformSetting(...))`, o sea
        POR DEBAJO de `set_platform_setting`, que es quien invalida la caché en
        producción. Mientras la caché estuvo rota —el cliente Redis quedaba atado
        al event loop anterior y toda lectura caía a la BD— el atajo no se notaba;
        al arreglarla (2026-08-10), la segunda lectura empezó a devolver los
        DEFAULTS que había cacheado la primera aserción de este mismo test.

        Se invalida en vez de desactivar la caché a propósito: así el test sigue
        recorriendo el mismo camino de lectura que producción, cache incluida.
        """
        for key in (EXECUTION_SOFT_TIME_LIMIT_KEY, EXECUTION_HARD_TIME_LIMIT_KEY):
            await invalidate_platform_setting_cache(key)

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)

        async with sm() as s, s.begin():
            await s.execute(text("TRUNCATE platform_settings"))
        await _escrito_por_fuera()
        async with sm() as s:
            # Defaults (prod-06 A3: > mayor budget de contenedor, < visibility).
            assert await get_execution_time_limits(s) == (
                DEFAULT_EXECUTION_SOFT_TIME_LIMIT_S,
                DEFAULT_EXECUTION_HARD_TIME_LIMIT_S,
            )

        async with sm() as s, s.begin():
            s.add(PlatformSetting(key=EXECUTION_SOFT_TIME_LIMIT_KEY, value=600))
            s.add(PlatformSetting(key=EXECUTION_HARD_TIME_LIMIT_KEY, value=900))
        await _escrito_por_fuera()
        async with sm() as s:
            assert await get_execution_time_limits(s) == (600, 900)  # operator override

        async with sm() as s, s.begin():
            await s.execute(text("TRUNCATE platform_settings"))
            s.add(PlatformSetting(key=EXECUTION_SOFT_TIME_LIMIT_KEY, value=900))
            s.add(PlatformSetting(key=EXECUTION_HARD_TIME_LIMIT_KEY, value=500))
        await _escrito_por_fuera()
        async with sm() as s:
            assert await get_execution_time_limits(s) == (900, 1200)  # hard<=soft → bumped
    finally:
        await engine.dispose()
