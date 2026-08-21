"""El watchdog entrega su alerta terminal a un humano (prod-08 task_prod08_watchdog_14).

Hasta esta tarea, agotar el backoff producía UNA LÍNEA DE LOG local
(``_logger.error("watchdog.alert", ...)``) dentro de un contenedor sin
agregación de logs desplegada: nadie se enteraba de que postgres llevaba media
hora sin levantar. El destino correcto ya existía y funcionaba de punta a punta
—``POST /internal/alerts/ingest`` (task_prod08_alert_ingest_01), que hace fan-out
al canal del System Admin—; lo que faltaba era el emisor.

Lo que estos tests fijan, y por qué cada uno:

  * la entrega ocurre **una sola vez** por episodio de agotamiento (el bucle
    tickea cada 30 s; sin esto, un postgres muerto genera 120 notificaciones/hora);
  * el payload es el **webhook v4 de Alertmanager**, porque el endpoint lo parsea
    con ese schema — un payload propio se tragaría un 422 silencioso;
  * el ``fingerprint`` es estable por servicio, que es la clave con la que el
    endpoint deduplica;
  * un fallo de entrega **NO tumba el tick** y deja rastro (el log local sigue
    siendo el suelo, no el techo);
  * sin configuración, el sink no intenta nada: un watchdog en dev no debe
    petardear contra un api-server que no existe;
  * y la recuperación del servicio **rearma** la entrega, para que el segundo
    episodio también avise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from watchdog.alerting import WATCHDOG_ALERTNAME, AlertSink
from watchdog.backoff import BackoffPolicy
from watchdog.service_monitor import ServiceMonitor


@dataclass
class FakeContainer:
    """Duck-typed stand-in for docker.models.containers.Container."""

    attrs: dict[str, Any] = field(default_factory=lambda: {"State": {"Status": "running"}})
    restart_calls: int = 0

    def reload(self) -> None:
        pass

    def restart(self, *, timeout: int = 10) -> None:
        self.restart_calls += 1

    def set_health(self, status: str) -> None:
        self.attrs = {"State": {"Status": "running", "Health": {"Status": status}}}


@dataclass
class RecordingTransport:
    """Captura los POST en vez de hacerlos. `status` o `raises` gobiernan la respuesta."""

    calls: list[tuple[str, dict[str, Any], dict[str, str]]] = field(default_factory=list)
    status: int = 202
    raises: Exception | None = None

    def __call__(self, url: str, body: bytes, headers: dict[str, str], timeout: float) -> int:
        self.calls.append((url, json.loads(body.decode("utf-8")), headers))
        if self.raises is not None:
            raise self.raises
        return self.status


def _exhausted_monitor(
    sink: AlertSink | None,
    *,
    max_attempts: int = 2,
) -> ServiceMonitor:
    """Un monitor cuyo servicio ya agotó el backoff (listo para el tick de alerta)."""
    policy = BackoffPolicy(initial_seconds=1.0, multiplier=1.0, max_attempts=max_attempts)
    monitor = ServiceMonitor(
        name="postgres", container=FakeContainer(), policy=policy, alert_sink=sink
    )
    monitor.container.set_health("unhealthy")
    for tick in range(max_attempts):
        monitor.check_and_recover(now=float(tick))
    assert monitor.record.exhausted(policy)
    return monitor


# ---------------------------------------------------------------------------
# La entrega ocurre, una sola vez, con el payload que el endpoint sabe parsear
# ---------------------------------------------------------------------------
def test_exhausted_backoff_posts_an_alertmanager_payload() -> None:
    transport = RecordingTransport()
    sink = AlertSink(
        url="http://api-server:8000/internal/alerts/ingest",
        token="s3cr3t",
        transport=transport,
    )
    monitor = _exhausted_monitor(sink)

    assert monitor.check_and_recover(now=100.0) == "exhausted"

    assert len(transport.calls) == 1
    url, payload, headers = transport.calls[0]
    assert url == "http://api-server:8000/internal/alerts/ingest"
    assert headers["Authorization"] == "Bearer s3cr3t"
    assert headers["Content-Type"] == "application/json"

    # Envelope v4 de Alertmanager: el endpoint valida `version`/`alerts[]`.
    assert payload["version"] == "4"
    assert payload["status"] == "firing"
    assert len(payload["alerts"]) == 1
    alert = payload["alerts"][0]
    assert alert["status"] == "firing"
    assert alert["labels"]["alertname"] == WATCHDOG_ALERTNAME
    # `critical` es lo que enruta al receiver de respaldo de alertmanager.yml.
    assert alert["labels"]["severity"] == "critical"
    assert alert["labels"]["instance"] == "postgres"
    assert "postgres" in alert["annotations"]["summary"]
    assert alert["startsAt"]


def test_alert_is_delivered_once_per_exhaustion_episode() -> None:
    transport = RecordingTransport()
    monitor = _exhausted_monitor(AlertSink(url="http://x/ingest", token="t", transport=transport))

    for tick in range(5):
        assert monitor.check_and_recover(now=100.0 + tick) == "exhausted"

    # El bucle tickea cada 30 s: sin esta guarda, un servicio muerto genera
    # una notificación por tick.
    assert len(transport.calls) == 1


def test_fingerprint_is_stable_per_service() -> None:
    """El endpoint deduplica por `fingerprint + status`; debe ser el mismo entre
    procesos para que un watchdog reiniciado no vuelva a notificar lo mismo."""
    transport = RecordingTransport()
    for _ in range(2):
        monitor = _exhausted_monitor(
            AlertSink(url="http://x/ingest", token="t", transport=transport)
        )
        monitor.check_and_recover(now=100.0)

    assert len(transport.calls) == 2
    first, second = (call[1]["alerts"][0]["fingerprint"] for call in transport.calls)
    assert first == second
    assert "postgres" in first


# ---------------------------------------------------------------------------
# Degradación: el log local es el suelo, nunca el techo
# ---------------------------------------------------------------------------
def test_delivery_failure_does_not_break_the_tick() -> None:
    transport = RecordingTransport(raises=OSError("api-server unreachable"))
    monitor = _exhausted_monitor(AlertSink(url="http://x/ingest", token="t", transport=transport))

    assert monitor.check_and_recover(now=100.0) == "exhausted"
    assert monitor.record.alerted is True
    assert monitor.record.alert_delivered is False


def test_delivery_is_retried_on_the_next_tick_after_a_failure() -> None:
    """Un api-server que arranca tarde no debe costar la única notificación."""
    transport = RecordingTransport(raises=OSError("boom"))
    monitor = _exhausted_monitor(AlertSink(url="http://x/ingest", token="t", transport=transport))

    monitor.check_and_recover(now=100.0)
    transport.raises = None
    monitor.check_and_recover(now=101.0)

    assert len(transport.calls) == 2
    assert monitor.record.alert_delivered is True


def test_http_error_status_counts_as_a_failed_delivery() -> None:
    transport = RecordingTransport(status=401)
    monitor = _exhausted_monitor(AlertSink(url="http://x/ingest", token="t", transport=transport))

    monitor.check_and_recover(now=100.0)
    assert monitor.record.alert_delivered is False


def test_unconfigured_sink_never_posts() -> None:
    """En dev no hay api-server ni token: el watchdog no debe petardear contra nada."""
    transport = RecordingTransport()
    for sink in (
        AlertSink(url=None, token="t", transport=transport),
        AlertSink(url="http://x/ingest", token=None, transport=transport),
    ):
        monitor = _exhausted_monitor(sink)
        assert monitor.check_and_recover(now=100.0) == "exhausted"
    assert transport.calls == []


def test_monitor_without_a_sink_still_works() -> None:
    """El sink es opcional: sin él, el comportamiento es exactamente el de antes."""
    monitor = _exhausted_monitor(None)
    assert monitor.check_and_recover(now=100.0) == "exhausted"
    assert monitor.record.alerted is True
    assert monitor.record.alert_delivered is False


# ---------------------------------------------------------------------------
# El segundo episodio también avisa
# ---------------------------------------------------------------------------
def test_recovery_rearms_the_alert() -> None:
    transport = RecordingTransport()
    monitor = _exhausted_monitor(AlertSink(url="http://x/ingest", token="t", transport=transport))
    monitor.check_and_recover(now=100.0)
    assert len(transport.calls) == 1

    # El servicio vuelve: se resetea el contador (y con él, la alerta).
    monitor.container.set_health("healthy")
    assert monitor.check_and_recover(now=101.0) == "ok"
    assert monitor.record.alerted is False
    assert monitor.record.alert_delivered is False

    # Segundo episodio de caída: debe volver a notificar.
    monitor.container.set_health("unhealthy")
    monitor.check_and_recover(now=200.0)
    monitor.check_and_recover(now=201.0)
    monitor.check_and_recover(now=202.0)
    assert len(transport.calls) == 2


# ---------------------------------------------------------------------------
# Construcción desde el entorno (lo que hace el contenedor)
# ---------------------------------------------------------------------------
def test_sink_from_env_reads_url_and_token(monkeypatch: Any) -> None:
    monkeypatch.setenv(
        "WATCHDOG_ALERTS_INGEST_URL", "http://api-server:8000/internal/alerts/ingest"
    )
    monkeypatch.setenv("WATCHDOG_ALERTS_INGEST_TOKEN", "from-env")
    sink = AlertSink.from_env()
    assert sink.url == "http://api-server:8000/internal/alerts/ingest"
    assert sink.token == "from-env"
    assert sink.is_configured is True


def test_sink_from_env_is_unconfigured_when_the_token_is_missing(monkeypatch: Any) -> None:
    monkeypatch.setenv(
        "WATCHDOG_ALERTS_INGEST_URL", "http://api-server:8000/internal/alerts/ingest"
    )
    monkeypatch.delenv("WATCHDOG_ALERTS_INGEST_TOKEN", raising=False)
    assert AlertSink.from_env().is_configured is False
