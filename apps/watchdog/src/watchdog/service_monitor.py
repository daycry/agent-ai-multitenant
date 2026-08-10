"""Per-container health monitor.

`ServiceMonitor.check_and_recover()` runs one tick of the watchdog
loop:

  1. Reload the container state.
  2. If healthy -> reset the attempt counter.
  3. If unhealthy:
     - Exhausted backoff -> emit a single alert log, deliver the alert to
       the api-server's ingest endpoint (prod-08 task_prod08_watchdog_14)
       and stop trying to restart.
     - Otherwise wait until the backoff window passes and call
       container.restart().

The Docker client is duck-typed: any object exposing `reload()`,
`attrs` (dict with State.Health.Status / State.Status) and
`restart(timeout=...)` works. Tests pass a stub.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from watchdog.alerting import AlertSink
from watchdog.backoff import AttemptRecord, BackoffPolicy

_logger = structlog.get_logger(__name__)


class _ContainerLike(Protocol):
    attrs: dict[str, Any]

    def reload(self) -> None: ...
    def restart(self, *, timeout: int = ...) -> None: ...


# Statuses that mean "do not attempt recovery".
_HEALTHY_STATUSES = {"healthy", "running", "starting"}


@dataclass
class ServiceMonitor:
    name: str
    container: _ContainerLike
    policy: BackoffPolicy = field(default_factory=BackoffPolicy)
    record: AttemptRecord = field(default_factory=AttemptRecord)
    # Opcional a propósito: sin sink el monitor se comporta exactamente como
    # antes (solo log), que es lo que quieren los tests que ya existían y un
    # watchdog corriendo en dev sin api-server delante.
    alert_sink: AlertSink | None = None

    # ----- inspection -----
    def status(self) -> str:
        self.container.reload()
        state = self.container.attrs.get("State", {})
        # Prefer healthcheck status when present; fall back to running state.
        health = state.get("Health") or {}
        return health.get("Status") or state.get("Status") or "unknown"

    def is_healthy(self) -> bool:
        return self.status() in _HEALTHY_STATUSES

    # ----- alerta terminal (prod-08 task_prod08_watchdog_14) -----
    def _deliver_alert(self) -> None:
        """Entrega la alerta al api-server como mucho una vez por episodio.

        Se reintenta en cada tick MIENTRAS no haya llegado: el caso que importa
        es el api-server arrancando después del watchdog, donde una única
        oportunidad de entrega equivale a no tener aviso."""
        if self.alert_sink is None or self.record.alert_delivered:
            return
        self.record.alert_delivered = self.alert_sink.deliver(
            service=self.name,
            attempts=self.record.consecutive_failures,
        )

    # ----- the watchdog tick -----
    def check_and_recover(self, *, now: float | None = None) -> str:
        """Run one tick. Returns the action taken for log/test purposes.

        'ok'           container reports healthy.
        'restarted'    backoff allowed an attempt and restart was called.
        'waiting'      unhealthy but the backoff window hasn't elapsed.
        'exhausted'    over the attempts cap; alert emitted at most once.
        'restart_failed' restart raised an exception.
        """
        try:
            healthy = self.is_healthy()
        except Exception as exc:  # - docker errors vary widely
            _logger.error("watchdog.inspect_failed", name=self.name, error=str(exc))
            return "inspect_failed"

        if healthy:
            if self.record.consecutive_failures:
                _logger.info("watchdog.recovered", name=self.name)
            self.record.reset()
            return "ok"

        if self.record.exhausted(self.policy):
            if not self.record.alerted:
                _logger.error(
                    "watchdog.alert",
                    name=self.name,
                    reason="max_attempts_exhausted",
                    attempts=self.record.consecutive_failures,
                )
                self.record.alerted = True
            self._deliver_alert()
            return "exhausted"

        if not self.record.ready_for_next_attempt(self.policy, now=now):
            return "waiting"

        self.record.record_attempt(now=now)
        try:
            self.container.restart(timeout=10)
            _logger.info(
                "watchdog.restart",
                name=self.name,
                attempt=self.record.consecutive_failures,
            )
            return "restarted"
        except Exception as exc:  # - docker errors vary widely
            _logger.error(
                "watchdog.restart_failed",
                name=self.name,
                attempt=self.record.consecutive_failures,
                error=str(exc),
            )
            return "restart_failed"
