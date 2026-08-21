"""Córtex F4 — migración 0095 (cortex_curiosity_pursuits).

Ejercita la tabla de auditoría/idempotencia de la curiosidad autónoma (ADR 0078):
``alembic upgrade head`` crea la tabla con los 2 índices y el CHECK de ``status``;
un INSERT con ``status='bogus'`` viola el CHECK; ``downgrade -1`` la elimina
(reversible). Patrón de ``test_cortex_threads_migration.py``.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()
    yield
    reset_engine_cache()
    reset_redis_cache()
    get_settings.cache_clear()


async def _table_exists(conn: asyncpg.Connection, name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = $1)",
            name,
        )
    )


async def _index_exists(conn: asyncpg.Connection, name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public'"
            " AND indexname = $1)",
            name,
        )
    )


@pytest.mark.asyncio
async def test_pursuits_table_indexes_check_and_reversible(
    configured_app, migrations_pg_dsn: str, alembic_config
) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert await _table_exists(conn, "cortex_curiosity_pursuits")
        assert await _index_exists(conn, "ix_cortex_pursuits_owner_status")
        assert await _index_exists(conn, "ix_cortex_pursuits_owner_topic_created")

        owner_id = uuid4()
        await conn.execute(
            "TRUNCATE cortex_curiosity_pursuits, user_org_memberships, organizations,"
            " users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, $3, true)",
            owner_id,
            "owner@curiosity.test",
            "h",
        )

        # Un status válido entra; 'bogus' viola el CHECK.
        await conn.execute(
            "INSERT INTO cortex_curiosity_pursuits (id, owner_user_id, topic, status)"
            " VALUES ($1, $2, 'arquitectura hexagonal', 'selected')",
            uuid4(),
            owner_id,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO cortex_curiosity_pursuits (id, owner_user_id, topic, status)"
                " VALUES ($1, $2, 'x', 'bogus')",
                uuid4(),
                owner_id,
            )
    finally:
        await conn.close()

    # downgrade -1 → la tabla desaparece (reversible).
    await asyncio.to_thread(command.downgrade, alembic_config, "0094_cortex_identity")
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert not await _table_exists(conn, "cortex_curiosity_pursuits")
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Migración 0103: el CHECK admite 'surfaced' y el downgrade reconvierte
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_status_surfaced_reversible(
    configured_app, migrations_pg_dsn: str, alembic_config
) -> None:
    owner_id = uuid4()
    pursuit_id = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("TRUNCATE cortex_curiosity_pursuits, users RESTART IDENTITY CASCADE")
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'h')",
            owner_id,
            "owner@surfaced-mig.test",
        )
        # En head, 'surfaced' es un estado válido del ciclo de vida.
        await conn.execute(
            "INSERT INTO cortex_curiosity_pursuits (id, owner_user_id, topic, status,"
            " surfaced_at) VALUES ($1, $2, 'tema', 'surfaced', now())",
            pursuit_id,
            owner_id,
        )
    finally:
        await conn.close()

    # downgrade a 0102: la fila 'surfaced' se reconvierte a 'digested' ANTES de
    # reponer el CHECK antiguo (reversible de verdad, sin filas inválidas).
    await asyncio.to_thread(command.downgrade, alembic_config, "0102_plan_pr_url")
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        status = await conn.fetchval(
            "SELECT status FROM cortex_curiosity_pursuits WHERE id = $1", pursuit_id
        )
        assert status == "digested"
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO cortex_curiosity_pursuits (id, owner_user_id, topic, status)"
                " VALUES ($1, $2, 'x', 'surfaced')",
                uuid4(),
                owner_id,
            )
    finally:
        await conn.close()

    # Vuelta a head para no dejar la BD de la sesión a medias.
    await asyncio.to_thread(command.upgrade, alembic_config, "head")


# ---------------------------------------------------------------------------
# Migración 0123: la columna `approved` del owner-approval gate
# ---------------------------------------------------------------------------
async def _column_type(conn: asyncpg.Connection, table: str, column: str) -> str | None:
    return await conn.fetchval(
        "SELECT data_type FROM information_schema.columns WHERE table_schema = 'public'"
        " AND table_name = $1 AND column_name = $2",
        table,
        column,
    )


@pytest.mark.asyncio
async def test_approved_es_tri_estado_y_reversible(
    configured_app, migrations_pg_dsn: str, alembic_config
) -> None:
    """`approved` es un booleano NULLABLE — y los tres estados significan cosas distintas.

    Es la columna que el owner-approval gate del paso 7 del bucle necesita
    (ADR 0078): sin ella no había forma de distinguir «propuesto, esperando al
    owner» de «aprobado». Por eso NO puede ser `NOT NULL DEFAULT false`: eso
    fundiría «pendiente» con «rechazado» y el bucle no sabría si esperar o
    descartar. Los tres estados son:

      * `NULL`  → propuesto, esperando al owner (el bucle NO busca);
      * `true`  → aprobado (la siguiente pasada lo investiga);
      * `false` → rechazado (no se vuelve a intentar ese pursuit).

    El test escribe los tres y luego comprueba que el `downgrade` la retira de
    verdad (reversibilidad exigida por CLAUDE.md)."""
    owner_id = uuid4()
    pendiente, aprobado, rechazado = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert await _column_type(conn, "cortex_curiosity_pursuits", "approved") == "boolean"
        # Nullable: sin esto, `approved IS NULL` (la condición del gate) no existiría.
        assert (
            await conn.fetchval(
                "SELECT is_nullable FROM information_schema.columns WHERE table_name ="
                " 'cortex_curiosity_pursuits' AND column_name = 'approved'"
            )
            == "YES"
        )

        await conn.execute("TRUNCATE cortex_curiosity_pursuits, users RESTART IDENTITY CASCADE")
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, 'owner@approved-mig.test', 'h', true)",
            owner_id,
        )
        # Sin valor explícito ⇒ NULL (el default del gate: esperando al owner).
        await conn.execute(
            "INSERT INTO cortex_curiosity_pursuits (id, owner_user_id, topic, status)"
            " VALUES ($1, $2, 'rust', 'selected')",
            pendiente,
            owner_id,
        )
        for pid, value in ((aprobado, True), (rechazado, False)):
            await conn.execute(
                "INSERT INTO cortex_curiosity_pursuits (id, owner_user_id, topic, status,"
                " approved) VALUES ($1, $2, 'rust', 'selected', $3)",
                pid,
                owner_id,
                value,
            )

        rows = {
            r["id"]: r["approved"]
            for r in await conn.fetch(
                "SELECT id, approved FROM cortex_curiosity_pursuits WHERE owner_user_id = $1",
                owner_id,
            )
        }
        assert rows[pendiente] is None
        assert rows[aprobado] is True
        assert rows[rechazado] is False
    finally:
        await conn.close()

    # downgrade a 0122 → la columna desaparece (reversible de verdad).
    await asyncio.to_thread(command.downgrade, alembic_config, "0122_retire_run_tools")
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert await _column_type(conn, "cortex_curiosity_pursuits", "approved") is None
        # La tabla y sus filas siguen ahí: el downgrade quita la columna, no los datos.
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM cortex_curiosity_pursuits WHERE owner_user_id = $1", owner_id
            )
            == 3
        )
    finally:
        await conn.close()

    await asyncio.to_thread(command.upgrade, alembic_config, "head")


@pytest.mark.asyncio
async def test_el_modelo_orm_expone_approved(configured_app) -> None:
    """El ORM y la migración no pueden divergir: `approved` está en el modelo.

    La auditoría del 2026-07-27 encontró la columna ausente en AMBOS sitios. Un
    `add_column` sin campo en el modelo dejaría el gate igual de inalcanzable
    desde el código (el bucle y el endpoint `/approve` leen y escriben por el
    ORM), y ningún test de migración lo detectaría."""
    from api_server.db.cortex_curiosity import CortexCuriosityPursuit

    column = CortexCuriosityPursuit.__table__.columns["approved"]
    assert column.nullable is True
    assert column.type.python_type is bool
