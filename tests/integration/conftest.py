"""Shared fixtures for integration tests.

The integration suite talks to a real PostgreSQL — the one in
docker/docker-compose.yml. Each test session creates a throwaway
database (`agentic_platform_test`) so tests cannot pollute the dev
database; the DB is dropped on teardown.

Env overrides (defaults match docker/.env.example):
  TEST_PG_HOST                default: localhost
  TEST_PG_PORT                default: 5432
  TEST_PG_ADMIN_USER          default: postgres
  TEST_PG_ADMIN_PASSWORD      default: changeme-dev-only
  TEST_PG_MIGRATIONS_USER     default: migrations_user
  TEST_PG_MIGRATIONS_PASSWORD default: changeme-migrations-dev-only
  TEST_PG_DB_NAME             default: agentic_platform_test
  TEST_REDIS_PASSWORD         default: se lee de docker/.env (REDIS_PASSWORD)
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest

# Redis test DB — DB 15 por defecto, para no pisar la dev (DB 0). La resolución
# (contraseña incluida) vive en `_redis_url.py`, que es la ÚNICA fuente de verdad
# del arnés: escribir la URL a mano aquí o en un test es la trampa que documenta
# `gotchas/redis-con-contrasena-rompe-la-integracion.md`.
from ._redis_url import TEST_REDIS_URL

#: `127.0.0.1` y NO `localhost`, a propósito. En Windows el resolver devuelve
#: `::1` ANTES que `127.0.0.1`, y los puertos que publica Docker Desktop sólo
#: escuchan en IPv4: cada conexión paga ~2 s esperando el rechazo del intento
#: IPv6 antes de caer al fallback. No es un error —todo acaba conectando—, así
#: que no se ve: sólo se nota en que la suite tarda horas… y en que
#: `/readyz`, cuyo deadline por check es de 2 s, da 503 con las dos
#: dependencias VIVAS. Ver
#: `docs/03-guides/gotchas/localhost-ipv6-primero-cuesta-dos-segundos.md`.
PG_HOST = os.environ.get("TEST_PG_HOST", "127.0.0.1")
# Default 15432 matches docker/docker-compose.dev.yml — avoids clashing
# with any local postgres on the host. Override TEST_PG_PORT for CI.
PG_PORT = int(os.environ.get("TEST_PG_PORT", "15432"))


PG_ADMIN_USER = os.environ.get("TEST_PG_ADMIN_USER", "postgres")
PG_ADMIN_PASSWORD = os.environ.get("TEST_PG_ADMIN_PASSWORD", "changeme-dev-only")
PG_MIG_USER = os.environ.get("TEST_PG_MIGRATIONS_USER", "migrations_user")
PG_MIG_PASSWORD = os.environ.get("TEST_PG_MIGRATIONS_PASSWORD", "changeme-migrations-dev-only")
PG_APP_USER = os.environ.get("TEST_PG_APP_USER", "app_user")
PG_APP_PASSWORD = os.environ.get("TEST_PG_APP_PASSWORD", "changeme-app-dev-only")
PG_TEST_DB = os.environ.get("TEST_PG_DB_NAME", "agentic_platform_test")


def _admin_dsn(db: str = "postgres") -> str:
    return f"postgresql://{PG_ADMIN_USER}:{PG_ADMIN_PASSWORD}@{PG_HOST}:{PG_PORT}/{db}"


async def _drop_create_db() -> None:
    conn = await asyncpg.connect(_admin_dsn(db="postgres"))
    try:
        # Disconnect anyone still on the test DB (idempotent).
        await conn.execute(f"""
            SELECT pg_terminate_backend(pid)
              FROM pg_stat_activity
             WHERE datname = '{PG_TEST_DB}' AND pid <> pg_backend_pid()
            """)
        await conn.execute(f'DROP DATABASE IF EXISTS "{PG_TEST_DB}"')
        await conn.execute(f'CREATE DATABASE "{PG_TEST_DB}" OWNER "{PG_MIG_USER}"')
    finally:
        await conn.close()

    # Enable the extensions the production init scripts add (pgvector,
    # pg_trgm, pgcrypto, uuid-ossp), grant baseline schema USAGE to
    # app_user, and set the ALTER DEFAULT PRIVILEGES that production
    # also configures (so any later CREATE TABLE BY migrations_user
    # auto-grants DML to app_user — same behaviour as prod).
    target = await asyncpg.connect(_admin_dsn(db=PG_TEST_DB))
    try:
        for ext in ("vector", "pg_trgm", "pgcrypto", "uuid-ossp"):
            await target.execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}"')
        await target.execute(f'GRANT USAGE ON SCHEMA public TO "{PG_APP_USER}"')
        await target.execute(
            f'ALTER DEFAULT PRIVILEGES FOR ROLE "{PG_MIG_USER}" IN SCHEMA public '
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{PG_APP_USER}"'
        )
        await target.execute(
            f'ALTER DEFAULT PRIVILEGES FOR ROLE "{PG_MIG_USER}" IN SCHEMA public '
            f'GRANT USAGE, SELECT ON SEQUENCES TO "{PG_APP_USER}"'
        )
    finally:
        await target.close()


#: Tablas a las que una migración RETIRA el acceso de la aplicación a propósito.
#:
#: El retro-grant de abajo es un `ON ALL TABLES` sin excepciones, así que
#: **deshacía esos revokes** y dejaba el arnés MÁS PERMISIVO QUE PRODUCCIÓN. Lo
#: destapó `test_the_backfill_table_is_unreachable_from_the_app`, que pasaba en
#: aislamiento y fallaba en lote: cualquier test anterior que llamase aquí volvía
#: a conceder lo que la migración 0138 había quitado.
#:
#: Ese patrón —guarda que solo pasa sola— es el que acaba con la guarda borrada
#: por «flaky», y la guarda tenía razón.
#:
#: Añadir una entrada exige que exista la migración que la revoca. Hoy:
#:   · `approval_policy_backfill_0133` → migración 0138 (respaldo interno de la
#:     0133; la aplicación no lo consulta y no debe poder leerlo).
#:   · `agent_tools_backfill_0145` → la propia migración 0145, que revoca en el
#:     mismo `upgrade` que crea la tabla (aprendido de la 0138: los default
#:     privileges alcanzan a toda tabla que Alembic cree, así que revocar
#:     «después» ya es tarde).
#:   · `agent_tools_backfill_0146` → la migración 0146, mismo patrón que la 0145
#:     (respaldo de los grants de `move_file` que propagó a las copias de tenant).
_APP_REVOKED_TABLES: tuple[str, ...] = (
    "approval_policy_backfill_0133",
    "agent_tools_backfill_0145",
    "agent_tools_backfill_0146",
)


async def _grant_app_user_existing_tables() -> None:
    """Retro-grant DML on tables that already exist (the default privs
    above only apply to tables created *after* they are set). Idempotent.

    Reaplica después los revokes de :data:`_APP_REVOKED_TABLES`, para que el
    arnés reproduzca los permisos de producción y no unos más laxos.
    """
    conn = await asyncpg.connect(_admin_dsn(db=PG_TEST_DB))
    try:
        await conn.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE"
            f' ON ALL TABLES IN SCHEMA public TO "{PG_APP_USER}"'
        )
        await conn.execute(
            f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{PG_APP_USER}"'
        )
        for table in _APP_REVOKED_TABLES:
            # `to_regclass` porque el retro-grant corre también sobre esquemas a
            # medio migrar, donde la tabla puede no existir todavía.
            await conn.execute(f"""
                DO $$
                BEGIN
                    IF to_regclass('public.{table}') IS NOT NULL THEN
                        EXECUTE 'REVOKE ALL ON TABLE public.{table} FROM "{PG_APP_USER}"';
                    END IF;
                END $$;
                """)
    finally:
        await conn.close()


async def _drop_db() -> None:
    conn = await asyncpg.connect(_admin_dsn(db="postgres"))
    try:
        await conn.execute(f"""
            SELECT pg_terminate_backend(pid)
              FROM pg_stat_activity
             WHERE datname = '{PG_TEST_DB}' AND pid <> pg_backend_pid()
            """)
        await conn.execute(f'DROP DATABASE IF EXISTS "{PG_TEST_DB}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    """Session-scoped: create the test DB, yield its URL, drop on teardown.

    DO NOT run this suite under pytest-xdist (``-n``). The whole integration
    suite shares this ONE session-scoped database, and some tests depend on
    execution order (e.g. ``test_migrations.py`` asserts on the shared schema
    after its upgrade->downgrade->upgrade round-trip). Parallel workers would
    race on both the shared DB and that ordering, producing flaky failures.
    Follow-up (Plan prod-02 task_12, finding tests-8): give each xdist worker
    its own throwaway database (worker-id-suffixed name) before enabling ``-n``.
    """
    asyncio.run(_drop_create_db())
    url = f"postgresql+asyncpg://{PG_MIG_USER}:{PG_MIG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"
    try:
        yield url
    finally:
        asyncio.run(_drop_db())


@pytest.fixture()
def alembic_config(test_database_url: str) -> Iterator[object]:
    """Alembic Config wired to the test DB. Use `alembic.command.*` with it."""
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    api_server_dir = repo_root / "apps" / "api-server"

    cfg = Config(str(api_server_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_server_dir / "migrations"))
    os.environ["DATABASE_URL"] = test_database_url
    try:
        yield cfg
    finally:
        os.environ.pop("DATABASE_URL", None)


@pytest.fixture()
def admin_pg_dsn() -> str:
    """Sync-style DSN for ad-hoc inspection queries by admin (BYPASSRLS)."""
    return _admin_dsn(db=PG_TEST_DB)


@pytest.fixture()
def migrations_pg_dsn() -> str:
    """DSN as migrations_user — has BYPASSRLS, used to seed test data
    bypassing RLS policies."""
    return f"postgresql://{PG_MIG_USER}:{PG_MIG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"


@pytest.fixture()
def app_database_url() -> str:
    """SQLAlchemy URL as app_user (NOBYPASSRLS). Use this for the
    FastAPI app under test so it goes through RLS like in production."""
    return f"postgresql+asyncpg://{PG_APP_USER}:{PG_APP_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"


@pytest.fixture()
def admin_database_url() -> str:
    """SQLAlchemy URL as migrations_user (BYPASSRLS). Used by /admin/*
    endpoints so System Admin can read across tenants and write
    audit_log rows with tenant_id IS NULL."""
    return f"postgresql+asyncpg://{PG_MIG_USER}:{PG_MIG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"


@pytest.fixture()
def test_redis_url() -> str:
    """Redis URL the FastAPI app under test should use."""
    return TEST_REDIS_URL


async def _flush_redis(url: str) -> None:
    """Wipe the test Redis DB. Idempotent."""
    from redis.asyncio import Redis

    client = Redis.from_url(url, decode_responses=True)
    try:
        await client.flushdb()
    finally:
        await client.aclose()


@pytest.fixture()
def configured_app(
    alembic_config: object,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[object]:
    """A real api-server app, migrated DB + flushed Redis, wired for tests.

    Shared by the public-API integration suites (``test_api_v1_endpoints``,
    ``test_api_versioning``) so they exercise the SAME wired app (every v1
    router-level dependency included). Upgrades the throwaway DB to head,
    grants the app role on the freshly-created tables, flushes the Redis
    test DB, points the api-server config at all three via env, then builds
    the app via :func:`create_app`. Engine/Redis/settings caches are reset
    on both setup and teardown so each test gets a clean, correctly-wired
    process state.
    """
    from alembic import command

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")
    monkeypatch.setenv("API_SERVER_SSO_ENCRYPTION_KEY", "test-sso-encryption-key")
    monkeypatch.setenv("API_SERVER_SSO_REDIRECT_BASE_URL", "http://testserver")
    monkeypatch.delenv("API_SERVER_VAULT_TOKEN", raising=False)

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# El estado que NO tiene tenant_id y por eso cruza de un fichero al siguiente
# ---------------------------------------------------------------------------
# Toda la suite de integración comparte UNA base de datos de sesión, y el job de
# CI reparte los ~547 ficheros entre cuatro shards por round-robin
# (`.github/workflows/ci.yml`; el reparto lo fija `tests/unit/
# test_ci_integration_shards.py`). Dentro de un shard, los ~137 ficheros corren
# en UN solo proceso y en un orden que depende de cuántos ficheros haya en el
# árbol: añadir un test en cualquier parte reordena los cuatro shards enteros.
#
# Para casi todo eso da igual, porque casi todo lleva `tenant_id` y cada fichero
# siembra su propio tenant. Las tres tablas de abajo son la excepción: son
# PLATFORM-GLOBAL, no tienen `tenant_id` y nadie las aísla. Lo que un fichero
# deja escrito ahí lo lee el siguiente del mismo shard.
#
# Ya costó dos tandas de rojos:
#
#  · 2026-08-19, cuatro rojos de `test_memory_skip_reason`: una fila `ollama`
#    ACTIVA que dejaban `test_cortex_model_settings` y
#    `test_assistant_provider_teardown` ganaba al doble de LLM que el test
#    inyectaba, porque `_select_distiller` mira el catálogo ANTES que la
#    factoría. El memorizer salía a una red que no existe.
#  · 2026-08-19, seis rojos de memoria: el valor de `platform_settings` que
#    dejaba el fichero anterior seguía SERVIDO DESDE LA CACHÉ Redis aunque su
#    fila ya no existiera.
#
# El modo de fallo de fondo es peor que cualquiera de los dos rojos: un `_seed`
# que trunca doce tablas y se deja una es INDISTINGUIBLE de uno correcto hasta
# que cambia el reparto. Por eso el estado conocido se garantiza aquí, una vez,
# y no en el recuerdo de cada fichero.
#
# Dos piezas, con dos granularidades distintas a propósito:
#
#  1. **Las filas** (`_global_tables_baseline`, scope de MÓDULO). La fuga que no
#     se ve es la que cruza ficheros: dentro de un fichero el orden es fijo, el
#     rojo es determinista y se reproduce en local. Truncar por TEST además
#     rompería a cualquier fichero que siembre un proveedor en un test y lo lea
#     en el siguiente, y costaría una conexión por test en vez de una por
#     fichero (~547 frente a varios miles).
#  2. **La caché** (`_platform_setting_cache_baseline`, scope de FUNCIÓN). Ésta
#     sí muerde entre tests del MISMO fichero: `get_platform_setting` cachea 30 s
#     y sólo `set_platform_setting` —la vía del System Admin— invalida, así que
#     un test que siembra por SQL directo lee lo que cacheó el test anterior.
#     Purgarla cuesta un DEL y es lo que hace la escritura real.
#
# Purgar la caché ANTES del test no tapa ningún defecto de producción: un test
# que escriba por la API y relea sigue ejerciendo la invalidación de verdad.
_GLOBAL_TABLES: tuple[str, ...] = ("platform_settings", "model_prices", "llm_providers")

#: Fixtures de este conftest que implican hablar con PostgreSQL. Se usan para NO
#: exigir base de datos a los ficheros que no la tocan (los de Docker puro:
#: `test_egress_proxy`, `test_container_isolation`, `test_no_docker_socket`…),
#: que hoy corren sin PostgreSQL levantado y tienen que seguir haciéndolo.
#: `tests/unit/test_integration_global_baseline.py` falla si aparece una fixture
#: de BD nueva que no esté en esta lista.
_DB_FIXTURES: frozenset[str] = frozenset(
    {
        "admin_database_url",
        "admin_pg_dsn",
        "alembic_config",
        "app_database_url",
        "configured_app",
        "migrations_pg_dsn",
        "test_database_url",
    }
)


def _module_uses_the_database(request: pytest.FixtureRequest) -> bool:
    """¿Algún test de este módulo pide una fixture de BD?

    `item.fixturenames` es el cierre COMPLETO (incluye las transitivas), así que
    un test que pida `configured_app` cuenta aunque no nombre `alembic_config`.
    """
    module = request.module
    return any(
        _DB_FIXTURES & set(item.fixturenames)
        for item in request.session.items
        if getattr(item, "module", None) is module
    )


async def _reset_global_tables() -> None:
    """Deja las tres tablas platform-global como las deja `alembic upgrade head`.

    Vacías, que es exactamente su estado tras migrar: ninguna migración y ningún
    seed de arranque escribe en ellas (lo comprueba
    `tests/unit/test_integration_global_baseline.py`). O sea que esto no borra
    semillas de nadie — sólo restos del fichero anterior.

    Tolera que las tablas no existan: el primer módulo de la sesión corre antes
    de la primera migración, y `test_migrations.py` baja el esquema a `base`.
    """
    conn = await asyncpg.connect(_admin_dsn(db=PG_TEST_DB))
    try:
        present = [
            t
            for t in _GLOBAL_TABLES
            if await conn.fetchval("SELECT to_regclass($1)", f"public.{t}") is not None
        ]
        if present:
            # Nombres de una constante de módulo, no de entrada de usuario.
            await conn.execute(f"TRUNCATE {', '.join(present)} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


#: Cliente Redis SÍNCRONO reutilizado por la purga de caché, en un hueco de
#: módulo. Reutilizarlo no es micro-optimización: medido en esta máquina, abrir
#: uno nuevo cuesta **73 ms** (TCP + AUTH sobre el loopback de Docker Desktop) y
#: el `SCAN`+`DEL` sobre el cliente ya abierto cuesta **10-30 ms**. A una purga
#: por test y ~1.100 tests por shard, la diferencia entre reutilizar y no son
#: ~80 s por shard de reloj de CI gastados en volver a abrir el mismo socket.
_PURGE_CLIENT: dict[str, Any] = {"redis": None}


def _purge_platform_setting_cache() -> None:
    """Borra las claves `psetting:*` de la Redis del arnés.

    Cliente SÍNCRONO a propósito: la caché de producción es async y su cliente
    queda atado al event loop de la primera llamada (ver el comentario largo en
    `db/platform_settings.py`), así que purgar por ahí desde una fixture pediría
    un `asyncio.run` y volvería a pisar ese binding. Aquí no hay loop.

    Se barre por PREFIJO y no por una lista de claves conocidas: la lista
    envejecería en silencio el día que alguien añada un ajuste con otro nombre, y
    una purga que se deja una clave es indistinguible de una correcta —que es
    exactamente el modo de fallo que estas fixtures vienen a cerrar—.

    Sólo se toca `TEST_REDIS_URL`. Si `API_SERVER_REDIS_URL` apunta a otro sitio
    —su default de producción es `redis://localhost:6379/0`— es que la caché no
    está hablando con la Redis del arnés, y el arnés no borra claves de un
    servidor que no es suyo.
    """
    import redis
    from api_server.db.platform_settings import _CACHE_PREFIX

    client = _PURGE_CLIENT["redis"]
    if client is None:
        client = redis.Redis.from_url(TEST_REDIS_URL, socket_connect_timeout=3, socket_timeout=3)
        _PURGE_CLIENT["redis"] = client
    try:
        # `count` alto para que el barrido quepa en un round-trip: el coste de
        # `SCAN` crece con el TAMAÑO DEL KEYSPACE, no con las claves que casan.
        keys = list(client.scan_iter(match=f"{_CACHE_PREFIX}*", count=2000))
        if keys:
            client.delete(*keys)
    except redis.RedisError:
        # La caché real degrada en silencio si Redis no contesta (`_cached_read`
        # captura y cae a la BD): sin Redis no hay caché que purgar. Se tira el
        # cliente para que el próximo test lo reconstruya en vez de arrastrar una
        # conexión rota el resto de la sesión. Cualquier otro error sí sube.
        _PURGE_CLIENT["redis"] = None
        with contextlib.suppress(Exception):
            client.close()


@pytest.fixture(scope="module", autouse=True)
def _global_tables_baseline(request: pytest.FixtureRequest) -> Iterator[bool]:
    """Las tres tablas platform-global, vacías al empezar cada FICHERO."""
    if not _module_uses_the_database(request):
        yield False
        return
    # Fuerza la creación de la BD de sesión antes de conectarse a ella.
    request.getfixturevalue("test_database_url")
    asyncio.run(_reset_global_tables())
    yield True


@pytest.fixture(autouse=True)
def _platform_setting_cache_baseline(_global_tables_baseline: bool) -> Iterator[None]:
    """La caché de `platform_settings`, vacía al empezar cada TEST."""
    if _global_tables_baseline:
        _purge_platform_setting_cache()
    yield
