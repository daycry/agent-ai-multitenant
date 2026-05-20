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


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


def reset_engine_cache() -> None:
    """Drop the cached engine + sessionmaker. Used by tests after
    monkey-patching settings."""
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
