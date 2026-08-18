"""OpenTelemetry tracing setup — alcance REAL (ADR 0140, prod-08 Fase D).

Este docstring describía un sistema que no existe. Prometía
«auto-instrumentation for FastAPI, SQLAlchemy, asyncpg, Redis, httpx» cuando
``SQLAlchemyInstrumentor`` **nunca se ha invocado**, y anunciaba una «Phase 12»
que cambiaría el exporter a OTLP/Tempo y que no está planificada. Un operador
podía leerlo y concluir que sus queries lentas ya estaban trazadas.

Lo que este módulo hace HOY:

  - Un ``TracerProvider`` por proceso.
  - Auto-instrumentación de **FastAPI, asyncpg, Redis y httpx** (las cuatro que
    de verdad se instrumentan; SQLAlchemy NO).
  - Propagador W3C TraceContext, de modo que ``traceparent`` cruza las
    fronteras HTTP.
  - **Un único exporter: Console, y opt-in** (``API_SERVER_OTEL_CONSOLE=1``).
    En tests, el exporter en memoria. Sin esa variable **los spans se generan
    y se descartan**: no hay backend de trazas desplegado.

El ADR 0140 declara el tracing distribuido (OTLP + Tempo/Jaeger) FUERA DE
ALCANCE v1: la correlación entre servicios la cubre ``request_id``, que sí
viaja de punta a punta —incluida la frontera Celery— desde prod-08 Fase C
(``api_server.logging.celery_pipeline``) y es buscable en Loki.

``configure_tracing()`` sigue siendo la costura única si algún día se adopta
OTLP: el cambio es de exporter, no de llamantes.
"""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)
from structlog.types import EventDict, WrappedLogger

_PROVIDER: TracerProvider | None = None
_INSTRUMENTED: bool = False


def configure_tracing(
    *,
    service_name: str = "api-server",
    exporter: SpanExporter | None = None,
) -> TracerProvider:
    """Initialise the global TracerProvider once per process.

    Re-calling returns the same provider (idempotent). No exporter is
    attached by default — the caller picks the destination (e.g. main.py
    adds ConsoleSpanExporter at startup; tests attach an InMemorySpanExporter).

    **Devuelve siempre el provider ACTIVO del proceso, nunca uno propio.**
    ``trace.set_tracer_provider()`` es *set-once*: si algo ya instaló uno —otra
    librería instrumentada, ``OTEL_PYTHON_TRACER_PROVIDER``, el wrapper
    ``opentelemetry-instrument``, o simplemente un reimport en tests— la llamada
    **avisa por log y no hace nada**. La versión anterior devolvía y cacheaba de
    todos modos el provider recién construido, que en ese caso no lo usa NINGÚN
    tracer del proceso: los spans salían por el provider ajeno mientras el
    ``exporter`` recibido se colgaba del cadáver. Es decir, el destino de trazas
    que el llamante pidió quedaba mudo, en silencio. Se lee el provider efectivo
    después de intentar instalarlo y se trabaja sobre ÉSE.
    """
    global _PROVIDER, _INSTRUMENTED  # noqa: PLW0603 — process-wide singleton by design
    if _PROVIDER is not None:
        return _PROVIDER

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    # El provider que de verdad quedó instalado. Si OTEL rechazó el nuestro,
    # `active` es el ajeno; si el que hay no es del SDK (NoOp/Proxy, que no sabe
    # exportar), nos quedamos con el nuestro para no perder el exporter.
    active = trace.get_tracer_provider()
    if not isinstance(active, TracerProvider):
        active = provider

    if exporter is not None:
        active.add_span_processor(BatchSpanProcessor(exporter))

    # Process-wide instrumentations (call once). AsyncPGInstrumentor
    # ships without typed signatures; Redis and HTTPX ones are typed.
    # `_INSTRUMENTED` existía sin usarse: se declaraba y se reseteaba, pero
    # nadie lo leía, así que un `_reset_for_tests()` volvía a instrumentar y
    # OTEL respondía con «Attempting to instrument while already instrumented».
    if not _INSTRUMENTED:
        AsyncPGInstrumentor().instrument()  # type: ignore[no-untyped-call]
        RedisInstrumentor().instrument()
        HTTPXClientInstrumentor().instrument()
        _INSTRUMENTED = True

    _PROVIDER = active
    return active


def add_console_exporter() -> None:
    """Attach a ConsoleSpanExporter to the current provider.

    Phase-0 default for prod-shaped runs. Phase 12 will replace this
    with an OTLP exporter pointing at Tempo.
    """
    provider = trace.get_tracer_provider()
    if hasattr(provider, "add_span_processor"):
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))


def instrument_fastapi(app: FastAPI) -> None:
    """Attach the FastAPI instrumentation to a single app instance.

    Separate from configure_tracing because tests build a fresh app
    per test; the SQLAlchemy instrumentor goes through the engine,
    not the framework, so it lives next to that engine in
    db/session.py — we don't touch it here.
    """
    FastAPIInstrumentor.instrument_app(app)


def add_otel_trace_context(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """structlog processor that adds trace_id / span_id when a span is
    active. Plugged into the shared processor chain by `logging.setup`."""
    # trace.get_current_span() returns INVALID_SPAN (never None) when
    # nothing is active; we filter on ctx.is_valid below.
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return event_dict
    event_dict.setdefault("trace_id", format(ctx.trace_id, "032x"))
    event_dict.setdefault("span_id", format(ctx.span_id, "016x"))
    return event_dict


def _reset_for_tests() -> None:
    """Test-only: drop the cached provider so a new exporter can be
    plugged in. Production code never calls this.

    Lo que **no** hace, a propósito: desinstalar el TracerProvider global de
    OTEL ni desinstrumentar asyncpg/Redis/httpx. Ninguna de las dos cosas es
    reversible sin hurgar en la API privada de OTEL, y hacerlo a medias es peor
    que no hacerlo — los instrumentors se quedarían atados al provider viejo y
    sus spans irían a un exporter muerto durante el resto de la sesión. Por eso
    ``_INSTRUMENTED`` NO se limpia aquí: el proceso sigue instrumentado, y
    fingir lo contrario sólo produce los avisos «already instrumented».
    Tras este reset, ``configure_tracing()`` vuelve a resolver —y devolver— el
    provider activo real, que es lo que el llamante necesita.
    """
    global _PROVIDER  # noqa: PLW0603 — singleton reset is the whole point
    _PROVIDER = None
