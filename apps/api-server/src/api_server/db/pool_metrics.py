"""Saturación del pool de conexiones, expuesta a Prometheus (prod-13 task_prod13_06).

La primera mitad de la tarea hizo el pool CONFIGURABLE (``pool_size``,
``max_overflow``, ``pool_timeout``, ``pool_recycle`` como settings). Esta es la
otra mitad, y sin ella el hallazgo db-2 no queda cerrado: un límite que nadie
puede medir no se puede tunear. El modo de fallo que describe la auditoría —«~15
chats concurrentes agotan el pool y toda la API devuelve ``TimeoutError``»— hoy
solo se manifiesta como una tormenta de 500 sin causa visible en ninguna gráfica.

Por qué un *collector* y no contadores
--------------------------------------
Un `Counter` incrementado al pedir/soltar conexión obligaría a instrumentar el
camino caliente de cada request y a mantener sincronizados dos estados (el
nuestro y el del pool). El pool de SQLAlchemy ya lleva la cuenta viva
(``checkedout()`` / ``checkedin()`` / ``overflow()``), así que este módulo la
LEE cuando Prometheus pasa a scrapear. En régimen normal cuesta cero.

Series publicadas
-----------------
``agentic_db_pool_connections{engine,state}``
    Conexiones ahora mismo, por engine (``app`` / ``admin``) y estado
    (``in_use`` / ``idle`` / ``overflow``).
``agentic_db_pool_capacity{engine}``
    Techo del engine = ``pool_size + max_overflow``. Es el DENOMINADOR del ratio
    de saturación; sin él la alerta de prod-08 es inescribible, porque «20
    conexiones en uso» no significa nada sin saber dónde está el techo.

Los dos engines, no solo el de aplicación: agotar el admin (BYPASSRLS) deja al
System Admin sin panel, que es justo cuando hace falta.

Cardinalidad: dos labels cerrados, 2 por 3 = 6 series como máximo. Ninguno depende
de datos de una request (misma disciplina que ``api_server.metrics``).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from prometheus_client import CollectorRegistry
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.metrics_core import Metric

from api_server.db.session import get_admin_engine, get_engine

_CONNECTIONS = "agentic_db_pool_connections"
_CAPACITY = "agentic_db_pool_capacity"

__all__ = ["DbPoolCollector", "install_pool_metrics"]


def _pool_of(factory: Any) -> Any | None:
    """El pool vivo del engine que devuelve ``factory``, o ``None``.

    Todo el camino es best-effort: construir un engine puede fallar (sin
    ``DATABASE_URL`` en un proceso que solo importa el módulo) y un ``/metrics``
    que revienta deja al operador sin NINGUNA métrica justo cuando algo va mal.
    Preferimos publicar menos series que ninguna.
    """
    try:
        return factory().pool
    except Exception:
        return None


def _reading(pool: Any) -> tuple[dict[str, float], float] | None:
    """``({estado: valor}, capacidad)`` de un pool, o ``None`` si no aplica.

    Un ``NullPool`` (el de las tareas Celery, task_prod13_08) no tiene ninguno de
    estos contadores a propósito: ahí cada tarea cuesta exactamente una conexión
    y no hay saturación que medir. Se omite en vez de publicar ceros que
    parecerían "pool sano".
    """
    size = getattr(pool, "size", None)
    checked_out = getattr(pool, "checkedout", None)
    if size is None or checked_out is None:
        return None
    try:
        max_overflow = float(getattr(pool, "_max_overflow", 0) or 0)
        overflow_now = float(pool.overflow())
        states = {
            "in_use": float(pool.checkedout()),
            "idle": float(pool.checkedin()),
            # `overflow()` arranca en `-pool_size` y sube: negativo significa
            # "aún no se ha tocado el desbordamiento", que para una gráfica es 0.
            "overflow": max(0.0, overflow_now),
        }
        return states, float(pool.size()) + max_overflow
    except Exception:
        return None


class DbPoolCollector:
    """Colector Prometheus que lee los pools en el momento del scrape."""

    def collect(self) -> Iterator[Metric]:
        connections = GaugeMetricFamily(
            _CONNECTIONS,
            "Conexiones del pool de SQLAlchemy ahora mismo, por engine y estado.",
            labels=["engine", "state"],
        )
        capacity = GaugeMetricFamily(
            _CAPACITY,
            "Techo de conexiones del engine (pool_size + max_overflow).",
            labels=["engine"],
        )
        # Los pares se construyen AQUÍ y no como constante de clase: así los
        # nombres se resuelven en cada scrape contra los globals del módulo, y
        # tanto un `reset_engine_cache()` como el monkeypatch de un test se ven
        # desde dentro. Se guarda la FUNCIÓN, no el engine, por lo mismo.
        for label, factory in (("app", get_engine), ("admin", get_admin_engine)):
            pool = _pool_of(factory)
            if pool is None:
                continue
            reading = _reading(pool)
            if reading is None:
                continue
            states, techo = reading
            for state, value in states.items():
                connections.add_metric([label, state], value)
            capacity.add_metric([label], techo)
        yield connections
        yield capacity


def _already_installed(registry: CollectorRegistry) -> bool:
    collectors: Iterable[Any] = getattr(registry, "_collector_to_names", {})
    return any(isinstance(collector, DbPoolCollector) for collector in collectors)


def install_pool_metrics(registry: CollectorRegistry) -> bool:
    """Registra el colector en ``registry``. ``True`` si lo añadió ahora.

    IDEMPOTENTE por el mismo motivo que ``api_server.metrics._build_collectors``:
    cualquier suite con más de un módulo de integración construye la app varias
    veces contra el registro global del proceso, y ``prometheus_client`` prohíbe
    duplicar nombres.
    """
    if _already_installed(registry):
        return False
    registry.register(DbPoolCollector())
    return True
