"""Exporter Prometheus del api-server (prod-08 Fase B — hallazgo observability-2).

Hasta 2026-07-31 Prometheus solo scrapeaba **infraestructura**: node-exporter y
cadvisor. Ningún servicio de aplicación exponía ``/metrics``, con dos
consecuencias que el plan prod-08 califica de `high`:

1. **No existía la serie ``up`` para las apps.** ``up`` la sintetiza Prometheus
   por cada target scrapeado; sin target, no hay serie, y sin serie la regla
   ``ServiceDown`` (``up == 0``) es literalmente inescribible. Un api-server
   caído no disparaba absolutamente nada: el operador se enteraba porque la UI
   dejaba de cargar.
2. **La latencia y los 5xx del API eran invisibles.** No había forma de
   distinguir «va lento» de «va mal» ni de ponerle un número.

Qué se expone y qué NO
----------------------
Se expone lo que de verdad ocurre DENTRO de este proceso: peticiones HTTP
(contador por estado + histograma de latencia), más las métricas de proceso y
GC que ``prometheus_client`` aporta de serie (RSS, fds abiertos, pausas de GC).

**No** se declaran contadores de ejecuciones, tokens ni coste LLM, aunque el
plan los listara. Esas cosas pasan en los **workers**, no aquí: un contador
in-process del api-server valdría siempre 0 y sería exactamente la
«configuración muerta» que el criterio de cierre 4 del plan prohíbe. Esa
familia ya la publica el sampler por textfile-collector
(``workers/queue_metrics.py`` → ``agentic_executions_24h``,
``agentic_celery_queue_depth``, ``agentic_dlq_depth``), que consulta la BD y
por tanto ve el sistema entero en vez de un proceso.

Cardinalidad
------------
Riesgo #2 del plan, y el que de verdad puede tumbar una TSDB en máquina única.
Dos guardas:

* Se etiqueta por **plantilla de ruta** (``/items/{item_id}``), nunca por path
  crudo. Sin esto, cada 404 de un escáner (``/wp-admin``, ``/.env``, …) sería
  una serie temporal nueva y permanente.
* Catálogo de labels **cerrado** (:data:`ALLOWED_LABELS`) con lista negra
  explícita (:data:`FORBIDDEN_LABELS`). ``execution_id``/``task_id``/``user_id``
  son trabajo de logs, no de métricas; hay un test que lo hace cumplir.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from time import perf_counter
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, CollectorRegistry, Counter, Histogram
from prometheus_client import generate_latest as _generate_latest
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Valor centinela para todo lo que no casó con una ruta declarada. Colapsar
# aquí es lo que acota la cardinalidad frente a tráfico de bots.
UNMATCHED_ROUTE = "__unmatched__"

# El path del propio scrape. Contarlo inflaría el ratio de peticiones con el
# ruido del propio Prometheus (una cada 15s, 5.760 al día).
_METRICS_PATH = "/metrics"

# Catálogo CERRADO de labels (decisión clave del plan). Ampliarlo es una
# decisión consciente que pasa por code review, no un descuido.
ALLOWED_LABELS: tuple[str, ...] = (
    "tenant_id",
    "queue",
    "status",
    "provider",
    "method",
    "route",
)

# Labels de cardinalidad ILIMITADA: un valor nuevo por cada entidad del
# sistema. Prohibidos por diseño.
FORBIDDEN_LABELS: tuple[str, ...] = (
    "execution_id",
    "task_id",
    "user_id",
    "plan_id",
    "request_id",
    "path",
)

# Buckets en segundos. Cubren desde un healthcheck (~1ms) hasta un endpoint
# que habla con un LLM (~10s); los de más de 10s caen en +Inf, que para una
# API HTTP ya es «roto», no «lento».
_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

_REQUESTS_NAME = "agentic_http_requests_total"
_DURATION_NAME = "agentic_http_request_duration_seconds"


def _registered(registry: CollectorRegistry, name: str) -> Any | None:
    """El colector ya registrado con ese nombre, si lo hay.

    ``_names_to_collectors`` es API privada de ``prometheus_client``, pero es
    la única forma de preguntar «¿esto ya está registrado?» sin provocar la
    excepción. Se accede defensivamente: si un día desaparece, se cae en el
    camino de registrar y la excepción vuelve a ser visible, en vez de fallar
    en silencio.
    """
    mapping = getattr(registry, "_names_to_collectors", None)
    if not isinstance(mapping, dict):
        return None
    return mapping.get(name)


def _build_collectors(registry: CollectorRegistry) -> tuple[Counter, Histogram]:
    """Declara las métricas contra ``registry``, de forma IDEMPOTENTE.

    Se declaran por registro (y no como singletons de módulo) para que cada
    test tenga los suyos: unos contadores globales harían que el estado de un
    test se filtrara al siguiente.

    Pero ``prometheus_client`` prohíbe registrar dos veces el mismo nombre en
    un registro, y el registro del proceso es global: construir la app dos
    veces —cosa que hace cualquier suite con más de un módulo de integración—
    reventaba con ``DuplicateTimeseries`` y tumbaba el proceso entero. Así que
    si ya están registrados, se REUTILIZAN. Devolver colectores nuevos sin
    registrar sería peor: contarían en el vacío y ``/metrics`` no los vería.
    """
    existing_requests = _registered(registry, _REQUESTS_NAME)
    existing_duration = _registered(registry, _DURATION_NAME)
    if existing_requests is not None and existing_duration is not None:
        return existing_requests, existing_duration

    requests = Counter(
        _REQUESTS_NAME,
        "Peticiones HTTP atendidas, por método, plantilla de ruta y código.",
        ["method", "route", "status"],
        registry=registry,
    )
    duration = Histogram(
        _DURATION_NAME,
        "Latencia de las peticiones HTTP en segundos.",
        ["method", "route"],
        buckets=_LATENCY_BUCKETS,
        registry=registry,
    )
    return requests, duration


def iter_declared_labels() -> Iterator[str]:
    """Todos los labels que este módulo declara. Lo consume la guarda de
    cardinalidad del test suite."""
    probe = CollectorRegistry()
    requests, duration = _build_collectors(probe)
    yield from requests._labelnames
    yield from duration._labelnames


def _route_label(scope: Scope) -> str:
    """La PLANTILLA de la ruta que atendió la petición, o el centinela.

    Starlette deja el objeto ``route`` en el scope cuando el router casa la
    petición. Como el scope es un dict compartido por referencia, está
    disponible AL VOLVER de la llamada a la app aguas abajo — de ahí que se lea
    después del ``await``.
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return UNMATCHED_ROUTE


