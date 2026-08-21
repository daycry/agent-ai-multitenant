"""Cableado del pipeline de logging en los servicios Celery (prod-08 Fase C).

Cierra dos hallazgos de la auditoría de producción 2026-06:

``observability-3`` — **workers y notification-dispatcher nunca llamaban a
``configure_logging()``**. Sus logs salían con el formato por defecto de
Celery: texto plano, sin campo ``service`` y sin el enmascarado PII que el
api-server sí aplica. Un email, un IBAN o un JWT logueado desde un task
aterrizaba EN CLARO en ``docker logs`` y en cualquier agregador aguas abajo.

``observability-7`` — la correlación moría en la frontera Celery. El
``request_id`` de la petición HTTP que encoló el trabajo no viajaba con el
mensaje, así que no había forma de unir «el usuario pulsó ejecutar» con «el
worker falló» salvo por marcas de tiempo.

Diseño
------
**Por señal, no por llamada suelta.** Celery, si nadie atiende
``setup_logging``, impone su propio handler de logging y se lleva por delante
la configuración de structlog. Conectar la señal es lo único que garantiza que
nuestra configuración es la última palabra.

**Propagación por ``before_task_publish``, no por call-site.** El plan original
proponía tocar cada ``apply_async`` del dispatch. Un único handler de
``before_task_publish`` cubre a TODOS los productores (api-server,
orchestrator, beat, el propio worker cuando encadena tareas) sin tocar ni una
línea de los call-sites, y no puede olvidarse en el siguiente productor que
alguien escriba.

**Sin paquete nuevo.** El plan presuponía extraer ``packages/shared-logging``
porque orchestrator y watchdog importaban ``api_server.logging`` «cruzando
apps». Los Dockerfiles de workers, notification-dispatcher y orchestrator
construyen todos **sobre la imagen de api-server** (``ARG BASE_IMAGE``), que ya
lleva el paquete en ``/opt/venv``; y workers importa ``api_server`` en ~50
sitios más. Extraer solo el logging no elimina el acoplamiento: lo disfraza.
Ver el ADR 0141.

Idempotencia
------------
Los signals de Celery son **globales al proceso** y ``connect(weak=False)``
apila receptores. Instalar dos veces duplicaría cada línea de log. Por eso los
handlers son funciones de módulo (identidad estable) y la instalación se
guarda con ``_STATE``.
"""

from __future__ import annotations

from typing import Any

import structlog

from api_server.logging.context import clear_request_context
from api_server.logging.setup import configure_logging

# Nombre de la cabecera del mensaje Celery que transporta el correlation id.
# Va en los `headers` del protocolo v2 (no en el body), así que sobrevive a
# cualquier firma de task y no invade sus argumentos.
CELERY_REQUEST_ID_HEADER = "request_id"

# Servicio + nivel con los que se configurará el logging cuando Celery emita
# `setup_logging`. Se rellenan en `install_celery_logging`.
_CONFIG: dict[str, str] = {"service": "celery", "level": "INFO"}
# Estado de instalación en un dict y no en un escalar de módulo: así se muta
# sin `global`, que ruff desaconseja con razón — un `global` disperso hace
# difícil razonar sobre quién cambia qué.
_STATE: dict[str, bool] = {"installed": False}


def current_request_id() -> str | None:
    """El ``request_id`` bindeado en el contexto structlog, si lo hay."""
    value = structlog.contextvars.get_contextvars().get("request_id")
    return value if isinstance(value, str) else None


# ---------------------------------------------------------------------------
# Handlers de señal (módulo-nivel → identidad estable → conectables una vez)
# ---------------------------------------------------------------------------
def on_setup_logging(**_kwargs: Any) -> None:
    """``celery.signals.setup_logging`` → nuestra configuración structlog.

    Atender esta señal le dice a Celery «yo me encargo del logging», y le
    impide instalar sus handlers. Sin esto, cualquier ``configure_logging()``
    llamado antes queda pisado en el arranque del worker.
    """
    configure_now()


