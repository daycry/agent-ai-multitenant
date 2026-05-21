"""OpenTelemetry tracing for api-server."""

from api_server.telemetry.setup import (
    add_console_exporter,
    add_otel_trace_context,
    configure_tracing,
    instrument_fastapi,
)

__all__ = [
    "add_console_exporter",
    "add_otel_trace_context",
    "configure_tracing",
    "instrument_fastapi",
]
