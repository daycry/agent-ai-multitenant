"""structlog configuration.

Output: JSON, one log line per event. Fields emitted by default:

  timestamp     ISO-8601, UTC, with a trailing 'Z'.
  level         info | warning | error | ...
  service       static, "api-server".
  logger        the module logger name.
  event         the human-readable message.

Per-request fields bound via `logging.context.bind_request_context`:

  request_id, user_id, tenant_id, project_id.

OpenTelemetry trace_id / span_id are added by task_00_15.
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

from api_server.logging.pii import mask_pii_processor
from api_server.telemetry.setup import add_otel_trace_context


def _add_service_name(service: str) -> Processor:
    def _processor(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
        event_dict.setdefault("service", service)
        return event_dict

    return _processor


def configure_logging(
    *,
    level: str = "INFO",
    service: str = "api-server",
) -> None:
    """Configure stdlib logging + structlog to emit JSON with PII masked.

    Safe to call multiple times — the structlog config is replaced atomically.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    # structlog's processor type union is wide enough to cover all of
    # the helpers below; the explicit annotation prevents mypy from
    # collapsing the list to `list[object]`.
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        _add_service_name(service),
        # OTEL context: stamp trace_id / span_id when a span is active
        # so log lines correlate with traces in Tempo (phase 12).
        add_otel_trace_context,
        # PII goes last among the meta-processors so any nested dicts/
        # lists bound earlier get walked too.
        mask_pii_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # El ÚLTIMO procesador de la cadena structlog es `wrap_for_formatter`, NO
    # un JSONRenderer.
    #
    # prod-08 (2026-07-31): antes la cadena terminaba en `JSONRenderer()`, que
    # devuelve un **string**. Ese string se le pasaba al logger de stdlib y el
    # `ProcessorFormatter` del handler raíz lo trataba como un registro
    # «foráneo» y lo volvía a envolver, produciendo JSON DOBLEMENTE CODIFICADO:
    #
    #   {"event": "{\"execution_id\": \"...\", \"event\": \"...\"}", "level": ...}
    #
    # En el nivel superior solo sobrevivían event/level/logger/timestamp/service
    # (y lo que aportaran los contextvars, que el foreign_pre_chain volvía a
    # añadir fuera). Todo campo de negocio pasado como kwarg —`execution_id`,
    # `tenant_id`, `task_id`, `plan_id`— quedaba sepultado dentro de una cadena,
    # invisible para `jq`, para LogQL y para cualquier agregador. Es decir: los
    # logs eran «JSON» sin ser consultables, que es el único motivo por el que
    # se emiten en JSON.
    #
    # `wrap_for_formatter` entrega el event_dict INTACTO al ProcessorFormatter,
    # que renderiza una sola vez. Un nivel de JSON, campos promocionados.
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib loggers (uvicorn, sqlalchemy, etc.) into the same
    # structlog pipeline so PII masking applies there too.
    root = logging.getLogger()
    # Replace handlers idempotently — repeated calls don't stack them.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            # Registros NATIVOS de structlog: ya traen los shared_processors
            # aplicados (corrieron en la cadena de arriba), así que aquí solo
            # se limpian las claves internas y se renderiza.
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
            # Registros FORÁNEOS (uvicorn, sqlalchemy, celery): no pasaron por
            # structlog, así que se les aplica aquí la misma cadena — incluido
            # el enmascarado PII.
            foreign_pre_chain=shared_processors,
        )
    )
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Convenience wrapper around `structlog.get_logger`."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
