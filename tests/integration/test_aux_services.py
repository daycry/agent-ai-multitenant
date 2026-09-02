"""Integration tests: aux services on the task's private bridge
(Plan 06 task_06_06).

The runner spins postgres-test and redis-test (or any
:class:`AuxServiceSpec`) as sidecars on the same bridge as the main
test-runtime, polling each one's healthcheck until ready. We mock
docker; the contract we pin is: aux containers join the bridge with
the right hostname, healthchecks gate the launch, cleanup tears them
down even on healthcheck failure.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from workers.config import Settings

pytestmark = pytest.mark.integration


def _spec_with_aux(*aux: Any) -> Any:
    from shared_test_runtimes.catalog import get
    from workers.test_runtime import (
        AcceptanceCheck,
        RuntimePlan,
        TestRuntimeSpec,
    )

    return TestRuntimeSpec(
        plan=RuntimePlan(
            template=get("python-pytest"),
            checks=(
                AcceptanceCheck(
                    id="a",
                    description="db tests",
                    runtime="python-pytest",
                    command="pytest tests/integration -v",
                ),
            ),
        ),
        worktree_host_path="/data/worktrees/x",
        dep_cache_host_path="/data/dep-cache/pip-x",
        aux_services=tuple(aux),
    )


def _client_with_started_list() -> tuple[MagicMock, list[Any]]:
    started: list[Any] = []

    def _run(image: str, **kwargs: Any) -> MagicMock:
        c = MagicMock()
        c.id = f"container-{len(started)}"
        c.image = image
        c.kwargs = kwargs
        c.exec_run = MagicMock(return_value=MagicMock(exit_code=0, output=b""))
        started.append(c)
        return c

    client = MagicMock()
    client.containers.run.side_effect = _run
    net = MagicMock()
    net.name = "test-runtime-python-pytest-xx"
    client.networks.create.return_value = net
    return client, started


def test_default_postgres_and_redis_specs_present() -> None:
    from workers.test_runtime import (
        DEFAULT_POSTGRES,
        DEFAULT_REDIS,
        default_aux_services,
    )

    services = default_aux_services()
    assert DEFAULT_POSTGRES in services
    assert DEFAULT_REDIS in services
    assert DEFAULT_POSTGRES.image.startswith("postgres:")
    assert DEFAULT_REDIS.image.startswith("redis:")
    # Hostnames the test-runtime expects.
    assert DEFAULT_POSTGRES.resolved_alias() == "postgres-test"
    assert DEFAULT_REDIS.resolved_alias() == "redis-test"


def test_aux_services_join_the_task_bridge() -> None:
    from workers.test_runtime import DEFAULT_POSTGRES, TestRuntimeRunner

    client, started = _client_with_started_list()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec_with_aux(DEFAULT_POSTGRES))

    # Two containers started: postgres-test and the main test-runtime.
    assert len(started) == 2
    pg = started[0]
    main = started[1]
    assert pg.kwargs["network"] == main.kwargs["network"]
    assert pg.kwargs["hostname"] == "postgres-test"
    assert pg.kwargs["environment"]["POSTGRES_USER"] == "test"


def test_healthcheck_gates_launch_and_polls_until_ready() -> None:
    from workers.test_runtime import DEFAULT_POSTGRES, TestRuntimeRunner

    client, started = _client_with_started_list()
    # First two probes fail, third succeeds.
    probe_results = iter(
        [
            MagicMock(exit_code=1, output=b"not ready"),
            MagicMock(exit_code=1, output=b"still starting"),
            MagicMock(exit_code=0, output=b"accepting connections"),
        ]
    )

    main_mock = MagicMock(return_value=MagicMock(exit_code=0, output=b"check passed\n"))
    pg_mock = MagicMock(side_effect=lambda _cmd: next(probe_results))

    def _run(image: str, **kwargs: Any) -> MagicMock:
        c = MagicMock()
        c.id = f"container-{len(started)}"
        c.image = image
        c.kwargs = kwargs
        c.exec_run = pg_mock if image.startswith("postgres") else main_mock
        started.append(c)
        return c

    client.containers.run.side_effect = _run
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec_with_aux(DEFAULT_POSTGRES))

    # Healthcheck polled three times before succeeding.
    assert pg_mock.call_count == 3
    # ``pg_isready -U test -d test`` is the canonical pg probe.
    assert pg_mock.call_args.args[0][0] == "pg_isready"


def test_healthcheck_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.test_runtime import AuxServiceSpec, TestRuntimeRunner

    client, started = _client_with_started_list()
    # Always-fails probe.
    probe = MagicMock(return_value=MagicMock(exit_code=1, output=b"nope"))

    def _run(image: str, **kwargs: Any) -> MagicMock:
        c = MagicMock()
        c.id = f"container-{len(started)}"
        c.image = image
        c.exec_run = probe
        started.append(c)
        return c

    client.containers.run.side_effect = _run

    # Patch time.monotonic so the loop exits fast (one round).
    times = iter([0.0, 0.6, 999.0])
    monkeypatch.setattr(
        "workers.test_runtime.time.monotonic" if False else "time.monotonic",
        lambda: next(times),
    )
    monkeypatch.setattr("time.sleep", lambda _s: None)

    bad_aux = AuxServiceSpec(
        name="redis-broken",
        image="redis:7-alpine",
        healthcheck_cmd=("redis-cli", "ping"),
        healthcheck_timeout_s=1,
    )
    runner = TestRuntimeRunner(Settings(), client=client)
    with pytest.raises(RuntimeError, match="did not become healthy"):
        runner.launch(_spec_with_aux(bad_aux))


def test_aux_services_cleaned_up_on_main_failure() -> None:
    from workers.test_runtime import DEFAULT_POSTGRES, TestRuntimeRunner

    client, started = _client_with_started_list()

    # Main container's exec_run raises, but aux services start fine.
    def _run(image: str, **kwargs: Any) -> MagicMock:
        c = MagicMock()
        c.id = f"container-{len(started)}"
        c.image = image
        if image.startswith("postgres"):
            c.exec_run = MagicMock(return_value=MagicMock(exit_code=0, output=b"ok"))
        else:
            c.exec_run = MagicMock(side_effect=RuntimeError("kaboom"))
        started.append(c)
        return c

    client.containers.run.side_effect = _run
    runner = TestRuntimeRunner(Settings(), client=client)
    with pytest.raises(RuntimeError, match="kaboom"):
        runner.launch(_spec_with_aux(DEFAULT_POSTGRES))

    for c in started:
        c.remove.assert_called_once_with(force=True, v=True)  # task_cv_04: volúmenes anónimos fuera


def test_no_aux_services_means_no_extra_containers() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _client_with_started_list()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec_with_aux())

    # Only the main test-runtime container.
    assert len(started) == 1
