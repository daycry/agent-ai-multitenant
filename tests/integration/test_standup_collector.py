"""Collector SQL del standup (ADR 0120) contra Postgres real.

El núcleo del standup está cubierto en unit con fakes
(tests/unit/test_standup.py); aquí se verifica la pieza que allí se inyecta:
``_collect_tenant_summary`` cuenta lo que dice contar (hecho AYER por
updated_at, en curso/bloqueado ahora, escalados, coste de ayer) y
``_load_tenant_configs`` lee los settings de plataforma con sus defaults.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(dsn: str) -> UUID:
    tenant = uuid4()
    project = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, plans, projects,"
            " user_org_memberships, organizations, users, platform_settings"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, 'SU', 'standup-t'), ($2, 'P', 'standup-p')",
            tenant,
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'SU')",
            project,
            tenant,
        )
        yesterday = datetime.now(tz=UTC) - timedelta(days=1)
        # 2 tareas done AYER + 1 done hoy (fuera de ventana) + 1 bloqueada +
        # 1 in_progress.
        for status, when in (
            ("done", yesterday),
            ("done", yesterday),
            ("done", datetime.now(tz=UTC)),
            ("blocked", yesterday),
            ("in_progress", yesterday),
        ):
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, title, status, updated_at)"
                " VALUES ($1, $2, $3, 'T', $4, $5)",
                uuid4(),
                tenant,
                project,
                status,
                when,
            )
        task = uuid4()
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title) VALUES ($1, $2, $3, 'E')",
            task,
            tenant,
            project,
        )
        # 1 run escalado ahora + 1 run de AYER con coste (cuenta) + 1 de hoy (no).
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, steps_log)"
            " VALUES ($1, $2, $3, 'needs_human_review', '[]'::jsonb)",
            uuid4(),
            tenant,
            task,
        )
        await conn.execute(
            "INSERT INTO executions"
            " (id, tenant_id, task_id, status, steps_log, total_cost_usd, created_at)"
            " VALUES ($1, $2, $3, 'done', '[]'::jsonb, 2.5, $4)",
            uuid4(),
            tenant,
            task,
            yesterday,
        )
        await conn.execute(
            "INSERT INTO executions"
            " (id, tenant_id, task_id, status, steps_log, total_cost_usd)"
            " VALUES ($1, $2, $3, 'done', '[]'::jsonb, 9.9)",
            uuid4(),
            tenant,
            task,
        )
    finally:
        await conn.close()
    return tenant


def test_collector_counts_what_it_claims(schema_at_head, migrations_pg_dsn: str) -> None:
    from workers.standup import _collect_tenant_summary

    async def _main() -> None:
        tenant = await _seed(migrations_pg_dsn)
        async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(async_dsn)
        try:
            sm = async_sessionmaker(engine, expire_on_commit=False)
            summary = await _collect_tenant_summary(sm, tenant)
        finally:
            await engine.dispose()
        assert summary.tasks_done_yesterday == 2
        assert summary.tasks_blocked == 1
        assert summary.tasks_in_progress == 1
        assert summary.runs_waiting_human == 1
        # Solo el coste de AYER (2.5), no el de hoy (9.9).
        assert summary.cost_usd_yesterday == pytest.approx(2.5)

    asyncio.run(_main())


def test_tenant_configs_read_platform_defaults(schema_at_head, migrations_pg_dsn: str) -> None:
    from workers.standup import _load_tenant_configs

    async def _main() -> None:
        tenant = await _seed(migrations_pg_dsn)
        async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(async_dsn)
        try:
            sm = async_sessionmaker(engine, expire_on_commit=False)
            configs = await _load_tenant_configs(sm)
        finally:
            await engine.dispose()
        by_id = {c.tenant_id: c for c in configs}
        assert tenant in by_id
        # Defaults del ADR 0120 sin settings persistidos: habilitado a las 08.
        assert by_id[tenant].enabled is True
        assert by_id[tenant].hour == 8

    asyncio.run(_main())
