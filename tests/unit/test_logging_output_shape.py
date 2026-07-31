"""La FORMA de la línea de log que emite ``configure_logging`` (prod-08 Fase C).

Nadie había afirmado nunca sobre la salida real de ``configure_logging`` — solo
sobre ``mask_pii_in_text`` por separado (``test_logging_pii.py``). Por eso
sobrevivió un defecto que anula media Fase C del plan: **la línea salía con el
JSON codificado DOS veces**.

    {"event": "{\\"request_id\\": \\"abc\\", \\"execution_id\\": ..., \\"event\\": \\"...\\"}",
     "level": "info", "logger": "...", "timestamp": "...", "service": "api-server"}

structlog renderizaba el event_dict a un **string** JSON y se lo pasaba al
logger de stdlib; el ``ProcessorFormatter`` del handler raíz lo trataba como un
registro «foráneo» y lo volvía a envolver. Consecuencia práctica: en el nivel
superior solo quedan ``event``/``level``/``logger``/``timestamp``/``service``,
y TODO lo que sirve para investigar —``request_id``, ``execution_id``,
``tenant_id``, ``task_id``— queda sepultado dentro de una cadena.

Eso rompe justo el caso de uso que motiva los logs JSON y el ADR-Loki:
«buscar por ``execution_id``/``tenant_id``». Con doble codificación, un
``{execution_id="..."}`` en LogQL o un ``jq '.request_id'`` no encuentran nada.

Estos tests fijan el contrato: **un solo nivel de JSON, campos de negocio
promocionados al nivel superior, y PII enmascarada en ambos caminos** (structlog
nativo y stdlib puenteado — uvicorn, sqlalchemy…).
"""

from __future__ import annotations

import json
import logging

import pytest
import structlog

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_context():
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def _last_json_line(capsys) -> dict:
    out = capsys.readouterr().out.strip()
    assert out, "configure_logging no emitió NADA por stdout"
    return json.loads(out.splitlines()[-1])


def test_event_field_is_the_message_not_a_nested_json_blob(capsys) -> None:
    """``event`` debe ser el mensaje humano, no un JSON serializado."""
    from api_server.logging.setup import configure_logging

    configure_logging(service="api-server")
    structlog.get_logger("test.shape").info("execution_started")

    payload = _last_json_line(capsys)
    assert (
        payload["event"] == "execution_started"
    ), "el campo `event` no es el mensaje: la línea viene doblemente codificada"


def test_business_fields_are_top_level_and_queryable(capsys) -> None:
    """``execution_id``/``tenant_id`` en el nivel superior — o Loki no los ve."""
    from api_server.logging.setup import configure_logging

    configure_logging(service="workers")
    structlog.get_logger("test.shape").info(
        "execution_finished", execution_id="exec-1", tenant_id="tenant-9"
    )

    payload = _last_json_line(capsys)
    assert payload["execution_id"] == "exec-1"
    assert payload["tenant_id"] == "tenant-9"
    assert payload["service"] == "workers"
    assert payload["level"] == "info"


def test_contextvars_are_top_level_too(capsys) -> None:
    """El ``request_id`` bindeado por el middleware/`task_prerun` es buscable."""
    from api_server.logging.setup import configure_logging

    configure_logging(service="api-server")
    structlog.contextvars.bind_contextvars(request_id="req-42")
    structlog.get_logger("test.shape").info("handled")

    payload = _last_json_line(capsys)
    assert payload["request_id"] == "req-42"


def test_pii_is_masked_in_structlog_records(capsys) -> None:
    from api_server.logging.setup import configure_logging

    configure_logging(service="api-server")
    structlog.get_logger("test.shape").info("login", actor="alice@example.com")

    payload = _last_json_line(capsys)
    assert payload["actor"] == "a***@example.com"


def test_pii_is_masked_in_bridged_stdlib_records(capsys) -> None:
    """uvicorn / sqlalchemy loguean por stdlib: también deben ir enmascarados.

    Este es el camino «foráneo» del ProcessorFormatter — el que NO pasa por
    structlog. Si el enmascarado solo cubriera el camino nativo, una traza de
    SQLAlchemy con un email en un parámetro saldría en claro.
    """
    from api_server.logging.setup import configure_logging

    configure_logging(service="api-server")
    logging.getLogger("uvicorn.access").warning("contacto bob@example.com desde Bearer abc123")

    payload = _last_json_line(capsys)
    line = json.dumps(payload)
    assert "bob@example.com" not in line
    assert "b***@example.com" in line
    assert "abc123" not in line, "el token Bearer del log stdlib salió en claro"
    assert payload["service"] == "api-server"
