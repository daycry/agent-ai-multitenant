"""Alembic environment.

Async-aware: uses an async SQLAlchemy engine so we can keep a single
asyncpg driver across the codebase. Connection details come from the
DATABASE_URL env var (so the same migrations run unchanged in dev, CI
and prod).

Required env vars:
  DATABASE_URL    e.g. postgresql+asyncpg://migrations_user:pwd@host:5432/agentic_platform

Optional env vars:
  ALEMBIC_LOG_LEVEL    INFO|WARNING|DEBUG (default: INFO)
"""

from __future__ import annotations

import asyncio
import logging
import os
from logging.config import fileConfig

from alembic import context

# Qué objetos de la BD compara el autogenerate (particiones del ADR 0151 y la
# tabla de respaldo de la 0133 quedan fuera, con su motivo escrito allí).
from api_server.db.autogenerate_policy import make_include_object, partition_children
from api_server.db.base import Base

# Carga la capa de modelos ENTERA, recorriendo el paquete. Antes esto era un
# `from api_server.db import models`, y como `db/models.py` es el agregador de la
# fase 0 —no importa `db/domain` ni los módulos posteriores—, `Base.metadata` se
# quedaba en 34 tablas de 84. Con la metadata a medias `alembic check` no daba
# veredicto: moría con `NoReferencedTableError` sobre
# `incoming_webhook_configs.project_id` → `projects`, un traceback que se lee
# como problema de configuración local y se ignora. Ver el docstring de
# `api_server.db.model_registry` y `tests/unit/test_alembic_metadata_is_complete.py`.
from api_server.db.model_registry import import_all_models
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

#: Cross-process advisory lock so two concurrent ``alembic upgrade`` runs (app
#: replicas, the install one-shot + a manual run, …) cannot apply migrations at
#: the same time (Plan prod-01 task_12 / deploy-6). A transaction-scoped lock
#: (``pg_advisory_xact_lock``) auto-releases at COMMIT; the loser blocks, then
#: sees an up-to-date DB and no-ops. Arbitrary fixed key for the "agentic
#: migrations" namespace.
_MIGRATION_LOCK_KEY = 0x4147454E54  # "AGENT"

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logging.getLogger("alembic").setLevel(os.environ.get("ALEMBIC_LOG_LEVEL", "INFO").upper())

# Override sqlalchemy.url from the environment (the .ini ships empty).
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    raise RuntimeError(
        "DATABASE_URL must be set when running Alembic. Example:\n"
        "  DATABASE_URL=postgresql+asyncpg://migrations_user:pwd@localhost:5432/agentic_platform"
    )
config.set_main_option("sqlalchemy.url", db_url)

import_all_models()
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection.

    Used by `alembic upgrade head --sql` to generate a deployable
    migration script. RLS DDL is statically known so this works.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        # Sin conexión no hay catálogo que consultar, así que el conjunto de
        # particiones va vacío. No importa: el modo offline sólo se usa para
        # `upgrade --sql`, que emite DDL ya escrito y nunca compara nada.
        include_object=make_include_object(frozenset()),
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # El sondeo de particiones va ANTES de `configure`, y ahí está la trampa:
    # es un SELECT, y con el autobegin de SQLAlchemy 2.x un SELECT deja la
    # conexión DENTRO de una transacción. Alembic mira ese estado exactamente
    # una vez, al construir el `MigrationContext`, y si la encuentra ocupada se
    # declara invitado (`_in_external_transaction`): `begin_transaction()` pasa
    # a devolver un `nullcontext` y Alembic NO comitea nunca, porque asume que
    # quien abrió la transacción la cerrará. Aquí nadie la cerraba: el
    # `async with connectable.connect()` de abajo cierra la conexión, que hace
    # ROLLBACK, y se pierde el DDL de todas las migraciones.
    #
    # El modo de fallo era SILENCIOSO y por eso caro: `alembic upgrade head`
    # imprimía las 143 migraciones como aplicadas y salía con código 0 sobre una
    # base de datos que se quedaba con CERO tablas. Medido el 2026-08-20 sobre
    # una BD nueva; y los shards de integración se habían quedado clavados en
    # `0139_executions_steps_rollup` mientras `head` era `0143`, con la suite en
    # verde porque reutilizan un esquema migrado ANTES de que entrase el sondeo.
    #
    # El sondeo es de sólo lectura, así que devolver la conexión a «sin
    # transacción» no descarta nada, y le devuelve a Alembic la propiedad de la
    # transacción — que es quien sabe cuándo comitearla (y de paso mantiene
    # `transaction_per_migration` funcionando).
    partitions = partition_children(connection)
    connection.rollback()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=make_include_object(partitions),
    )
    with context.begin_transaction():
        # Serialize concurrent upgrades within this same transaction; released
        # automatically on COMMIT (Plan prod-01 task_12).
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _MIGRATION_LOCK_KEY})
        context.run_migrations()


async def run_async_migrations() -> None:
    """Connect with an async engine, then hand off to the sync runner
    via SQLAlchemy's run_sync."""
    section = config.get_section(config.config_ini_section, {})
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
