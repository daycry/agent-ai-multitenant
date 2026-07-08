"""Integration tests: the post-install execution sandbox (Plan 09 task_09_06).

Exercises :class:`api_server.marketplace.sandbox.MarketplaceSandbox` with
the **Docker client MOCKED**. A real container run against a live daemon is
an integration step pending the sandbox runtime image; the test environment
cannot reliably spin real Docker, so — exactly like the test-runtime
launch tests (:mod:`tests.integration.test_test_runtime_launch`) — we pin
the contract that does NOT need a daemon:

  * the SPEC the runner hands the daemon carries the full hardening
    envelope: ``cap_drop ALL`` + ``no-new-privileges`` + read-only root +
    non-root uid + ``mem_limit`` + ``pids_limit`` + cpu cap;
  * the network policy is honored: ``none`` / ``restricted`` ride an
    *internal* bridge, only ``open`` gets a non-internal one;
  * the smoke command is wrapped with a wall-clock ``timeout`` so a wedged
    probe cannot run forever, and a 124 surfaces as ``timed_out``;
  * a failing smoke check surfaces as a TYPED failure
    (``SandboxResult.passed is False``), not an exception;
  * a launch error surfaces as :class:`SandboxError` (fail closed);
  * teardown (container.remove + network.remove) ALWAYS runs — on success,
    on smoke failure, on timeout, and even when the run raises;
  * a config that would bind the Docker socket is rejected by the
    :func:`assert_no_docker_socket` tripwire.

No ``cross_tenant`` marker: the sandbox is a pure container-orchestration
utility that touches no tenant-owned rows. The multi-tenancy guarantee is
enforced at the install-flow layer (task_09_11) that invokes it under
``get_tenant_session`` + RLS, which is where the cross-tenant tests live.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from api_server.marketplace.sandbox import (
    SANDBOX_UID_GID,
    DockerSocketLeakError,
    MarketplaceSandbox,
    SandboxError,
    SandboxSpec,
    assert_no_docker_socket,
    build_sandbox_run_kwargs,
)
from api_server.marketplace.trust import NetworkPolicy

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# A mock Docker client that records every container/network it created.
# ---------------------------------------------------------------------------
def _fake_client(
    *,
    exit_code: int = 0,
    stdout: bytes = b"smoke ok\n",
    stderr: bytes = b"",
    exec_raises: Exception | None = None,
) -> tuple[MagicMock, list[Any]]:
    """Build a docker client mock.

    Returns ``(client, started)`` — ``started`` is mutated as
    ``containers.run`` is called so tests can introspect the run kwargs.
    ``exec_run`` returns ``demux=True`` style ``(stdout, stderr)`` output.
    """
    started: list[Any] = []

    def _run(image: str, **kwargs: Any) -> MagicMock:
        c = MagicMock()
        c.id = f"sandbox-{len(started)}"
        c.image = image
        c.kwargs = kwargs
        exec_mock = MagicMock()
        if exec_raises is not None:
            exec_mock.side_effect = exec_raises
        else:
            exec_mock.return_value = MagicMock(exit_code=exit_code, output=(stdout, stderr))
        c.exec_run = exec_mock
        started.append(c)
        return c

    client = MagicMock()
    client.containers.run.side_effect = _run
    network = MagicMock()
    network.name = "marketplace-sandbox-deadbeef"
    network.remove = MagicMock()
    client.networks.create.return_value = network
    return client, started


def _spec(**overrides: Any) -> SandboxSpec:
    base: dict[str, Any] = {
        "image": "agentic/sandbox-python:latest",
        "smoke_command": "python -c 'import tool; print(tool.ping())'",
    }
    base.update(overrides)
    return SandboxSpec(**base)


# ---------------------------------------------------------------------------
# Hardening envelope — the binding requirement (cap_drop ALL, etc.)
# ---------------------------------------------------------------------------
def test_spec_kwargs_have_full_hardening_envelope() -> None:
    """build_sandbox_run_kwargs carries every lockdown flag the .docx
    mandates for a container that runs third-party code (CLAUDE.md §2)."""
    kwargs = build_sandbox_run_kwargs(_spec(), "marketplace-sandbox-abc")

    assert kwargs["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in kwargs["security_opt"]
    assert kwargs["read_only"] is True
    assert kwargs["user"] == SANDBOX_UID_GID
    assert kwargs["user"] != "0:0"  # never root
    # Resource caps bound a leak / fork-bomb.
    assert kwargs["mem_limit"] == "256m"
    assert kwargs["pids_limit"] == 128
    assert kwargs["nano_cpus"] == 1_000_000_000
    # /tmp is a size-capped noexec/nosuid tmpfs; nothing else is writable.
    assert "/tmp" in kwargs["tmpfs"]
    assert "noexec" in kwargs["tmpfs"]["/tmp"]
    assert "nosuid" in kwargs["tmpfs"]["/tmp"]


def test_launch_applies_hardening_envelope_through_run() -> None:
    """The same envelope reaches the daemon when run() launches."""
    client, started = _fake_client()
    MarketplaceSandbox(client=client).run(_spec())

    assert len(started) == 1
    main = started[0]
    assert main.kwargs["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in main.kwargs["security_opt"]
    assert main.kwargs["read_only"] is True
    assert main.kwargs["user"] == SANDBOX_UID_GID
    assert main.kwargs["mem_limit"] == "256m"
    assert main.kwargs["pids_limit"] == 128


def test_resource_overrides_carry_through() -> None:
    kwargs = build_sandbox_run_kwargs(_spec(mem_limit="512m", pids_limit=64, cpu=2.0), "net-x")
    assert kwargs["mem_limit"] == "512m"
    assert kwargs["pids_limit"] == 64
    assert kwargs["nano_cpus"] == 2_000_000_000


def test_workspace_mount_is_read_only() -> None:
    """The downloaded artifact is mounted read-only — the probe must not be
    able to mutate the thing it is testing."""
    kwargs = build_sandbox_run_kwargs(_spec(workspace_host_path="/data/dl/listing-abc"), "net-x")
    mounts = kwargs["mounts"]
    assert len(mounts) == 1
    assert mounts[0]["Target"] == "/workspace"
    assert mounts[0]["Source"] == "/data/dl/listing-abc"
    # docker.types.Mount stores read-only as ReadOnly=True.
    assert mounts[0]["ReadOnly"] is True


def test_no_workspace_mount_when_unset() -> None:
    kwargs = build_sandbox_run_kwargs(_spec(), "net-x")
    assert "mounts" not in kwargs


# ---------------------------------------------------------------------------
# Network policy honored (ADR 0094 / task_prod12_net_01: NUNCA NAT crudo)
# ---------------------------------------------------------------------------
def test_every_network_policy_rides_an_internal_bridge() -> None:
    """TODAS las políticas (incluida OPEN) van en bridge INTERNAL — el NAT
    crudo de 'open' se eliminó (ADR 0094 D1, mitad marketplace de
    task_prod12_net_01). El egress de 'open' es SOLO vía registry-proxy."""
    for policy in (NetworkPolicy.NONE, NetworkPolicy.RESTRICTED, NetworkPolicy.OPEN):
        client, _ = _fake_client()
        MarketplaceSandbox(client=client).run(_spec(network_policy=policy))
        kwargs = client.networks.create.call_args.kwargs
        assert kwargs["internal"] is True, f"{policy} must use an internal bridge"
        assert kwargs["driver"] == "bridge"


def test_open_policy_attaches_registry_proxy_and_injects_proxy_env() -> None:
    """OPEN = egress PROXIFICADO: el registry-proxy se conecta al bridge
    interno, el contenedor recibe HTTP(S)_PROXY apuntándole, y al terminar
    el proxy se DESCONECTA (nunca se borra — es un servicio compartido)."""
    client, started = _fake_client()
    proxy = MagicMock()
    client.containers.get.return_value = proxy
    network = client.networks.create.return_value

    result = (
        MarketplaceSandbox(
            client=client,
            registry_proxy_url="http://registry-proxy:8888",
            registry_proxy_container="agentic-registry-proxy",
            registry_proxy_alias="registry-proxy",
        )
    ).run(_spec(network_policy=NetworkPolicy.OPEN))

    client.containers.get.assert_called_once_with("agentic-registry-proxy")
    network.connect.assert_called_once_with(proxy, aliases=["registry-proxy"])
    env = started[0].kwargs["environment"]
    assert env["HTTP_PROXY"] == "http://registry-proxy:8888"
    assert env["HTTPS_PROXY"] == "http://registry-proxy:8888"
    # Teardown: disconnect (force) — nunca remove del proxy compartido.
    network.disconnect.assert_called_once()
    assert result.proxied_egress is True
    assert result.network_policy == "open"


def test_open_policy_without_proxy_configured_stays_offline() -> None:
    """Sin registry-proxy configurado/alcanzable, OPEN se queda OFFLINE
    (bridge interno sin attach, sin env de proxy) — nunca NAT crudo."""
    client, started = _fake_client()
    sandbox = MarketplaceSandbox(client=client, registry_proxy_url="", registry_proxy_container="")
    result = sandbox.run(_spec(network_policy=NetworkPolicy.OPEN))

    assert client.networks.create.call_args.kwargs["internal"] is True
    env = started[0].kwargs["environment"]
    assert "HTTP_PROXY" not in env
    assert result.proxied_egress is False


def test_none_policy_never_touches_the_proxy() -> None:
    client, started = _fake_client()
    MarketplaceSandbox(
        client=client,
        registry_proxy_url="http://registry-proxy:8888",
        registry_proxy_container="agentic-registry-proxy",
    ).run(_spec(network_policy=NetworkPolicy.NONE))
    client.containers.get.assert_not_called()
    assert "HTTP_PROXY" not in started[0].kwargs["environment"]


def test_network_policy_stamped_as_label() -> None:
    kwargs = build_sandbox_run_kwargs(_spec(network_policy=NetworkPolicy.RESTRICTED), "net-x")
    assert kwargs["labels"]["com.agentic-platform.network-policy"] == "restricted"


# ---------------------------------------------------------------------------
# Timeout wiring
# ---------------------------------------------------------------------------
def test_smoke_command_is_wrapped_with_timeout() -> None:
    """The probe is wrapped in GNU ``timeout`` so a wedged process cannot
    run forever, and the configured timeout value is honored."""
    client, started = _fake_client()
    MarketplaceSandbox(client=client).run(_spec(timeout_s=30))

    main = started[0]
    exec_argv = main.exec_run.call_args.args[0]
    # exec is ["sh", "-c", "timeout 30 sh -c '<smoke>'"]
    assert exec_argv[0] == "sh"
    assert exec_argv[1] == "-c"
    assert exec_argv[2].startswith("timeout 30 ")
    assert "import tool" in exec_argv[2]


def test_timeout_exit_surfaces_as_timed_out_failure() -> None:
    """A 124 (killed by timeout) surfaces as a typed failure, not a pass."""
    client, _ = _fake_client(exit_code=124, stdout=b"", stderr=b"killed\n")
    result = MarketplaceSandbox(client=client).run(_spec())

    assert result.timed_out is True
    assert result.passed is False
    assert result.exit_code == 124


# ---------------------------------------------------------------------------
# Result handling — typed failure vs. exception
# ---------------------------------------------------------------------------
def test_passing_smoke_check_is_typed_success() -> None:
    client, _ = _fake_client(exit_code=0, stdout=b"pong\n")
    result = MarketplaceSandbox(client=client).run(_spec())

    assert result.passed is True
    assert result.timed_out is False
    assert result.exit_code == 0
    assert "pong" in result.stdout


def test_failing_smoke_check_surfaces_as_typed_failure_not_exception() -> None:
    """A probe that runs and exits non-zero is a SandboxResult with
    ``passed is False`` — NOT a raised exception. The install flow records
    it and blocks; it must not crash the api-server."""
    client, _ = _fake_client(exit_code=1, stdout=b"", stderr=b"ImportError: no module 'tool'\n")
    result = MarketplaceSandbox(client=client).run(_spec())

    assert result.passed is False
    assert result.timed_out is False
    assert result.exit_code == 1
    assert "ImportError" in result.stderr


def test_stdout_and_stderr_captured_separately() -> None:
    client, _ = _fake_client(exit_code=0, stdout=b"on-out\n", stderr=b"on-err\n")
    result = MarketplaceSandbox(client=client).run(_spec())
    assert "on-out" in result.stdout
    assert "on-err" in result.stderr


def test_oversized_logs_are_truncated() -> None:
    from api_server.marketplace.sandbox import MAX_CAPTURED_LOG_BYTES

    big = b"A" * (MAX_CAPTURED_LOG_BYTES + 5_000)
    client, _ = _fake_client(exit_code=0, stdout=big)
    result = MarketplaceSandbox(client=client).run(_spec())

    assert result.truncated is True
    assert "[truncated]" in result.stdout
    # The captured text is bounded — never the full oversized blob.
    assert len(result.stdout) <= MAX_CAPTURED_LOG_BYTES + 64


def test_launch_failure_surfaces_as_sandbox_error() -> None:
    """A daemon error starting the container fails closed (SandboxError)."""
    client, _ = _fake_client()
    client.containers.run.side_effect = RuntimeError("ImageNotFound")
    with pytest.raises(SandboxError, match="could not start sandbox container"):
        MarketplaceSandbox(client=client).run(_spec())


def test_network_create_failure_surfaces_as_sandbox_error() -> None:
    client, _ = _fake_client()
    client.networks.create.side_effect = RuntimeError("network pool exhausted")
    with pytest.raises(SandboxError, match="could not create sandbox network"):
        MarketplaceSandbox(client=client).run(_spec())


def test_exec_failure_surfaces_as_sandbox_error() -> None:
    """If the daemon drops the exec mid-run we fail closed."""
    client, _ = _fake_client(exec_raises=RuntimeError("exec stream closed"))
    with pytest.raises(SandboxError, match="sandbox exec failed"):
        MarketplaceSandbox(client=client).run(_spec())


# ---------------------------------------------------------------------------
# Teardown ALWAYS runs
# ---------------------------------------------------------------------------
def test_teardown_runs_on_success() -> None:
    client, started = _fake_client(exit_code=0)
    network = client.networks.create.return_value
    MarketplaceSandbox(client=client).run(_spec())

    for c in started:
        c.remove.assert_called_once_with(force=True)
    network.remove.assert_called_once()


def test_teardown_runs_on_smoke_failure() -> None:
    client, started = _fake_client(exit_code=1)
    network = client.networks.create.return_value
    MarketplaceSandbox(client=client).run(_spec())

    for c in started:
        c.remove.assert_called_once_with(force=True)
    network.remove.assert_called_once()


def test_teardown_runs_on_timeout() -> None:
    client, started = _fake_client(exit_code=124)
    network = client.networks.create.return_value
    MarketplaceSandbox(client=client).run(_spec())

    for c in started:
        c.remove.assert_called_once_with(force=True)
    network.remove.assert_called_once()


def test_teardown_runs_even_when_exec_raises() -> None:
    """The container + network are removed even though exec raised and the
    run propagated a SandboxError."""
    client, started = _fake_client(exec_raises=RuntimeError("daemon panic"))
    network = client.networks.create.return_value
    with pytest.raises(SandboxError):
        MarketplaceSandbox(client=client).run(_spec())

    # The container was started (so it's in `started`) and then removed.
    for c in started:
        c.remove.assert_called_once_with(force=True)
    network.remove.assert_called_once()


def test_teardown_swallows_remove_errors() -> None:
    """A teardown that itself raises must not mask the real result."""
    client, started = _fake_client(exit_code=0)
    network = client.networks.create.return_value

    def _run(image: str, **kwargs: Any) -> MagicMock:
        c = MagicMock()
        c.id = "sandbox-0"
        c.kwargs = kwargs
        c.exec_run = MagicMock(return_value=MagicMock(exit_code=0, output=(b"ok\n", b"")))
        c.remove = MagicMock(side_effect=RuntimeError("already gone"))
        started.append(c)
        return c

    client.containers.run.side_effect = _run
    network.remove.side_effect = RuntimeError("network busy")

    # Result still returns despite both teardown steps raising.
    result = MarketplaceSandbox(client=client).run(_spec())
    assert result.passed is True


# ---------------------------------------------------------------------------
# Docker socket tripwire
# ---------------------------------------------------------------------------
def test_assert_no_docker_socket_rejects_volume_bind() -> None:
    with pytest.raises(DockerSocketLeakError):
        assert_no_docker_socket(
            {"volumes": {"/var/run/docker.sock": {"bind": "/var/run/docker.sock"}}}
        )


def test_assert_no_docker_socket_rejects_mount() -> None:
    with pytest.raises(DockerSocketLeakError):
        assert_no_docker_socket(
            {"mounts": [{"Source": "/run/docker.sock", "Target": "/run/docker.sock"}]}
        )


def test_assert_no_docker_socket_passes_clean_kwargs() -> None:
    # Default sandbox kwargs never bind the socket → no raise.
    assert_no_docker_socket(build_sandbox_run_kwargs(_spec(), "net-x"))


def test_socket_leak_is_a_sandbox_error_subclass() -> None:
    """DockerSocketLeakError is a SandboxError so the install flow's
    fail-closed handler catches it too."""
    assert issubclass(DockerSocketLeakError, SandboxError)
