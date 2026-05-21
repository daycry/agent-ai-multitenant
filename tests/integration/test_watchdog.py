"""Integration test for the watchdog — kill a real container, verify recovery.

Skipped unless `WATCHDOG_E2E=1` because the test is intrusive (it
kills and restarts a Compose container) and slow (waits for the
container to report healthy again).

We pick redis as the target because it's the fastest of the five
services to come back up.
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = [pytest.mark.integration]


@pytest.mark.skipif(
    os.environ.get("WATCHDOG_E2E") != "1",
    reason="intrusive — opt in with WATCHDOG_E2E=1",
)
def test_kill_and_recover() -> None:
    docker = pytest.importorskip("docker")

    project = os.environ.get("WATCHDOG_COMPOSE_PROJECT", "agentic-platform")
    name = f"{project}-redis-1"

    client = docker.from_env()
    container = client.containers.get(name)

    # Kill it.
    container.kill()
    container.reload()
    assert container.status in {"exited", "dead", "created"}

    # Drive one tick of the watchdog against this container.
    from watchdog.backoff import BackoffPolicy
    from watchdog.service_monitor import ServiceMonitor

    monitor = ServiceMonitor(
        name="redis",
        container=container,
        policy=BackoffPolicy(initial_seconds=0.1, multiplier=1.0),
    )
    assert monitor.check_and_recover(now=0.0) == "restarted"

    # Wait up to 60s for the container to report healthy.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        container.reload()
        state = container.attrs.get("State", {})
        health = (state.get("Health") or {}).get("Status")
        if health == "healthy" or (health is None and state.get("Status") == "running"):
            return
        time.sleep(2)

    pytest.fail("redis did not become healthy within 60s of the watchdog restart")
