"""PROJ-03 + G-04/P1-08 (auditoría proyecto 2026-07-17): integridad
referencial tras un restore y tenants muertos.

El restore per-tenant copia con `session_replication_role = replica` (FK
triggers apagados): si el bundle y la DB viva divergen (catálogo builtin,
filas cross-tenant, restores parciales) quedan HUÉRFANOS que ninguna FK
volverá a validar. El sweep post-restore los detecta y borra (iterando: un
borrado puede destapar huérfanos de segundo nivel). El reconciler, además,
VIGILA (solo WARNING) hijos de tenants inexistentes.
"""

from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_orphans(superuser_dsn: str) -> dict[str, object]:
    """Siembra (con FK triggers apagados vía superuser, como hace el restore):
    - un tenant vivo con un proyecto sano,
    - un proyecto huérfano de un tenant INEXISTENTE,
    - un plan colgando del proyecto huérfano (huérfano de 2º nivel tras
      borrar el proyecto)."""
    tenant, ghost = uuid4(), uuid4()
    project_ok, project_orphan, plan_orphan = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(superuser_dsn)
    try:
        async with conn.transaction():
            await conn.execute("TRUNCATE plans, projects, organizations RESTART IDENTITY CASCADE")
            await conn.execute("SET LOCAL session_replication_role = replica")
            await conn.execute(
                "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)",
                tenant,
                f"ri-{tenant.hex[:8]}",
            )
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, name, status) VALUES"
                " ($1, $2, 'sano', 'active'), ($3, $4, 'huerfano', 'active')",
                project_ok,
                tenant,
                project_orphan,
                ghost,
            )
            await conn.execute(
                "INSERT INTO plans (id, tenant_id, project_id, title, status)"
                " VALUES ($1, $2, $3, 'plan huerfano', 'draft')",
                plan_orphan,
                ghost,
                project_orphan,
            )
    finally:
        await conn.close()
    return {
        "tenant": tenant,
        "ghost": ghost,
        "project_ok": project_ok,
        "project_orphan": project_orphan,
        "plan_orphan": plan_orphan,
    }


@pytest.mark.asyncio
async def test_sweep_deletes_orphans_transitively(
    _migrated: None, admin_database_url: str, admin_pg_dsn: str
) -> None:
    from workers.maintenance.integrity import sweep_fk_orphans

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_orphans(admin_pg_dsn)

        async with sm() as s, s.begin():
            report = await sweep_fk_orphans(s)

        async with sm() as s:
            live_projects = list((await s.execute(text("SELECT id FROM projects"))).scalars())
            live_plans = list((await s.execute(text("SELECT id FROM plans"))).scalars())
        # El proyecto sano sobrevive; el huérfano y su plan (2º nivel) caen.
        assert live_projects == [ids["project_ok"]]
        assert live_plans == []
        assert sum(report.values()) >= 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sweep_is_noop_on_healthy_db(
    _migrated: None, admin_database_url: str, admin_pg_dsn: str
) -> None:
    from workers.maintenance.integrity import sweep_fk_orphans

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_orphans(admin_pg_dsn)
        async with sm() as s, s.begin():
            await sweep_fk_orphans(s)
        # Segunda pasada sobre una DB ya sana: 0 borrados.
        async with sm() as s, s.begin():
            report = await sweep_fk_orphans(s)
        async with sm() as s:
            live = list((await s.execute(text("SELECT id FROM projects"))).scalars())
        assert report == {}
        assert live == [ids["project_ok"]]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tenant_children_check_reports_ghosts(
    _migrated: None, admin_database_url: str, admin_pg_dsn: str
) -> None:
    from workers.maintenance.integrity import check_tenant_children

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        await _seed_orphans(admin_pg_dsn)

        async with sm() as s:
            report = await check_tenant_children(s)
        # Un proyecto y un plan cuelgan de un tenant inexistente.
        assert report.get("projects") == 1
        assert report.get("plans") == 1
    finally:
        await engine.dispose()
