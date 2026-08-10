"""Entrega de la alerta terminal del watchdog (prod-08 ``task_prod08_watchdog_14``).

Cuando un servicio agota el backoff, el watchdog había hecho todo lo que podía y
la única salida era ``_logger.error("watchdog.alert", ...)``: una línea de log
DENTRO de un contenedor, en un stack sin agregación de logs desplegada. Es decir,
el vigilante gritaba en una habitación vacía.

Este módulo es el emisor que faltaba. El destino ya existía y funcionaba de punta
a punta: ``POST /internal/alerts/ingest`` en el api-server
(``task_prod08_alert_ingest_01``), que traduce el webhook v4 de Alertmanager a una
notificación del Plan 10 y la encola hacia los canales del System Admin.

Tres decisiones que conviene no deshacer:

* **El payload imita a Alertmanager, no inventa un formato propio.** El endpoint
  valida con el schema v4 (``alerts[].labels/annotations/status``); cualquier otra
  forma se comería un 422 y volveríamos a la habitación vacía, esta vez con la
  falsa sensación de tener la cadena montada.
* **Transporte por ``urllib`` de la stdlib, no ``requests``/``httpx``.** El
  watchdog declara dos dependencias (``docker`` y ``structlog``); una imagen que
  solo tiene que hacer un POST de 400 bytes no necesita más. El transporte es
  inyectable, así que los tests no tocan red.
* **El log local se conserva SIEMPRE.** La entrega remota es el techo, no el
  suelo: si el api-server es justamente lo que se ha caído, el log sigue siendo la
  única evidencia y no se pierde por haber "mejorado" la alerta.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

_logger = structlog.get_logger(__name__)

# El `alertname` con el que estas alertas llegan al System Admin. Es también la
# mitad estable del fingerprint, así que cambiarlo rompe la deduplicación de los
# episodios en curso.
WATCHDOG_ALERTNAME = "WatchdogServiceUnrecoverable"

# Variables de entorno del contenedor (ver docker/docker-compose.yml). El token
# es el MISMO `API_SERVER_ALERTS_INGEST_TOKEN` que valida el endpoint.
_URL_ENV = "WATCHDOG_ALERTS_INGEST_URL"
_TOKEN_ENV = "WATCHDOG_ALERTS_INGEST_TOKEN"
_TIMEOUT_ENV = "WATCHDOG_ALERTS_TIMEOUT"

_DEFAULT_TIMEOUT_SECONDS = 5.0


class _Transport(Protocol):
    """POST síncrono; devuelve el código HTTP. Levanta ante fallo de red."""

    def __call__(self, url: str, body: bytes, headers: dict[str, str], timeout: float) -> int: ...


def _urllib_post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> int:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    # La URL viene de la configuración del despliegue, no de entrada de usuario.
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return int(response.status)


def _fingerprint(service: str) -> str:
    """Clave de dedup del endpoint (``fingerprint + status``).

    Estable por servicio y entre procesos: un watchdog reiniciado durante el
    mismo episodio no vuelve a notificar lo mismo."""
    return f"watchdog:{service}"


@dataclass
class AlertSink:
    """Entrega la alerta de agotamiento al api-server. Degrada a log.

    Sin ``url`` o sin ``token`` queda **desconfigurado** y no intenta nada: en dev
    el watchdog corre sin api-server delante y no debe petardear contra él en cada
    tick.
    """

    url: str | None = None
    token: str | None = None
    timeout: float = _DEFAULT_TIMEOUT_SECONDS
    transport: _Transport = _urllib_post

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> AlertSink:
        source = os.environ if env is None else env
        raw_timeout = source.get(_TIMEOUT_ENV, "")
        try:
            timeout = float(raw_timeout) if raw_timeout else _DEFAULT_TIMEOUT_SECONDS
        except ValueError:
            timeout = _DEFAULT_TIMEOUT_SECONDS
        return cls(
            url=(source.get(_URL_ENV) or "").strip() or None,
            token=(source.get(_TOKEN_ENV) or "").strip() or None,
            timeout=timeout,
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.token)

    def payload(self, *, service: str, attempts: int) -> dict[str, Any]:
        """El webhook v4 sintético que el endpoint sabe parsear."""
        summary = f"watchdog: {service} sigue caído tras {attempts} reintentos"
        return {
            "version": "4",
            "status": "firing",
            "receiver": "watchdog",
            "alerts": [
                {
                    "status": "firing",
                    "fingerprint": _fingerprint(service),
                    "labels": {
                        "alertname": WATCHDOG_ALERTNAME,
                        # `critical` es lo que enruta al receiver de respaldo de
                        # alertmanager.yml: un servicio de infraestructura que no
                        # levanta solo es exactamente el caso que debe despertar
                        # a alguien.
                        "severity": "critical",
                        "instance": service,
                        "service": service,
                        "source": "watchdog",
                    },
                    "annotations": {
                        "summary": summary,
                        "description": (
                            f"El watchdog agotó su política de backoff reiniciando "
                            f"'{service}' ({attempts} intentos consecutivos) y el "
                            f"contenedor sigue sin reportarse sano. Requiere "
                            f"intervención manual: docs/06-runbooks/restart-services.md"
                        ),
                    },
                    "startsAt": datetime.now(UTC).isoformat(),
                }
            ],
        }

    def deliver(self, *, service: str, attempts: int) -> bool:
        """Intenta la entrega. Devuelve si llegó. NUNCA levanta."""
        if not self.is_configured:
            return False
        assert self.url is not None  # is_configured lo garantiza (mypy)
        body = json.dumps(self.payload(service=service, attempts=attempts)).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        try:
            status = self.transport(self.url, body, headers, self.timeout)
        except Exception as exc:  # red caída, DNS, api-server muerto…
            _logger.error(
                "watchdog.alert_delivery_failed",
                service=service,
                error=str(exc),
            )
            return False
        if 200 <= status < 300:
            _logger.info("watchdog.alert_delivered", service=service, status=status)
            return True
        _logger.error(
            "watchdog.alert_delivery_rejected",
            service=service,
            status=status,
        )
        return False
