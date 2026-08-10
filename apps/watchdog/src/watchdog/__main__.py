"""Watchdog process entry point.

python -m watchdog                          # default compose-named services
WATCHDOG_SERVICES=postgres,redis python -m watchdog
"""

from __future__ import annotations

import os
import signal
import sys
import time
from typing import Any, NoReturn

import structlog

import docker
from watchdog.alerting import AlertSink
from watchdog.service_monitor import ServiceMonitor

# Default compose project + the infra services this watchdog supervises.
_DEFAULT_PROJECT = "agentic-platform"
# prod-08 task_prod08_watchdog_14: los cinco servicios de infraestructura de la
# fase 00 MÁS los dos proxies. El egress-proxy es la única salida de los
# agent-runtimes hacia los LLM (ADR 0019) y el registry-proxy la única de los
# runtime-templates hacia los registries de paquetes (ADR 0094): los dos son
# puntos únicos de fallo cuya caída se manifiesta como "los agentes no funcionan"
# sin que nada señale la causa. Ahora que su healthcheck es honesto (ya no
# termina en `|| true`, deploy-9), el watchdog puede actuar sobre ellos.
_DEFAULT_SERVICES = (
    "postgres",
    "redis",
    "minio",
    "vault",
    "clamav",
    "egress-proxy",
    "registry-proxy",
)
_POLL_INTERVAL_SECONDS = float(os.environ.get("WATCHDOG_POLL_INTERVAL", "30"))

# Etiquetas que Docker Compose pone en TODO contenedor que levanta.
_PROJECT_LABEL = "com.docker.compose.project"
_SERVICE_LABEL = "com.docker.compose.service"

_logger = structlog.get_logger(__name__)


def resolve_container(
    client: Any,
    project: str,
    service: str,
    *,
    not_found: type[BaseException] = docker.errors.NotFound,
) -> Any | None:
    """El contenedor del servicio `service` del proyecto `project`, o None.

    **Por etiquetas primero, por nombre después**, y ese orden importa: el
    `egress-proxy` y el `registry-proxy` declaran `container_name:` explícito en
    `docker/docker-compose.yml`, así que la convención `{proyecto}-{servicio}-1`
    NO los encuentra. Resolverlos por nombre habría dado un watchdog que dice
    vigilarlos y no vigila ninguno — cobertura aparente, que es peor que ninguna.

    El fallback por nombre se conserva para stacks levantados a mano (sin las
    etiquetas de Compose), y un fallo de la consulta por etiquetas cae a él en vez
    de dejar al watchdog ciego.
    """
    try:
        matches = client.containers.list(
            all=True,
            filters={"label": [f"{_PROJECT_LABEL}={project}", f"{_SERVICE_LABEL}={service}"]},
        )
    except Exception as exc:  # daemon viejo / filtro no soportado
        _logger.warning("watchdog.label_lookup_failed", service=service, error=str(exc))
        matches = []
    if matches:
        return matches[0]

    try:
        return client.containers.get(f"{project}-{service}-1")
    except not_found:
        return None


def _build_monitors() -> list[ServiceMonitor]:
    project = os.environ.get("WATCHDOG_COMPOSE_PROJECT", _DEFAULT_PROJECT)
    raw_services = os.environ.get("WATCHDOG_SERVICES")
    services = (
        tuple(s.strip() for s in raw_services.split(",") if s.strip())
        if raw_services
        else _DEFAULT_SERVICES
    )

    # docker SDK ships incomplete type stubs; both attributes do exist
    # at runtime.
    client = docker.from_env()
    sink = AlertSink.from_env()
    if not sink.is_configured:
        # Sin destino la alerta terminal vuelve a ser una línea de log local, que
        # es exactamente el defecto que prod-08 vino a arreglar. Se dice alto.
        _logger.warning(
            "watchdog.alert_sink_unconfigured",
            hint="set WATCHDOG_ALERTS_INGEST_URL + WATCHDOG_ALERTS_INGEST_TOKEN",
        )
    monitors: list[ServiceMonitor] = []
    for svc in services:
        container = resolve_container(client, project, svc)
        if container is None:
            _logger.warning("watchdog.container_missing", project=project, service=svc)
            continue
        monitors.append(ServiceMonitor(name=svc, container=container, alert_sink=sink))
    return monitors


def _install_signal_handlers(stop: list[bool]) -> None:
    def _handler(*_args: object) -> None:
        stop.append(True)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def main() -> NoReturn:
    # Lazy import so test imports don't pull api_server transitively.
    # Mypy excludes apps/watchdog/* because of this cross-package import.
    from api_server.logging import configure_logging

    configure_logging(service="watchdog")

    monitors = _build_monitors()
    _logger.info(
        "watchdog.started",
        services=[m.name for m in monitors],
        poll_interval=_POLL_INTERVAL_SECONDS,
    )

    stop: list[bool] = []
    _install_signal_handlers(stop)

    while not stop:
        for monitor in monitors:
            try:
                monitor.check_and_recover()
            except Exception as exc:
                _logger.error("watchdog.tick_failed", service=monitor.name, error=str(exc))
        time.sleep(_POLL_INTERVAL_SECONDS)

    _logger.info("watchdog.stopped")
    sys.exit(0)


if __name__ == "__main__":
    main()
