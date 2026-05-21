"""OpenTelemetry tracing setup.

Phase 0:
  - One TracerProvider per process, exporter is the in-memory exporter
    in tests and ConsoleSpanExporter elsewhere.
  - Auto-instrumentation for FastAPI, SQLAlchemy, asyncpg, Redis, httpx.
  - W3C TraceContext propagator (default) so trace_id flows across HTTP
    boundaries via the `traceparent` header.

Phase 12 will swap the exporter to OTLP/Tempo without touching callers
— `configure_tracing()` is the single seam.
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
    """
    global _PROVIDER  # noqa: PLW0603 — process-wide singleton by design
    if _PROVIDER is not None:
        return _PROVIDER

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Process-wide instrumentations (call once). AsyncPGInstrumentor
    # ships without typed signatures; Redis and HTTPX ones are typed.
    AsyncPGInstrumentor().instrument()  # type: ignore[no-untyped-call]
    RedisInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()

    _PROVIDER = provider
    return provider


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
    plugged in. Production code never calls this."""
    global _PROVIDER, _INSTRUMENTED  # noqa: PLW0603 — singleton reset is the whole point
    _PROVIDER = None
    _INSTRUMENTED = False
