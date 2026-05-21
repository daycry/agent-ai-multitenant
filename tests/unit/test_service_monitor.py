"""Unit tests for watchdog.service_monitor using a stub container.

Avoids Docker entirely — the real integration check (kill + recover
a live container) lives in tests/integration/test_watchdog.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from watchdog.backoff import BackoffPolicy
from watchdog.service_monitor import ServiceMonitor


@dataclass
class FakeContainer:
    """Duck-typed stand-in for docker.models.containers.Container."""

    attrs: dict[str, Any] = field(default_factory=lambda: {"State": {"Status": "running"}})
    restart_calls: int = 0
    restart_raises: Exception | None = None

    def reload(self) -> None:
        pass

    def restart(self, *, timeout: int = 10) -> None:
        self.restart_calls += 1
        if self.restart_raises is not None:
            raise self.restart_raises

    # Helpers used by tests, not part of the docker API.
    def set_status(self, status: str) -> None:
        self.attrs = {"State": {"Status": status}}

    def set_health(self, status: str) -> None:
        self.attrs = {"State": {"Status": "running", "Health": {"Status": status}}}


# ---------------------------------------------------------------------------
# Healthy / unhealthy
# ---------------------------------------------------------------------------
def test_healthy_container_does_nothing() -> None:
    monitor = ServiceMonitor(name="db", container=FakeContainer())
    monitor.container.set_health("healthy")
    assert monitor.check_and_recover() == "ok"
    assert monitor.container.restart_calls == 0
    assert monitor.record.consecutive_failures == 0


def test_unhealthy_container_triggers_restart() -> None:
    monitor = ServiceMonitor(name="db", container=FakeContainer())
    monitor.container.set_health("unhealthy")
    assert monitor.check_and_recover(now=0.0) == "restarted"
    assert monitor.container.restart_calls == 1
    assert monitor.record.consecutive_failures == 1


def test_exited_status_triggers_restart() -> None:
    monitor = ServiceMonitor(name="db", container=FakeContainer())
    monitor.container.set_status("exited")
    assert monitor.check_and_recover(now=0.0) == "restarted"
    assert monitor.container.restart_calls == 1


def test_starting_status_is_treated_as_healthy() -> None:
    monitor = ServiceMonitor(name="db", container=FakeContainer())
    monitor.container.set_health("starting")
    assert monitor.check_and_recover() == "ok"
    assert monitor.container.restart_calls == 0


# ---------------------------------------------------------------------------
# Backoff window
# ---------------------------------------------------------------------------
def test_second_tick_within_window_only_waits() -> None:
    policy = BackoffPolicy(initial_seconds=10.0, multiplier=3.0)
    monitor = ServiceMonitor(name="db", container=FakeContainer(), policy=policy)
    monitor.container.set_health("unhealthy")

    assert monitor.check_and_recover(now=0.0) == "restarted"
    # Only 5s have passed: still inside the 10s window.
    assert monitor.check_and_recover(now=5.0) == "waiting"
    assert monitor.container.restart_calls == 1

    # 10s later: backoff window cleared.
    assert monitor.check_and_recover(now=10.0) == "restarted"
    assert monitor.container.restart_calls == 2


# ---------------------------------------------------------------------------
# Recovery resets the counter
# ---------------------------------------------------------------------------
def test_recovery_resets_attempts() -> None:
    monitor = ServiceMonitor(name="db", container=FakeContainer())

    monitor.container.set_health("unhealthy")
    monitor.check_and_recover(now=0.0)
    assert monitor.record.consecutive_failures == 1

    monitor.container.set_health("healthy")
    assert monitor.check_and_recover(now=0.0) == "ok"
    assert monitor.record.consecutive_failures == 0


# ---------------------------------------------------------------------------
# Exhaustion
# ---------------------------------------------------------------------------
def test_after_max_attempts_alert_is_emitted_once() -> None:
    policy = BackoffPolicy(initial_seconds=1.0, multiplier=1.0, max_attempts=3)
    monitor = ServiceMonitor(name="db", container=FakeContainer(), policy=policy)
    monitor.container.set_health("unhealthy")

    monitor.check_and_recover(now=0.0)
    monitor.check_and_recover(now=1.0)
    monitor.check_and_recover(now=2.0)
    assert monitor.container.restart_calls == 3
    assert monitor.record.exhausted(policy)

    # 4th tick: alert path.
    assert monitor.check_and_recover(now=10.0) == "exhausted"
    assert monitor.record.alerted is True
    # 5th tick: alert already raised, no new restart.
    assert monitor.check_and_recover(now=20.0) == "exhausted"
    assert monitor.container.restart_calls == 3


# ---------------------------------------------------------------------------
# Restart raising an error
# ---------------------------------------------------------------------------
def test_restart_failure_still_counts_as_an_attempt() -> None:
    container = FakeContainer()
    container.restart_raises = RuntimeError("daemon down")
    monitor = ServiceMonitor(name="db", container=container)
    monitor.container.set_health("unhealthy")

    assert monitor.check_and_recover(now=0.0) == "restart_failed"
    # Counter still bumped so backoff applies before retrying.
    assert monitor.record.consecutive_failures == 1


# ---------------------------------------------------------------------------
# Inspection failures
# ---------------------------------------------------------------------------
def test_inspect_failure_is_reported_without_restart() -> None:
    class ExplodingContainer(FakeContainer):
        def reload(self) -> None:
            raise RuntimeError("docker socket gone")

    monitor = ServiceMonitor(name="db", container=ExplodingContainer())
    assert monitor.check_and_recover() == "inspect_failed"
    assert monitor.container.restart_calls == 0


# ---------------------------------------------------------------------------
# Parametrised: every unhealthy status triggers a restart
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", ["unhealthy", "exited", "dead", "removing"])
def test_all_unhealthy_statuses_trigger_restart(status: str) -> None:
    monitor = ServiceMonitor(name="db", container=FakeContainer())
    monitor.container.set_status(status)
    assert monitor.check_and_recover(now=0.0) == "restarted"
