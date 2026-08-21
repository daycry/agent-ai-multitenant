"""La migración 0129 (ADR 0142 D6) contra PostgreSQL real — `task_mkt2_09`.

Tres cosas, y ninguna es «la columna existe»:

1. **El backfill no rompe el catálogo vivo**: se siembra un listing ANTES de la
   0129 y, tras aplicarla, sigue siendo `published`. Es la mitad del contrato
   que justifica el `server_default = 'published'`; sin este test, el default
   asimétrico es una afirmación del docstring.
2. **El CHECK es real**: un quinto estado inventado por un script se rechaza en
   la base, no solo en el enum de Python.
3. **El downgrade BAJA de verdad**, anclado a la revisión **por nombre**
   (`_REVISION_BEFORE`) y nunca `downgrade("-1")` — con varios agentes añadiendo
   migraciones, `-1` apunta a lo que haya debajo en ese momento (la trampa que
   costó las 0125/0126). Y el round-trip vuelve a subir: una migración que solo
   funciona sobre una base virgen no es reversible.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = [pytest.mark.integration]

#: La revisión ANTERIOR a la 0129: el estado que su `downgrade` debe restaurar.
_REVISION_BEFORE = "0128_marketplace_v2_deploy"

_NEW_COLUMNS = ("review_status", "reviewed_by", "reviewed_at", "rejection_reason")


async def _columns(dsn: str) -> set[str]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'marketplace_listings'"
        )
        return {r["column_name"] for r in rows}
    finally:
        await conn.close()


async def _seed_listing_before_the_migration(dsn: str) -> UUID:
    """Un listing como los que ya viven en el catálogo: sin estado de revisión."""
    listing_id = uuid4()
    source_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_listing_versions, marketplace_listings,"
            " marketplace_sources RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type, is_trusted)"
            " VALUES ($1,'oficial-0129','official',true)",
            source_id,
        )
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level, manifest,"
            "  requested_permissions)"
            " VALUES ($1,$2,NULL,'tool','ya-publicado','1.0.0','verified',"
            "         '{}'::jsonb,'[]'::jsonb)",
            listing_id,
            source_id,
        )
    finally:
        await conn.close()
    return listing_id


async def _review_status(dsn: str, listing_id: UUID) -> str | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT review_status FROM marketplace_listings WHERE id = $1", listing_id
        )
    finally:
        await conn.close()


def test_backfill_leaves_the_live_catalog_published(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Lo que hoy se ve, tras la 0129 se sigue viendo.

    El caso contrario —default `'draft'`— vaciaría el catálogo entero en el
    despliegue, que es un apagón más ruidoso que el fallo del que protegería un
    default estricto. Esto es lo que lo demuestra.
    """
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]

    assert not (set(_NEW_COLUMNS) & asyncio.run(_columns(migrations_pg_dsn))), (
        "el downgrade dejó columnas suyas atrás: no baja de verdad"
    )
    listing_id = asyncio.run(_seed_listing_before_the_migration(migrations_pg_dsn))

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    assert asyncio.run(_review_status(migrations_pg_dsn, listing_id)) == "published"


def test_round_trip_restores_the_schema_both_ways(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    after_up = asyncio.run(_columns(migrations_pg_dsn))
    assert set(_NEW_COLUMNS) <= after_up

    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    after_down = asyncio.run(_columns(migrations_pg_dsn))
    assert not (set(_NEW_COLUMNS) & after_down)

    # Y vuelve a subir sobre la base ya usada, no sobre una virgen.
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert set(_NEW_COLUMNS) <= asyncio.run(_columns(migrations_pg_dsn))


def test_check_constraint_rejects_an_invented_state(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """El vocabulario es cerrado EN LA BASE, no solo en el enum de Python."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    listing_id = asyncio.run(_seed_listing_before_the_migration(migrations_pg_dsn))

    async def attempt() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            with pytest.raises(asyncpg.exceptions.CheckViolationError):
                await conn.execute(
                    "UPDATE marketplace_listings SET review_status = 'aprobado-ya' WHERE id = $1",
                    listing_id,
                )
        finally:
            await conn.close()

    asyncio.run(attempt())


def test_the_review_queue_index_exists(alembic_config: object, migrations_pg_dsn: str) -> None:
    """Sin el índice parcial, la cola escanea el catálogo entero en cada carga."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def check() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            definition = await conn.fetchval(
                "SELECT indexdef FROM pg_indexes"
                " WHERE tablename = 'marketplace_listings'"
                "   AND indexname = 'ix_marketplace_listings_review_queue'"
            )
            assert definition is not None, "el índice de la cola de revisión no existe"
            assert "review_status" in definition
            # Parcial: si deja de serlo, cubre el catálogo entero y deja de ser
            # el índice barato que justificaba crearlo.
            assert "WHERE" in definition.upper()
        finally:
            await conn.close()

    asyncio.run(check())
