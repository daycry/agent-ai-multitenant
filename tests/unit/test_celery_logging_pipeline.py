"""prod-08 Fase C — pipeline de logs JSON + PII en los servicios Celery.

Hallazgo `observability-3` de la auditoría 2026-06: workers y
notification-dispatcher **nunca invocaban** ``configure_logging()``. Sus logs
salían con el formato por defecto de Celery — texto plano, sin campo
``service`` y, lo que de verdad importa, **sin el enmascarado PII** que el
api-server sí aplica. Un email o un JWT logueado desde un task aterrizaba en
claro en ``docker logs``.

Y el hallazgo `observability-7`: la traza moría en la frontera Celery. El
``request_id`` de la petición HTTP que encoló el trabajo no viajaba con el
mensaje, así que los logs del worker eran incorrelacionables con los del
api-server.

Estos tests cubren las dos mitades:

  * **Consumidor** — ``setup_logging`` configura structlog (JSON + ``service``
    + PII), ``task_prerun`` bindea el ``request_id`` que viajó en las cabeceras
    y ``task_postrun`` limpia el contexto (nunca se filtra al siguiente task
    del mismo proceso).
  * **Productor** — ``before_task_publish`` inyecta el ``request_id`` del
    contextvar en las cabeceras del mensaje, de modo que TODO productor lo
    propaga sin tocar sus ``apply_async``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import structlog

pytestmark = pytest.mark.unit


@pytest.fixture()
def pipeline():
    """Importa el módulo y garantiza que los signals quedan desconectados.

    Los signals de Celery son GLOBALES al proceso: sin este teardown un test
    dejaría handlers vivos que contaminarían al resto de la suite.
    """
    from api_server.logging import celery_pipeline

    celery_pipeline.uninstall_celery_logging()
    structlog.contextvars.clear_contextvars()
    yield celery_pipeline
    celery_pipeline.uninstall_celery_logging()
    structlog.contextvars.clear_contextvars()


def test_setup_logging_signal_is_connected_and_configures_json(pipeline, capsys) -> None:
    """La señal ``setup_logging`` de Celery debe acabar en configure_logging.

    Celery, si nadie atiende esa señal, IMPONE su propio formato de logging y
    se lleva por delante la configuración de structlog. Por eso el cableado es
    por señal y no una llamada suelta en el arranque.
    """
    from celery.signals import setup_logging

    pipeline.install_celery_logging(service="workers")

    receivers = [r for _, r in setup_logging.receivers]
    assert receivers, "install_celery_logging no conectó la señal setup_logging"

    # Disparar la señal como hace Celery al arrancar el worker.
    setup_logging.send(sender=None, loglevel="INFO", logfile=None, format=None, colorize=None)

    structlog.get_logger("workers.test_setup").info("boot")
    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["service"] == "workers"
    assert payload["event"] == "boot"


def test_worker_log_line_is_json_with_service_and_masked_pii(pipeline, capsys) -> None:
    """El test que de verdad justifica el plan: PII enmascarada en el worker.

    Un JWT y un email emitidos desde un task NO pueden salir en claro.
    """
    pipeline.install_celery_logging(service="workers")
    pipeline.configure_now()

    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJlX2Zha2U"
    structlog.get_logger("workers.test_pii").info(
        "task.finished", actor="alice@example.com", token=jwt
    )

    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)

    assert payload["service"] == "workers"
    assert "alice@example.com" not in line, "el email salió en claro en el log del worker"
    assert jwt not in line, "el JWT salió en claro en el log del worker"
    assert payload["actor"] == "a***@example.com"
    assert payload["token"] == "***REDACTED***"


def test_before_task_publish_injects_request_id_into_headers(pipeline) -> None:
    """Productor: el request_id del contextvar viaja en las cabeceras."""
    pipeline.install_celery_logging(service="api-server")
    structlog.contextvars.bind_contextvars(request_id="req-abc-123")

    headers: dict[str, Any] = {"id": "task-1", "task": "workers.run_execution"}
    pipeline.on_before_task_publish(headers=headers)

    assert headers[pipeline.CELERY_REQUEST_ID_HEADER] == "req-abc-123"


def test_before_task_publish_without_request_id_adds_nothing(pipeline) -> None:
    """Sin request_id en contexto (beat, CLI) no se inventa una cabecera vacía."""
    pipeline.install_celery_logging(service="api-server")

    headers: dict[str, Any] = {"id": "task-1"}
    pipeline.on_before_task_publish(headers=headers)

    assert pipeline.CELERY_REQUEST_ID_HEADER not in headers


def test_task_prerun_binds_request_id_from_the_message(pipeline, capsys) -> None:
    """Consumidor: el request_id de la cabecera aparece en CADA log del task."""
    pipeline.install_celery_logging(service="workers")
    pipeline.configure_now()

    class _FakeRequest:
        def __init__(self) -> None:
            self.request_id = "req-abc-123"

        def get(self, key: str, default: Any = None) -> Any:
            return getattr(self, key, default)

    class _FakeTask:
        request = _FakeRequest()
        name = "workers.run_execution"

    pipeline.on_task_prerun(task=_FakeTask())
    structlog.get_logger("workers.execution").info("execution_started")

    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["request_id"] == "req-abc-123"
    assert payload["service"] == "workers"


def test_task_postrun_clears_the_context(pipeline) -> None:
    """Un request_id no puede filtrarse al siguiente task del mismo proceso."""
    pipeline.install_celery_logging(service="workers")
    structlog.contextvars.bind_contextvars(request_id="req-leak")

    pipeline.on_task_postrun()

    assert "request_id" not in structlog.contextvars.get_contextvars()


def test_install_is_idempotent(pipeline) -> None:
    """Arrancar dos veces (import + señal) no puede apilar handlers.

    Si se apilaran, cada log line se emitiría N veces y el coste crecería en
    silencio con cada recarga del módulo.
    """
    from celery.signals import task_prerun

    pipeline.install_celery_logging(service="workers")
    before = len(task_prerun.receivers)
    pipeline.install_celery_logging(service="workers")

    assert len(task_prerun.receivers) == before
