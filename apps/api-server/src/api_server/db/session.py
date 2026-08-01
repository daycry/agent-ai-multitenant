"""Async SQLAlchemy engine and session factory.

The engine is created lazily so tests can override DATABASE_URL via
env vars before the first session is requested.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api_server.config import get_settings


def pool_kwargs() -> dict[str, object]:
    """Los cuatro parámetros de pool, leídos de settings (prod-13 task_prod13_06).

    Antes los engines se creaban con los defaults de SQLAlchemy — `pool_size=5`,
    `max_overflow=10`, `pool_timeout=30`, sin `pool_recycle` — y ninguno era
    visible ni ajustable sin tocar código. Con la transacción por request
    retenida durante el turno LLM, esas 15 conexiones se agotaban con ~15 chats
    concurrentes y toda la API empezaba a devolver `TimeoutError` (db-2/perf-2).

    Se expone como función y no como constante para que un cambio de env var se
    recoja al reconstruir el engine (los tests lo hacen con
    `reset_engine_cache()`), no al importar el módulo.
    """
    settings = get_settings()
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": settings.db_pool_recycle,
    }


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Engine for normal application traffic (NOBYPASSRLS role)."""
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
        **pool_kwargs(),
    )


def _install_pool_metrics_once() -> None:
    """Registra el colector de saturación del pool en el registro del proceso.

    Vive aquí y no en ``main.install_metrics`` para que la métrica exista en
    CUALQUIER proceso que abra sesiones (api-server, CLI, seeds), no solo en el
    que monta la app FastAPI. Es idempotente y se llama desde los sessionmakers,
    que son ``lru_cache``: en la práctica corre una vez por proceso.

    Nunca levanta: quedarse sin métrica es un incordio; que un import de
    ``prometheus_client`` tumbe la creación de sesiones, no.
    """
    try:
        from prometheus_client import REGISTRY

        from api_server.db.pool_metrics import install_pool_metrics

        install_pool_metrics(REGISTRY)
    except Exception:  # pragma: no cover - observabilidad best-effort
        pass


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    maker = async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )
    _install_pool_metrics_once()
    return maker


@lru_cache(maxsize=1)
def get_admin_engine() -> AsyncEngine:
    """Engine for System Admin endpoints (BYPASSRLS role)."""
    settings = get_settings()
    return create_async_engine(
        settings.admin_database_url,
        pool_pre_ping=True,
        future=True,
        **pool_kwargs(),
    )


@lru_cache(maxsize=1)
def get_admin_sessionmaker() -> async_sessionmaker[AsyncSession]:
    maker = async_sessionmaker(
        bind=get_admin_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )
    _install_pool_metrics_once()
    return maker


def reset_engine_cache() -> None:
    """Drop the cached engines + sessionmakers. Used by tests after
    monkey-patching settings."""
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    get_admin_engine.cache_clear()
    get_admin_sessionmaker.cache_clear()