def configure_now() -> None:
    """Aplica ``configure_logging`` con el servicio registrado.

    Expuesto aparte de la señal para el arranque de procesos que no son un
    worker Celery (p.ej. un `celery beat`, o un test).
    """
    configure_logging(service=_CONFIG["service"], level=_CONFIG["level"])


def on_before_task_publish(headers: dict[str, Any] | None = None, **_kwargs: Any) -> None:
    """PRODUCTOR: inyecta el ``request_id`` del contexto en las cabeceras.

    Si no hay ``request_id`` en contexto (beat periódico, comando CLI) NO se
    añade una cabecera vacía: una cabecera presente pero nula sería un
    correlation id falso, peor que su ausencia.
    """
    if headers is None:
        return
    request_id = current_request_id()
    if request_id:
        headers[CELERY_REQUEST_ID_HEADER] = request_id


def on_task_prerun(task: Any = None, **_kwargs: Any) -> None:
    """CONSUMIDOR: bindea el ``request_id`` que viajó con el mensaje.

    A partir de aquí, cada línea de log del task —incluidos los de bibliotecas
    puenteadas por stdlib— lleva el mismo correlation id que la petición HTTP
    de origen.
    """
    request = getattr(task, "request", None)
    if request is None:
        return
    request_id = getattr(request, CELERY_REQUEST_ID_HEADER, None)
    if isinstance(request_id, str) and request_id:
        structlog.contextvars.bind_contextvars(request_id=request_id)


def on_task_postrun(**_kwargs: Any) -> None:
    """CONSUMIDOR: limpia el contexto al terminar el task.

    Un worker reutiliza el proceso para el siguiente mensaje: sin este clear,
    el ``request_id`` del task anterior se filtraría a los logs del siguiente y
    la correlación mentiría (peor que no tenerla).
    """
    clear_request_context()


# ---------------------------------------------------------------------------
# Instalación
# ---------------------------------------------------------------------------
def install_celery_logging(*, service: str, level: str = "INFO") -> None:
    """Conecta el pipeline de logging + correlación a las señales de Celery.

    Idempotente: llamarlo N veces deja exactamente un receptor por señal.
    """
    _CONFIG["service"] = service
    _CONFIG["level"] = level
    if _STATE["installed"]:
        return

    from celery.signals import before_task_publish, setup_logging, task_postrun, task_prerun

    # weak=False: los handlers son funciones de módulo; sin esto el receptor
    # podría ser recolectado y la señal quedaría muda sin avisar.
    setup_logging.connect(on_setup_logging, weak=False)
    before_task_publish.connect(on_before_task_publish, weak=False)
    task_prerun.connect(on_task_prerun, weak=False)
    task_postrun.connect(on_task_postrun, weak=False)
    _STATE["installed"] = True


def install_request_id_propagation() -> None:
    """Solo la mitad PRODUCTORA, para procesos que encolan pero no consumen.

    El api-server no corre tasks: únicamente los publica. Necesita
    ``before_task_publish`` (para que el ``request_id`` viaje) pero NO las
    señales de consumidor, y su logging ya está configurado por ``main.py``.
    """
    if _STATE["installed"]:
        return

    from celery.signals import before_task_publish

    before_task_publish.connect(on_before_task_publish, weak=False)


def uninstall_celery_logging() -> None:
    """Desconecta todo. Existe para los tests: las señales son globales al
    proceso y un receptor huérfano contaminaría el resto de la suite."""

    from celery.signals import before_task_publish, setup_logging, task_postrun, task_prerun

    setup_logging.disconnect(on_setup_logging)
    before_task_publish.disconnect(on_before_task_publish)
    task_prerun.disconnect(on_task_prerun)
    task_postrun.disconnect(on_task_postrun)
    _STATE["installed"] = False


__all__ = [
    "CELERY_REQUEST_ID_HEADER",
    "configure_now",
    "current_request_id",
    "install_celery_logging",
    "install_request_id_propagation",
    "on_before_task_publish",
    "on_setup_logging",
    "on_task_postrun",
    "on_task_prerun",
    "uninstall_celery_logging",
]
