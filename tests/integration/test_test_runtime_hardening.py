"""Integration tests: the test-runtime aux services + DinD proxy get the
hardened envelope (Plan 06.14 task_06_14_11 / container-isolation-1/2).

The agent-runtime envelope is hardened by
``isolation.build_hardened_run_kwargs`` (tested in
``test_no_docker_socket.py``). The test-runtime's *aux* sidecars
(postgres-test / redis-test) and the optional DinD socket-proxy used to
launch WITHOUT ``cap_drop``/``no-new-privileges``/``mem_limit``/
``pids_limit`` — a leak or a fork-bomb in a transient sidecar could reach
the host. This pins the fix two ways:

  * unit-style: ``build_aux_run_kwargs`` / ``build_dind_proxy_run_kwargs``
    return the hardened kwargs (cap_drop ALL + no-new-priv + mem/pids
    caps), mirroring how ``build_hardened_run_kwargs`` is asserted.
  * behavioural: the runner actually wires those kwargs into
    ``client.containers.run`` (mocked daemon, no Docker needed).

No live Docker daemon is required — these are kwargs-building assertions
with a mocked client, like ``test_hardened_run_has_no_bind_mounts_at_all``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from workers.config import Settings
from workers.test_runtime import (
    DEFAULT_POSTGRES,
    DEFAULT_REDIS,
    AuxServiceSpec,
    TestcontainersMode,
    TestRuntimeRunner,
    build_aux_run_kwargs,
    build_dind_proxy_run_kwargs,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers (mirror test_aux_services.py / test_testcontainers_mode.py)
# ---------------------------------------------------------------------------


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
    net.name = "test-runtime-python-pytest-hard"
    client.networks.create.return_value = net
    return client, started


def _spec(*aux: Any, testcontainers: bool = False) -> Any:
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
                    description="hardening",
                    runtime="python-pytest",
                    command="pytest -q",
                ),
            ),
        ),
        worktree_host_path="/data/worktrees/h",
        dep_cache_host_path="/data/dep-cache/pip-h",
        aux_services=tuple(aux),
        testcontainers=TestcontainersMode(enabled=testcontainers),
    )


# ---------------------------------------------------------------------------
# build_aux_run_kwargs — the hardened envelope for aux sidecars
# ---------------------------------------------------------------------------


def test_aux_kwargs_drop_all_caps_and_block_privilege_escalation() -> None:
    kwargs = build_aux_run_kwargs(Settings(), DEFAULT_POSTGRES, "net-x")
    assert kwargs["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in kwargs["security_opt"]


def test_aux_kwargs_carry_mem_and_pids_caps() -> None:
    kwargs = build_aux_run_kwargs(Settings(), DEFAULT_POSTGRES, "net-x")
    assert kwargs["mem_limit"]  # truthy, set
    assert isinstance(kwargs["pids_limit"], int)
    assert kwargs["pids_limit"] > 0


def test_aux_postgres_uses_its_own_mem_limit() -> None:
    # DEFAULT_POSTGRES pins 256m on the spec — it wins over the redis default.
    kwargs = build_aux_run_kwargs(Settings(), DEFAULT_POSTGRES, "net-x")
    assert kwargs["mem_limit"] == "256m"


def test_aux_redis_uses_its_own_mem_limit() -> None:
    kwargs = build_aux_run_kwargs(Settings(), DEFAULT_REDIS, "net-x")
    assert kwargs["mem_limit"] == "128m"


def test_aux_pids_limit_falls_back_to_settings_default() -> None:
    settings = Settings(aux_default_pids_limit=42)
    spec = AuxServiceSpec(name="custom-test", image="postgres:16-alpine")
    kwargs = build_aux_run_kwargs(settings, spec, "net-x")
    # No per-spec pids_limit → operator-tunable Settings default applies.
    assert kwargs["pids_limit"] == 42


def test_aux_mem_limit_falls_back_to_settings_by_image_family() -> None:
    settings = Settings(aux_postgres_mem_limit="333m", aux_redis_mem_limit="111m")
    pg = AuxServiceSpec(name="pg-thing", image="postgres:16-alpine")
    rd = AuxServiceSpec(name="cache-thing", image="redis:7-alpine")
    assert build_aux_run_kwargs(settings, pg, "n")["mem_limit"] == "333m"
    assert build_aux_run_kwargs(settings, rd, "n")["mem_limit"] == "111m"


def test_aux_spec_mem_limit_override_wins_over_settings() -> None:
    settings = Settings(aux_postgres_mem_limit="333m")
    spec = AuxServiceSpec(name="pg-x", image="postgres:16-alpine", mem_limit="777m")
    assert build_aux_run_kwargs(settings, spec, "n")["mem_limit"] == "777m"


def test_aux_spec_pids_limit_override_wins_over_settings() -> None:
    settings = Settings(aux_default_pids_limit=128)
    spec = AuxServiceSpec(name="pg-x", image="postgres:16-alpine", pids_limit=9)
    assert build_aux_run_kwargs(settings, spec, "n")["pids_limit"] == 9


def test_aux_kwargs_preserve_network_hostname_and_env() -> None:
    # Hardening must not regress the existing wiring contract.
    kwargs = build_aux_run_kwargs(Settings(), DEFAULT_POSTGRES, "net-bridge-7")
    assert kwargs["network"] == "net-bridge-7"
    assert kwargs["hostname"] == "postgres-test"
    assert kwargs["environment"]["POSTGRES_USER"] == "test"
    assert kwargs["detach"] is True
    assert kwargs["labels"]["com.agentic-platform.role"] == "aux-service"


# ---------------------------------------------------------------------------
# build_dind_proxy_run_kwargs — the hardened envelope for the DinD proxy
# ---------------------------------------------------------------------------


def test_dind_proxy_kwargs_drop_caps_and_block_privilege_escalation() -> None:
    mode = TestcontainersMode(enabled=True)
    kwargs = build_dind_proxy_run_kwargs(Settings(), mode, "net-x")
    assert kwargs["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in kwargs["security_opt"]
    assert kwargs["read_only"] is True


def test_dind_proxy_kwargs_carry_mem_and_pids_caps() -> None:
    settings = Settings(dind_proxy_mem_limit="128m", dind_proxy_pids_limit=64)
    kwargs = build_dind_proxy_run_kwargs(settings, TestcontainersMode(enabled=True), "net-x")
    assert kwargs["mem_limit"] == "128m"
    assert kwargs["pids_limit"] == 64


def test_dind_proxy_mem_pids_are_operator_tunable() -> None:
    settings = Settings(dind_proxy_mem_limit="55m", dind_proxy_pids_limit=7)
    kwargs = build_dind_proxy_run_kwargs(settings, TestcontainersMode(enabled=True), "net-x")
    assert kwargs["mem_limit"] == "55m"
    assert kwargs["pids_limit"] == 7


def test_dind_proxy_still_mounts_socket_onto_itself_only() -> None:
    # Hardening must not drop the socket bind onto the proxy (the whole
    # point of testcontainers mode) nor leak the socket elsewhere.
    kwargs = build_dind_proxy_run_kwargs(Settings(), TestcontainersMode(enabled=True), "net-x")
    sources = {m["Source"] for m in kwargs["mounts"]}
    assert "/var/run/docker.sock" in sources
    assert kwargs["labels"]["com.agentic-platform.role"] == "dind-proxy"


# ---------------------------------------------------------------------------
# Behavioural: the runner actually applies the hardened kwargs
# ---------------------------------------------------------------------------


def test_runner_launches_aux_with_hardening() -> None:
    client, started = _client_with_started_list()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec(DEFAULT_POSTGRES))

    pg = next(c for c in started if str(c.image).startswith("postgres"))
    assert pg.kwargs["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in pg.kwargs["security_opt"]
    assert pg.kwargs["mem_limit"] == "256m"
    assert pg.kwargs["pids_limit"] == Settings().aux_default_pids_limit


def test_runner_launches_dind_proxy_with_hardening() -> None:
    client, started = _client_with_started_list()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec(testcontainers=True))

    proxy = next(c for c in started if "docker-socket-proxy" in str(c.image))
    assert proxy.kwargs["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in proxy.kwargs["security_opt"]
    assert proxy.kwargs["mem_limit"] == Settings().dind_proxy_mem_limit
    assert proxy.kwargs["pids_limit"] == Settings().dind_proxy_pids_limit


def test_every_aux_sidecar_is_hardened() -> None:
    # All default aux services (postgres-test AND redis-test), not just one.
    client, started = _client_with_started_list()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec(DEFAULT_POSTGRES, DEFAULT_REDIS))

    aux = [
        c
        for c in started
        if c.kwargs.get("labels", {}).get("com.agentic-platform.role") == "aux-service"
    ]
    assert len(aux) == 2
    for c in aux:
        assert c.kwargs["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in c.kwargs["security_opt"]
        assert c.kwargs["mem_limit"]
        assert isinstance(c.kwargs["pids_limit"], int)
