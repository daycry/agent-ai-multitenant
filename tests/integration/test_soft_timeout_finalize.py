"""prod-06 (MUST-ADDRESS a): el handler del soft-timeout finaliza la fila.

Cuando salta ``SoftTimeLimitExceeded`` (default 7500s tras el fix A3 — un run
genuinamente colgado), ``_finalize_soft_timeout`` NO deja la ejecución ``running``
para siempre (esperando al sweeper): la finaliza clasificando por el flag de
cancelación — con ``cancel_requested_at`` → ``cancelled`` (was_cancel=True, sin
DLQ); sin flag → ``failed(soft_time_limit_exceeded)`` (was_cancel=False, con DLQ).
El kill del contenedor huérfano es best-effort (Docker no está en el test).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ._redis_url import TEST_REDIS_URL  # con credencial; ver _redis_url.py

pytestmark = pytest.mark.integration


async def _seed_running_execution(dsn: str, *, cancel_requested: bool) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4(), "exec": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', 't-soft')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'P')",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status)"
            " VALUES ($1, $2, $3, 'run', 'in_progress')",
            ids["task"],
            ids["tenant"],
            ids["project"],
        )
        cancel_clause = "now()" if cancel_requested else "NULL"
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, started_at,"
            f" cancel_requested_at) VALUES ($1, $2, $3, 'running', now(), {cancel_clause})",
            ids["exec"],
            ids["tenant"],
            ids["task"],
        )
    finally:
        await conn.close()
    return ids


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, alembic_config, migrations_pg_dsn: str):
    command.upgrade(alembic_config, "head")
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    monkeypatch.setenv("WORKERS_EVENTS_REDIS_URL", TEST_REDIS_URL)
    from workers.config import get_settings, reset_settings_cache

    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


async def _status(dsn: str, exec_id: UUID) -> tuple[str, str | None, bool]:
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT status, abort_code, completed_at IS NOT NULL AS done"
                        " FROM executions WHERE id = :id"
                    ),
                    {"id": exec_id},
                )
            ).one()
        return row.status, row.abort_code, row.done
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_soft_timeout_without_cancel_flag_fails_and_dlqs(
    workers_settings: object, migrations_pg_dsn: str
) -> None:
    from workers.tasks import _finalize_soft_timeout

    ids = await _seed_running_execution(migrations_pg_dsn, cancel_requested=False)
    request = {"task_id": str(ids["task"]), "tenant_id": str(ids["tenant"])}

    was_cancel = await _finalize_soft_timeout(workers_settings, request)  # type: ignore[arg-type]

    assert was_cancel is False  # → el caller SÍ manda al DLQ
    status, abort_code, done = await _status(migrations_pg_dsn, ids["exec"])
    assert status == "failed"
    assert abort_code == "soft_time_limit_exceeded"
    assert done is True  # completed_at sellado → finalize tardío idempotente


@pytest.mark.asyncio
async def test_soft_timeout_with_cancel_flag_is_cancelled_no_dlq(
    workers_settings: object, migrations_pg_dsn: str
) -> None:
    from workers.tasks import _finalize_soft_timeout

    ids = await _seed_running_execution(migrations_pg_dsn, cancel_requested=True)
    request = {"task_id": str(ids["task"]), "tenant_id": str(ids["tenant"])}

    was_cancel = await _finalize_soft_timeout(workers_settings, request)  # type: ignore[arg-type]

    assert was_cancel is True  # → el caller NO manda al DLQ (fue un cancel)
    status, _abort_code, done = await _status(migrations_pg_dsn, ids["exec"])
    assert status == "cancelled"
    assert done is True
