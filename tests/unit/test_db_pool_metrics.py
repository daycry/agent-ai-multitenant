"""prod-13 · task_prod13_06 (2ª mitad) — la saturación del pool es OBSERVABLE.

Los cuatro settings del pool llegaron con la 1ª mitad de la tarea, pero
configurar un límite que nadie puede medir no cierra el hallazgo db-2: el modo de
fallo que describe la auditoría es «~15 chats concurrentes agotan el pool y TODA
la API devuelve `TimeoutError`», y sin métrica el operador solo se entera por los
500. Peor: no hay forma de saber si `pool_size=10` es holgado o justo, así que el
tuning se queda en adivinar.

Se mide en el SCRAPE, no en el checkout. Un contador incrementado al pedir una
conexión obligaría a instrumentar el camino caliente de cada request; el pool de
SQLAlchemy ya lleva la cuenta viva (`checkedout()`, `checkedin()`, `overflow()`),
así que basta con preguntársela cuando Prometheus pasa. Coste en régimen normal:
cero.

Lo que se comprueba, y por qué no basta con menos:

  * el colector publica las series de los DOS engines (app y admin) — la mitad
    admin es la que atiende al System Admin y agotarla también tumba su panel;
  * el valor sale del pool REAL, no de los settings: un test que solo mirase
    «existe la serie» pasaría con un colector que reporta ceros para siempre;
  * `capacity` = `pool_size + max_overflow`, que es el denominador del ratio de
    saturación — sin él la alerta de prod-08 no es escribible (¿20 conexiones en
    uso es mucho? depende del techo);
  * registrar dos veces no revienta: la app se construye más de una vez en
    cualquier suite de integración, y `prometheus_client` prohíbe duplicar
    nombres en un registro.
"""

from __future__ import annotations

from typing import Any

import pytest
from prometheus_client import CollectorRegistry

pytestmark = pytest.mark.unit


def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    monkeypatch.setenv("API_SERVER_DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/db")
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", "postgresql+asyncpg://a:p@127.0.0.1:1/db")
    get_settings.cache_clear()
    reset_engine_cache()


def _samples(registry: CollectorRegistry) -> dict[tuple[str, str], float]:
    """`{(nombre_serie, engine): valor}` de todo lo que publique el registro."""
    out: dict[tuple[str, str], float] = {}
    for metric in registry.collect():
        for sample in metric.samples:
            engine = sample.labels.get("engine", "")
            state = sample.labels.get("state", "")
            key = (sample.name, f"{engine}{'/' + state if state else ''}")
            out[key] = sample.value
    return out


def test_the_collector_publishes_both_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    _fresh(monkeypatch)
    from api_server.db.pool_metrics import DbPoolCollector

    registry = CollectorRegistry()
    registry.register(DbPoolCollector())

    samples = _samples(registry)
    engines = {key[1].split("/")[0] for key in samples}
    assert {"app", "admin"} <= engines, f"faltan engines en las series: {sorted(engines)}"


def test_capacity_is_pool_size_plus_max_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """El denominador del ratio de saturación. Se lee con valores NO-default
    para que un colector que devolviera constantes no cuele."""
    _fresh(monkeypatch)
    monkeypatch.setenv("API_SERVER_DB_POOL_SIZE", "3")
    monkeypatch.setenv("API_SERVER_DB_MAX_OVERFLOW", "7")

    import api_server.db.session as session_mod
    from api_server.config import get_settings
    from api_server.db.pool_metrics import DbPoolCollector

    get_settings.cache_clear()
    session_mod.reset_engine_cache()
    try:
        registry = CollectorRegistry()
        registry.register(DbPoolCollector())
        samples = _samples(registry)
        assert samples[("agentic_db_pool_capacity", "app")] == 10.0
        assert samples[("agentic_db_pool_capacity", "admin")] == 10.0
    finally:
        session_mod.reset_engine_cache()
        get_settings.cache_clear()


def test_in_use_comes_from_the_real_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """El valor lo da el pool vivo. Se simula una conexión en uso moviendo el
    contador del pool real: si el colector devolviera un 0 fijo, este test lo ve."""
    _fresh(monkeypatch)
    import api_server.db.session as session_mod
    from api_server.db.pool_metrics import DbPoolCollector

    engine = session_mod.get_engine()
    pool = engine.pool
    try:
        registry = CollectorRegistry()
        registry.register(DbPoolCollector())
        assert _samples(registry)[("agentic_db_pool_connections", "app/in_use")] == 0.0

        # `checkedout()` deriva de este contador interno; moverlo es la forma
        # barata de tener "una conexión en uso" sin un PostgreSQL delante.
        pool._inc_overflow()  # type: ignore[attr-defined]
        assert _samples(registry)[("agentic_db_pool_connections", "app/in_use")] == 1.0
    finally:
        session_mod.reset_engine_cache()


def test_a_broken_engine_does_not_break_the_scrape(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/metrics` no puede caerse porque un engine no se pueda construir: un
    scrape roto deja al operador sin NINGUNA métrica, justo cuando algo va mal."""
    _fresh(monkeypatch)
    import api_server.db.pool_metrics as mod

    def _boom() -> Any:
        raise RuntimeError("sin DATABASE_URL")

    monkeypatch.setattr(mod, "get_engine", _boom)

    registry = CollectorRegistry()
    registry.register(mod.DbPoolCollector())
    samples = _samples(registry)
    engines = {key[1].split("/")[0] for key in samples}
    assert "app" not in engines, "un engine roto no debe publicar series inventadas"
    assert "admin" in engines, "el engine sano debe seguir publicándose"


def test_install_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Construir la app dos veces (cualquier suite de integración lo hace) no
    puede reventar con `Duplicated timeseries`."""
    _fresh(monkeypatch)
    from api_server.db.pool_metrics import install_pool_metrics

    registry = CollectorRegistry()
    assert install_pool_metrics(registry) is True
    assert install_pool_metrics(registry) is False
    names = {type(collector).__name__ for collector in registry._collector_to_names}
    assert list(names).count("DbPoolCollector") == 1


def test_opening_sessions_registers_the_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    """El cableado, que es donde muere este tipo de trabajo.

    Un colector perfecto que nadie registra publica exactamente nada — el patrón
    «mecanismo entregado, cero llamantes» del §5 de
    `verificar-antes-de-implementar.md`. Se comprueba sobre el registro GLOBAL,
    que es el que sirve `/metrics`.
    """
    _fresh(monkeypatch)
    import api_server.db.session as session_mod
    from api_server.db.pool_metrics import DbPoolCollector
    from prometheus_client import REGISTRY

    session_mod.get_sessionmaker.cache_clear()
    session_mod.get_sessionmaker()
    try:
        assert any(
            isinstance(collector, DbPoolCollector) for collector in REGISTRY._collector_to_names
        ), "abrir sesiones no registró el colector: /metrics no publicaría el pool"
    finally:
        session_mod.reset_engine_cache()
