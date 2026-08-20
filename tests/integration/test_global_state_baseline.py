"""El arnés deja las tablas platform-global en estado conocido — comprobado.

`tests/integration/conftest.py` monta dos fixtures automáticas para que el
estado de las tres tablas SIN `tenant_id` (`platform_settings`, `model_prices`,
`llm_providers`) no cruce de un fichero al siguiente dentro del mismo shard de
CI. El comentario largo de allí explica el porqué; esto comprueba el qué.

Hace falta un fichero propio porque el modo de fallo del arreglo es el peor de
todos: **una limpieza que deja de limpiar no da ningún error**. Los ficheros que
se apoyan en ella siguen verdes mientras el reparto les toque un vecino
inofensivo, y caen semanas después en un shard reordenado, lejos del cambio que
lo causó. Aquí se ejerce a mano, contra PostgreSQL y Redis de verdad.

Y el tercer test vigila el radio de la explosión: `TRUNCATE … CASCADE` sobre esas
tres tablas es seguro HOY porque la única clave foránea que apunta a ellas nace
de otra de las tres. Si alguien cuelga una tabla nueva de `llm_providers`, la
fixture pasaría a vaciarla en cada fichero sin que nadie lo hubiera decidido.
"""

from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

_TABLAS = ("platform_settings", "model_prices", "llm_providers")


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


async def _sembrar_las_tres(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_settings (key, value) VALUES ($1, $2::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            "memory.backfill_enabled",
            "false",
        )
        provider_id = uuid4()
        await conn.execute(
            "INSERT INTO llm_providers (id, kind, display_name, slug, is_active, base_url)"
            " VALUES ($1, 'ollama', 'resto del fichero anterior', $2, TRUE,"
            " 'http://ollama:11434/v1')",
            provider_id,
            f"leftover-{provider_id.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO model_prices"
            " (id, provider, model_id, modality, input_price, output_price, source,"
            "  provider_id, effective_from)"
            " VALUES (gen_random_uuid(), 'ollama', $1, 'text', 3.0, 15.0, 'manual',"
            " $2, now())",
            f"leftover-model-{provider_id.hex[:8]}",
            provider_id,
        )
    finally:
        await conn.close()


async def _cuantas_filas(dsn: str) -> dict[str, int]:
    conn = await asyncpg.connect(dsn)
    try:
        return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in _TABLAS}
    finally:
        await conn.close()


# ===========================================================================
# 1. La limpieza limpia: filas fuera, entrada de caché fuera
# ===========================================================================
@pytest.mark.asyncio
async def test_the_reset_empties_the_three_global_tables_and_the_settings_cache(
    schema_at_head, migrations_pg_dsn: str, test_redis_url: str, monkeypatch
) -> None:
    """Las dos mitades del arreglo, ejercidas contra PostgreSQL y Redis reales.

    Las dos hacen falta y por separado no bastan: truncar sin purgar deja
    servido desde la caché un valor cuya fila ya no existe (los seis rojos de
    memoria del 2026-08-19), y purgar sin truncar deja el `ollama` activo que se
    llevó por delante cuatro tests del memorizer ese mismo día.
    """
    import redis
    from api_server.db.platform_settings import (
        _CACHE_PREFIX,
        MEMORY_BACKFILL_ENABLED_KEY,
        get_memory_backfill_enabled,
    )

    from tests.integration.conftest import _purge_platform_setting_cache, _reset_global_tables

    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings

    get_settings.cache_clear()
    reset_redis_cache()

    await _sembrar_las_tres(migrations_pg_dsn)
    assert all(n > 0 for n in (await _cuantas_filas(migrations_pg_dsn)).values()), (
        "la siembra de este test no dejó filas: sin ellas, comprobar que la"
        " limpieza limpia no significaría nada"
    )

    # Ceba la caché por la vía de producción, que es como se llena en un run.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(
        migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            assert await get_memory_backfill_enabled(session) is False
    finally:
        await engine.dispose()

    cliente = redis.Redis.from_url(test_redis_url, socket_connect_timeout=3, socket_timeout=3)
    try:
        clave = f"{_CACHE_PREFIX}{MEMORY_BACKFILL_ENABLED_KEY}"
        assert cliente.get(clave) is not None, (
            "la lectura de producción no dejó nada en la caché Redis, así que este"
            " test no puede demostrar que la purga sirva de algo. Comprueba que"
            f" `API_SERVER_REDIS_URL` apunta a {test_redis_url}."
        )

        await _reset_global_tables()
        _purge_platform_setting_cache()

        assert (await _cuantas_filas(migrations_pg_dsn)) == dict.fromkeys(_TABLAS, 0)
        assert cliente.get(clave) is None, (
            "la fila se borró pero su valor sigue en la caché: el siguiente"
            " fichero del shard leería un ajuste que ya no existe"
        )
    finally:
        cliente.close()


# ===========================================================================
# 2. Este módulo se reconoce como «de base de datos» (no-vacuidad del filtro)
# ===========================================================================
def test_this_module_is_recognised_as_a_database_module(request) -> None:
    """`_module_uses_the_database` es lo que decide si la limpieza corre.

    Devuelve `False` para los ficheros de Docker puro, que no necesitan
    PostgreSQL y tienen que poder correr sin él. Si un día devolviera `False`
    para TODOS —una fixture renombrada, un cambio en `_DB_FIXTURES`—, la
    limpieza dejaría de correr en toda la suite sin un solo error. Este test lo
    ancla desde dentro: aquí tiene que decir `True`.
    """
    from tests.integration.conftest import _module_uses_the_database

    assert _module_uses_the_database(request) is True


# ===========================================================================
# 3. El radio de la explosión del TRUNCATE … CASCADE no ha crecido
# ===========================================================================
@pytest.mark.asyncio
async def test_nothing_outside_the_three_tables_hangs_off_them(
    schema_at_head, admin_pg_dsn: str
) -> None:
    """Sólo `model_prices` apunta a `llm_providers`, y las dos se truncan juntas.

    Mientras eso sea cierto, `CASCADE` no puede llevarse por delante ninguna
    tabla con `tenant_id`. Si alguien cuelga una tabla nueva de cualquiera de las
    tres, la fixture pasaría a vaciarla al empezar CADA fichero — y eso hay que
    decidirlo, no descubrirlo.
    """
    conn = await asyncpg.connect(admin_pg_dsn)
    try:
        filas = await conn.fetch(
            "SELECT conrelid::regclass::text AS hija, confrelid::regclass::text AS madre"
            "  FROM pg_constraint"
            " WHERE contype = 'f' AND confrelid::regclass::text = ANY($1::text[])",
            list(_TABLAS),
        )
    finally:
        await conn.close()

    encontradas = {(f["hija"], f["madre"]) for f in filas}
    assert ("model_prices", "llm_providers") in encontradas, (
        "el descubrimiento de claves foráneas dejó de ver la única que sí existe"
        f" (`model_prices.provider_id` -> `llm_providers`): {sorted(encontradas)}."
        " Sin esta aserción el test de abajo pasaría en vacío."
    )
    intrusas = {par for par in encontradas if par[0] not in _TABLAS}
    assert not intrusas, (
        f"tablas nuevas colgando de las platform-global: {sorted(intrusas)}. El"
        " `TRUNCATE … CASCADE` de `_reset_global_tables` las vaciaría al empezar"
        " cada fichero de integración."
    )
