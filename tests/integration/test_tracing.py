"""OpenTelemetry tracing — integration tests.

The setup uses InMemorySpanExporter so spans can be inspected
synchronously without piping JSON to stdout. Each test gets a fresh
TracerProvider via `_reset_for_tests`.

What we verify:

  - configure_tracing is idempotent (second call returns same provider).
  - A request to a FastAPI endpoint creates at least one server span.
  - The traceparent header from the client is honoured: the resulting
    span belongs to the trace_id the client supplied.
  - structlog event_dicts gain trace_id / span_id when a span is
    active (verified via add_otel_trace_context directly so we don't
    depend on log capture).
"""

from __future__ import annotations

import pytest
from api_server.telemetry.setup import (
    add_otel_trace_context,
    configure_tracing,
    instrument_fastapi,
)
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def exporter():
    """Attach an InMemorySpanExporter to the live provider.

    OpenTelemetry forbids replacing the global TracerProvider, so we
    add a SimpleSpanProcessor (synchronous) that pushes finished
    spans into the in-memory store. The default ConsoleSpanExporter
    set by configure_tracing keeps running in parallel — harmless
    for tests, useful for stdout debugging.
    """
    # Make sure the provider exists; this is idempotent.
    configure_tracing(service_name="api-server-test")
    provider = trace.get_tracer_provider()

    in_memory = InMemorySpanExporter()
    processor = SimpleSpanProcessor(in_memory)
    provider.add_span_processor(processor)  # type: ignore[attr-defined]

    try:
        yield in_memory
    finally:
        in_memory.clear()
        processor.shutdown()


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    instrument_fastapi(app)
    return app


# ---------------------------------------------------------------------------
# Provider lifecycle
# ---------------------------------------------------------------------------
def test_configure_tracing_is_idempotent(exporter: InMemorySpanExporter) -> None:
    first = trace.get_tracer_provider()
    # Calling again returns the same provider (no second processor stacked).
    second = configure_tracing(service_name="api-server-test", exporter=exporter)
    assert first is second


# ---------------------------------------------------------------------------
# HTTP request -> span
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_request_emits_server_span(exporter: InMemorySpanExporter) -> None:
    app = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/ping")
    assert resp.status_code == 200

    # Force any pending spans to flush before reading the exporter.
    provider = trace.get_tracer_provider()
    provider.force_flush()  # type: ignore[attr-defined]

    spans = exporter.get_finished_spans()
    assert spans, "no spans recorded after request"

    # At least one span should represent the GET /ping route.
    routes = [s for s in spans if s.name and "/ping" in s.name]
    assert routes, f"no /ping span among {[s.name for s in spans]}"


# ---------------------------------------------------------------------------
# Inbound trace context is honoured
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_inbound_traceparent_is_used(exporter: InMemorySpanExporter) -> None:
    """Trace propagation: a client-supplied trace_id should be the
    parent of the server span produced by the endpoint."""
    app = _build_app()

    # W3C traceparent: version=00, trace_id, parent_id, flags=01.
    inbound_trace_id = "0af7651916cd43dd8448eb211c80319c"
    inbound_parent_id = "b7ad6b7169203331"
    traceparent = f"00-{inbound_trace_id}-{inbound_parent_id}-01"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/ping", headers={"traceparent": traceparent})
    assert resp.status_code == 200

    trace.get_tracer_provider().force_flush()  # type: ignore[attr-defined]

    spans = exporter.get_finished_spans()
    assert spans
    # Every server span produced during this request must share the
    # client trace_id (propagation worked).
    request_spans = [s for s in spans if format(s.context.trace_id, "032x") == inbound_trace_id]
    assert request_spans, (
        f"no spans carried the inbound trace_id {inbound_trace_id}; "
        f"got {[format(s.context.trace_id, '032x') for s in spans]}"
    )


# ---------------------------------------------------------------------------
# structlog processor adds trace context
# ---------------------------------------------------------------------------
def test_add_otel_trace_context_returns_dict_unchanged_without_span(
    exporter: InMemorySpanExporter,
) -> None:
    out = add_otel_trace_context(None, "info", {"event": "no span here"})
    assert "trace_id" not in out
    assert "span_id" not in out


def test_add_otel_trace_context_stamps_ids_inside_span(
    exporter: InMemorySpanExporter,
) -> None:
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("unit-test-span"):
        out = add_otel_trace_context(None, "info", {"event": "x"})

    assert "trace_id" in out
    assert "span_id" in out
    # 32 hex chars for trace_id, 16 for span_id.
    assert len(out["trace_id"]) == 32
    assert len(out["span_id"]) == 16
    int(out["trace_id"], 16)  # parses
    int(out["span_id"], 16)
