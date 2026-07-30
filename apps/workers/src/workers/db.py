"""Factoría única de engines y sesiones para los workers (`task_audit14_06`).

Antes de este módulo, cada task Celery abría su propio engine a mano:

    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        ...
    finally:
        await engine.dispose()

Eran **49 llamadas** repartidas por `apps/workers/` el 2026-07-14 (35 fuera de
`maintenance/`), y el patrón tenía tres problemas que no daban error pero costaban:

1. **Pool que nace y muere con la task.** Sin `poolclass`, SQLAlchemy usa
   `QueuePool`: monta la maquinaria de 5 conexiones + 10 de overflow para una
   task que abre UNA conexión y dura segundos. `NullPool` es el pool correcto
   para conexiones cortas (decisión 4 del plan de remediación) y además elimina
   el riesgo clásico de heredar un pool a través del `fork` del prefork de
   Celery.
2. **Ningún sitio donde tocar nada.** Un timeout de conexión, un
   `statement_cache_size` para pgbouncer, un `connect_args`: había que editar 49
   sitios y acordarse de los 49.
3. **`try/finally` copiado 49 veces.** Los llamantes lo tenían bien —se
   verificó uno a uno— pero era 49 oportunidades de olvidarlo.

Sobre `pool_pre_ping`
--------------------
El plan pedía «`pool_pre_ping` donde proceda», y con `NullPool` **no procede**:
pre-ping existe para detectar conexiones rancias que llevaban tiempo en el pool, y
aquí cada checkout abre una conexión nueva. Ponerlo sería un `SELECT 1` extra por
sesión a cambio de nada. Se deja fuera a propósito, no por olvido.

Multi-tenancy
-------------
Este módulo **no cambia la semántica de tenant de nadie**. Los workers ya corrían
con el rol BYPASSRLS (`settings.database_url` del worker) y acotaban por
`tenant_id` explícito en cada query, o iterando tenant a tenant; eso sigue
exactamente igual. Lo único que cambia es quién construye el engine. Si un
llamante necesita otra URL (`restore_per_tenant` restaura contra una copia), la
pasa en `url=`.

Contrato
--------
- :func:`worker_engine` — el primitivo **obligatorio**: engine `NullPool`. Lo usan
  también los llamantes cuyo `try/except/finally` propio no se puede sustituir por
  un `async with` sin reindentar medio módulo (los sweeps de beat que envuelven
  todo en un `except Exception` best-effort).
- :func:`worker_sessionmaker` — context manager que cede el `async_sessionmaker` y
  **garantiza el dispose**. Es la forma preferida en código nuevo.
- :func:`worker_session` — azúcar para el caso de una sola sesión.

Las dos últimas aceptan `override=`: el seam de test que ya existía en
`browse_task` y `git_remote_sweep` (`sessionmaker` inyectable). Con `override` no
se abre engine y no se dispone nada, porque el sessionmaker inyectado no es
nuestro.

La guarda que impide volver a `create_async_engine` suelto vive en
`tests/unit/test_worker_engines_nullpool.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

__all__ = [
    "SupportsDatabaseUrl",
    "worker_engine",
    "worker_session",
    "worker_sessionmaker",
]


class SupportsDatabaseUrl(Protocol):
    """Lo único que la factoría necesita de `workers.config.Settings`.

    Un `Protocol` en vez de `Settings` para que los tests puedan pasar un objeto
    mínimo sin construir el Settings entero (que valida ~200 campos y rechaza las
    credenciales de dev).
    """

    @property
    def database_url(self) -> str: ...


def worker_engine(
    settings: SupportsDatabaseUrl | None = None, *, url: str | None = None
) -> AsyncEngine:
    """Engine de worker: `NullPool`, conexión corta, un solo sitio que configurarlo.

    Se llama de dos formas, y las dos existen porque hay dos llamantes reales:
    con `settings` (el caso normal, la BD de la plataforma) o con `url=` a secas
    —`restore_per_tenant`, cuyo config sólo tiene `admin_database_url` y no
    satisface el `Protocol`—. Faltando las dos, `ValueError`: un engine contra
    `None` fallaría mucho más tarde y con mucho menos contexto.

    No abre conexión —construir un engine en SQLAlchemy es perezoso—, así que no
    es `async` y no hace falta disponer uno que no se llegue a usar. Quien lo use
    sí tiene que disponerlo; si eso puede olvidarse, usa
    :func:`worker_sessionmaker`, que lo hace por ti.
    """
    resolved = url if url is not None else (None if settings is None else settings.database_url)
    if not resolved:
        raise ValueError("worker_engine necesita `settings` o `url`; no ha llegado ninguno")
    return create_async_engine(resolved, poolclass=NullPool, future=True)


@asynccontextmanager
async def worker_sessionmaker(
    settings: SupportsDatabaseUrl | None = None,
    *,
    url: str | None = None,
    override: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Cede un sessionmaker y garantiza el `dispose()` del engine al salir.

        async with worker_sessionmaker(settings) as sessionmaker:
            async with sessionmaker() as db, db.begin():
                ...

    Con `override` cede el sessionmaker inyectado tal cual: no abre engine y no
    dispone nada (lo cierra su dueño).
    """
    if override is not None:
        yield override
        return

    engine = worker_engine(settings, url=url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@asynccontextmanager
async def worker_session(
    settings: SupportsDatabaseUrl | None = None,
    *,
    url: str | None = None,
    override: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Azúcar para el caso de UNA sola sesión: leer un platform setting y salir.

    No abre transacción: el llamante decide si quiere `session.begin()`, igual que
    antes. No vale para los sweeps que abren una sesión POR TENANT — ésos quieren
    :func:`worker_sessionmaker`.
    """
    async with worker_sessionmaker(settings, url=url, override=override) as sessionmaker:
        session: Any = sessionmaker()
        async with session as opened:
            yield opened