class PrometheusMiddleware:
    """Middleware ASGI puro que cuenta peticiones y mide latencia.

    ASGI puro y no ``BaseHTTPMiddleware`` por el mismo motivo que
    :class:`~api_server.logging.context.RequestContextMiddleware`: no introduce
    una tarea intermedia, así que no rompe streaming ni WebSockets y ve el
    ``scope`` que el router enriquece.
    """

    def __init__(self, app: ASGIApp, registry: CollectorRegistry | None = None) -> None:
        self.app = app
        self.registry = registry if registry is not None else get_default_registry()
        self._requests, self._duration = _build_collectors(self.registry)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "").startswith(_METRICS_PATH):
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET"))
        status_holder: dict[str, int] = {"status": 500}
        started = perf_counter()

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            # En `finally` para que una excepción no manejada aguas abajo se
            # contabilice igualmente como 500 en vez de desaparecer de las
            # métricas — que es justo el caso que interesa ver.
            elapsed = perf_counter() - started
            route = _route_label(scope)
            self._requests.labels(
                method=method, route=route, status=str(status_holder["status"])
            ).inc()
            self._duration.labels(method=method, route=route).observe(elapsed)


def build_metrics_endpoint(
    registry: CollectorRegistry | None = None,
) -> Callable[[Request], Awaitable[Response]]:
    """Endpoint que sirve el formato de exposición de Prometheus.

    Se registra como RUTA, no con ``app.mount``. ``mount("/metrics", ...)``
    parece equivalente pero hace que Starlette responda **307** a ``/metrics``
    (redirige a ``/metrics/``): el scrape acabaría siguiendo un redirect en cada
    pasada, y cualquier cliente que no los siga vería el target caído.

    Se escribe a mano en vez de usar ``prometheus_client.make_asgi_app`` para
    poder fijar el registro concreto (cada test usa el suyo, así el estado no
    se filtra entre tests) sin depender de detalles internos de la librería.
    """
    target = registry if registry is not None else get_default_registry()

    async def _endpoint(_request: Request) -> Response:
        return Response(_generate_latest(target), media_type=CONTENT_TYPE_LATEST)

    return _endpoint


def get_default_registry() -> CollectorRegistry:
    """El registro del proceso.

    Es el ``REGISTRY`` global de ``prometheus_client``, que ya trae de serie
    los colectores de proceso (RSS, descriptores abiertos, CPU) y de GC:
    métricas útiles que salen gratis con el mismo scrape.
    """
    return REGISTRY


def install_metrics(app: Any) -> None:
    """Enchufa el middleware y monta ``/metrics`` en una app FastAPI.

    Sin auth: el endpoint solo es alcanzable desde ``agentic-net`` (no se
    publica puerto al host) y no expone datos de tenant — solo agregados de
    proceso. No colisiona con el ``/inbox/metrics`` autenticado (otro path).
    """
    app.add_middleware(PrometheusMiddleware)
    app.add_route(_METRICS_PATH, build_metrics_endpoint(), methods=["GET"], include_in_schema=False)


__all__ = [
    "ALLOWED_LABELS",
    "FORBIDDEN_LABELS",
    "UNMATCHED_ROUTE",
    "PrometheusMiddleware",
    "build_metrics_endpoint",
    "get_default_registry",
    "install_metrics",
    "iter_declared_labels",
]
