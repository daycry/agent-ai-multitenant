"""Watchdog process entry point.

python -m watchdog                          # default compose-named services
WATCHDOG_SERVICES=postgres,redis python -m watchdog
"""

from __future__ import annotations

import os
import signal
import sys
import time
from typing import NoReturn

import structlog

import docker
from watchdog.service_monitor import ServiceMonitor

# Default compose project + the five infra services from phase 00.
_DEFAULT_PROJECT = "agentic-platform"
_DEFAULT_SERVICES = ("postgres", "redis", "minio", "vault", "clamav")
_POLL_INTERVAL_SECONDS = float(os.environ.get("WATCHDOG_POLL_INTERVAL", "30"))

_logger = structlog.get_logger(__name__)


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
    monitors: list[ServiceMonitor] = []
    for svc in services:
        container_name = f"{project}-{svc}-1"
        try:
            container = client.containers.get(container_name)
        except docker.errors.NotFound:
            _logger.warning("watchdog.container_missing", container=container_name)
            continue
        monitors.append(ServiceMonitor(name=svc, container=container))
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
