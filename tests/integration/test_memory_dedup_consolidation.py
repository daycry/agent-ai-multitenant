"""AUD16-18 (auditoría 2026-07-16): consolidación idempotente de memorias
duplicadas EXACTAS preexistentes.

El dedup del persist (P1-2, 2026-07-12) previene duplicados NUEVOS pero no
limpia los anteriores (5 filas idénticas del batch del 07-07 seguían vivas).
La consolidación soft-borra todas menos la MÁS ANTIGUA de cada grupo
(tenant, scope, owner, contenido exacto); es idempotente y jamás cruza
scopes/owners (el mismo texto en otro scope es otra memoria).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "team": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE memory_entries, organizations RESTART IDENTITY CASCADE")
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)",
            ids["tenant"],
            f"dedup-{ids['tenant'].hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'Dedup project', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, 'Dedup team')",
            ids["team"],
            ids["tenant"],
        )
        # Tres filas idénticas (mismo scope/proyecto) — deben quedar en UNA.
        for i in range(3):
            await conn.execute(
                "INSERT INTO memory_entries"
                " (id, tenant_id, scope, type, content, project_id, created_at)"
                " VALUES ($1, $2, 'project_shared', 'semantic',"
                "  'All acceptance criteria are met.', $3,"
                "  now() - make_interval(mins => $4))",
                uuid4(),
                ids["tenant"],
                ids["project"],
                10 - i,  # la primera insertada es la más antigua
            )
        # Una lección distinta — intacta.
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, project_id)"
            " VALUES ($1, $2, 'project_shared', 'semantic',"
            "  'composer install needs the registry egress proxy.', $3)",
            uuid4(),
            ids["tenant"],
            ids["project"],
        )
        # El MISMO texto en otro scope/owner — otra memoria, intacta.
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, team_id)"
            " VALUES ($1, $2, 'team_shared', 'semantic',"
            "  'All acceptance criteria are met.', $3)",
            uuid4(),
            ids["tenant"],
            ids["team"],
        )
    finally:
        await conn.close()
    return ids


@pytest.mark.asyncio
async def test_consolidation_soft_deletes_exact_duplicates_keeping_oldest(
    _migrated: None, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    from workers.maintenance.memory_dedup import consolidate_exact_duplicate_memories

    await _seed(migrations_pg_dsn)
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s, s.begin():
            first = await consolidate_exact_duplicate_memories(s)
        async with sm() as s, s.begin():
            second = await consolidate_exact_duplicate_memories(s)
    finally:
        await engine.dispose()

    assert first == 2  # del trío idéntico sobreviven 1; las otras 2 filas intactas
    assert second == 0  # idempotente

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        alive = await conn.fetch(
            "SELECT scope, content, created_at FROM memory_entries"
            " WHERE deleted_at IS NULL ORDER BY created_at"
        )
        trio_alive = [
            r
            for r in alive
            if r["scope"] == "project_shared" and r["content"].startswith("All acceptance")
        ]
    finally:
        await conn.close()

    assert len(alive) == 3
    assert len(trio_alive) == 1
