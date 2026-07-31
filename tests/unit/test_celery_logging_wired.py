"""Los servicios Celery ENCHUFAN de verdad el pipeline de logging (prod-08).

El patrón dominante de esta base (``docs/03-guides/verificar-antes-de-implementar.md``
§5) es «mecanismo entregado, cero llamantes»: el módulo existe, está probado, y
no lo invoca nadie. ``api_server.logging.celery_pipeline`` sería inútil si
``workers`` y ``notification-dispatcher`` no lo instalaran al arrancar.

Estos tests recargan cada ``celery_app`` y comprueban que, por el mero hecho de
importarse —que es lo que hace el CLI de Celery—, las señales quedan
conectadas y con el nombre de servicio correcto.
"""

from __future__ import annotations

import importlib

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture()
def pipeline():
    from api_server.logging import celery_pipeline

    celery_pipeline.uninstall_celery_logging()
    yield celery_pipeline
    celery_pipeline.uninstall_celery_logging()


def _connected(signal) -> bool:
    from api_server.logging import celery_pipeline

    handlers = {r for _, r in signal.receivers}
    return any(
        h in handlers
        for h in (
            celery_pipeline.on_setup_logging,
            celery_pipeline.on_task_prerun,
            celery_pipeline.on_before_task_publish,
        )
    )


@pytest.mark.parametrize(
    ("module_name", "expected_service"),
    [
        ("workers.celery_app", "workers"),
        ("notification_dispatcher.celery_app", "notification-dispatcher"),
    ],
)
def test_importing_celery_app_installs_the_logging_pipeline(
    pipeline, module_name: str, expected_service: str
) -> None:
    from celery.signals import setup_logging, task_prerun

    module = importlib.import_module(module_name)
    importlib.reload(module)

    assert _connected(setup_logging), (
        f"{module_name} no conectó setup_logging: sus logs seguirán saliendo "
        "en el formato por defecto de Celery, sin JSON y sin enmascarado PII"
    )
    assert _connected(
        task_prerun
    ), f"{module_name} no conectó task_prerun: el request_id no se bindeará"
    # El servicio con el que se etiquetarán las líneas de log.
    assert pipeline._CONFIG["service"] == expected_service


def test_api_server_installs_the_producer_half(pipeline) -> None:
    """El api-server no consume tasks: los PRODUCE. Necesita `before_task_publish`.

    Sin este hook el `request_id` de la petición HTTP nunca sube al mensaje y
    los logs del worker quedan incorrelacionables con los del api-server — el
    hallazgo observability-7 exactamente.
    """
    import importlib

    from celery.signals import before_task_publish

    main = importlib.import_module("api_server.main")
    importlib.reload(main)

    handlers = {r for _, r in before_task_publish.receivers}
    assert (
        pipeline.on_before_task_publish in handlers
    ), "api_server.main no instaló la propagación de request_id a Celery"


def test_request_id_survives_the_celery_boundary_round_trip(pipeline) -> None:
    """Ida y vuelta con el `Context` REAL de Celery, no con un doble.

    El productor escribe la cabecera; Celery la transporta en los headers del
    protocolo v2 y las expone como atributos de `task.request`; el consumidor
    la bindea. Se usa `celery.app.task.Context` de verdad para que el test
    falle si esa suposición sobre el wire format deja de ser cierta.
    """
    import structlog
    from celery.app.task import Context

    pipeline.install_celery_logging(service="workers")

    # --- Productor (proceso api-server) -----------------------------------
    structlog.contextvars.bind_contextvars(request_id="req-cross-boundary")
    headers: dict = {"id": "task-1", "task": "workers.run_execution"}
    pipeline.on_before_task_publish(headers=headers)
    structlog.contextvars.clear_contextvars()

    # --- Transporte (lo que hace Celery con los headers del protocolo v2) --
    request = Context(headers)

    # --- Consumidor (proceso worker) --------------------------------------
    class _Task:
        pass

    task = _Task()
    task.request = request  # type: ignore[attr-defined]
    pipeline.on_task_prerun(task=task)

    assert structlog.contextvars.get_contextvars()["request_id"] == "req-cross-boundary"
