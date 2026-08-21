"""prod-13 · task_prod13_06 — el pool de conexiones es configurable y se aplica.

Hallazgo db-2/perf-2: los engines del api-server se creaban con los defaults de
SQLAlchemy (`pool_size=5` + `max_overflow=10` = 15 conexiones, `pool_timeout=30`,
sin `pool_recycle`) y no había forma de tocarlos sin editar código. Con ~15 chats
concurrentes el pool se agotaba y TODA la API devolvía `TimeoutError`.

Lo que se comprueba, y por qué no basta con menos:

  * los cuatro settings existen y tienen los defaults de la decisión clave 4 —
    un test que solo mirase "existe el campo" dejaría pasar un default de 5;
  * los DOS engines (app y admin) reciben los cuatro valores, no solo el de app;
  * un valor de entorno distinto LLEGA al engine construido, leído del objeto
    `engine.pool` real, no del kwarg que le pasamos.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deja settings y engines sin cachear (los dos son `lru_cache`)."""
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    monkeypatch.setenv("API_SERVER_DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/db")
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", "postgresql+asyncpg://a:p@127.0.0.1:1/db")
    get_settings.cache_clear()
    reset_engine_cache()


def test_pool_settings_have_the_documented_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _fresh(monkeypatch)
    from api_server.config import get_settings

    settings = get_settings()
    assert settings.db_pool_size == 10
    assert settings.db_max_overflow == 20
    assert settings.db_pool_timeout == 10.0
    assert settings.db_pool_recycle == 1800


def test_pool_kwargs_carries_all_four_knobs(monkeypatch: pytest.MonkeyPatch) -> None:
    _fresh(monkeypatch)
    from api_server.db.session import pool_kwargs

    assert set(pool_kwargs()) == {
        "pool_size",
        "max_overflow",
        "pool_timeout",
        "pool_recycle",
    }


@pytest.mark.parametrize("factory_name", ["get_engine", "get_admin_engine"])
def test_both_engines_apply_the_env_configured_pool(
    monkeypatch: pytest.MonkeyPatch, factory_name: str
) -> None:
    """Se lee del pool REAL del engine, no del kwarg. Si alguien deja de pasar
    `**pool_kwargs()` a uno de los dos engines, este test lo ve."""
    _fresh(monkeypatch)
    monkeypatch.setenv("API_SERVER_DB_POOL_SIZE", "3")
    monkeypatch.setenv("API_SERVER_DB_MAX_OVERFLOW", "7")
    monkeypatch.setenv("API_SERVER_DB_POOL_TIMEOUT", "2.5")
    monkeypatch.setenv("API_SERVER_DB_POOL_RECYCLE", "600")

    import api_server.db.session as session_mod
    from api_server.config import get_settings

    get_settings.cache_clear()
    session_mod.reset_engine_cache()

    engine = getattr(session_mod, factory_name)()
    try:
        pool = engine.pool
        assert pool.size() == 3, f"pool_size no llegó al engine {factory_name}"
        assert pool._max_overflow == 7
        assert pool._timeout == 2.5
        assert pool._recycle == 600
    finally:
        session_mod.reset_engine_cache()
        get_settings.cache_clear()


def test_pool_size_rejects_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """`pool_size=0` en SQLAlchemy significa "sin límite de conexiones", que en
    un host único es una forma silenciosa de tumbar PostgreSQL. El `ge=1` lo
    rechaza al arrancar en vez de descubrirlo en producción."""
    _fresh(monkeypatch)
    monkeypatch.setenv("API_SERVER_DB_POOL_SIZE", "0")
    from api_server.config import Settings, get_settings

    get_settings.cache_clear()
    with pytest.raises(ValueError):
        Settings()
    get_settings.cache_clear()
