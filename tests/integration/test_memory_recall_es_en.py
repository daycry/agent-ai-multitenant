"""BM25 de memoria con configuración español/english + unaccent
(Plan 06.17 task_06_17_04).

Antes de esta tarea el path BM25 de ``recall.py`` usaba la configuración
``'simple'`` (``recall.py:147,149-150``): solo minúsculas + tokenización, SIN
stemming ni unaccent. Así ``arquitectura`` NO casaba ``arquitecturas`` (plural)
ni ``arquitéctura`` (acento). Aquí se verifica contra Postgres real que el path
BM25 (vía la configuración ``public.es_unaccent`` creada por la migración):

  * ``arquitectura`` casa una memoria que dice ``arquitecturas`` (stemming ES);
  * el acento es irrelevante: ``decision`` casa ``decisión`` (unaccent);
  * el inglés sigue funcionando (``database`` casa ``databases``).

El embedder no se toca: ``query_embedding=None`` → solo participa el path BM25,
que es justo lo que queremos aislar aquí.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_memories(dsn: str, contents: list[str]) -> UUID:
    """Siembra ``global`` memorias (sin owner pointer) en un tenant."""
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE memory_entries, organizations RESTART IDENTITY CASCADE")
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Tenant ES",
            "tenant-es-en",
        )
        for content in contents:
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content)"
                " VALUES ($1, $2, 'global', 'semantic', $3)",
                uuid4(),
                tenant_id,
                content,
            )
    finally:
        await conn.close()
    return tenant_id


async def _set_tenant(session, tenant_id: UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": str(tenant_id)},
    )


async def _recall(dsn: str, tenant_id: UUID, query: str) -> list[str]:
    from api_server.memorizer.recall import recall

    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _set_tenant(session, tenant_id)
            hits = await recall(
                session,
                query=query,
                tenant_id=tenant_id,
                scopes=["global"],
                query_embedding=None,
                limit=10,
            )
            return [h.content for h in hits]
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 1. stemming español: 'arquitectura' casa 'arquitecturas'
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_spanish_stemming_singular_matches_plural(
    schema_at_head, migrations_pg_dsn: str
) -> None:
    tenant_id = await _seed_memories(
        migrations_pg_dsn,
        [
            "El equipo revisa las arquitecturas propuestas cada semana.",
            "La cena del viernes fue en un restaurante italiano.",
        ],
    )
    contents = await _recall(migrations_pg_dsn, tenant_id, "arquitectura")
    assert any("arquitecturas" in c for c in contents), contents


# ---------------------------------------------------------------------------
# 2. unaccent: 'decision' casa 'decisión'
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unaccent_matches_accented(schema_at_head, migrations_pg_dsn: str) -> None:
    tenant_id = await _seed_memories(
        migrations_pg_dsn,
        [
            "La decisión final la toma el arquitecto del proyecto.",
            "El gato duerme sobre el teclado del portátil.",
        ],
    )
    contents = await _recall(migrations_pg_dsn, tenant_id, "decision")
    assert any("decisión" in c for c in contents), contents


# ---------------------------------------------------------------------------
# 3. inglés sigue funcionando: 'database' casa 'databases'
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_english_still_works(schema_at_head, migrations_pg_dsn: str) -> None:
    tenant_id = await _seed_memories(
        migrations_pg_dsn,
        [
            "The project uses two databases in production.",
            "El perro corre por el parque cada mañana.",
        ],
    )
    contents = await _recall(migrations_pg_dsn, tenant_id, "database")
    assert any("databases" in c for c in contents), contents


# ---------------------------------------------------------------------------
# 4. migración 0079 reversible: down a 0078 (índice 'simple' + sin columna),
#    up de nuevo (config es_unaccent + columna) sin perder schema
# ---------------------------------------------------------------------------
def test_migration_0079_reversible(alembic_config) -> None:
    from alembic import command

    command.upgrade(alembic_config, "head")
    # downgrade -1 (0079 -> 0078): vuelve al índice 'simple', sin la columna.
    command.downgrade(alembic_config, "0078_skills_category_check")
    # upgrade de nuevo aplica 0079 limpio (config + índice + columna).
    command.upgrade(alembic_config, "head")
