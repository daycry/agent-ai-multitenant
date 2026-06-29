"""Integration tests: TestRuntimeRunner launches test-runtime
containers (Plan 06 task_06_05).

We mock the Docker client because a real ``docker.containers.run`` of
fourteen runtimes per CI push would burn half an hour; the contract
we pin here is what *kwargs* the runner passes to the daemon (network,
mounts, env, hardening flags) and that cleanup always happens.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from workers.config import Settings

pytestmark = pytest.mark.integration


def _fake_client() -> tuple[MagicMock, list[Any]]:
    """Build a docker client mock that tracks every container started.

    Returns ``(client, started_containers)`` — the list is mutated as
    ``containers.run`` is called so tests can introspect kwargs."""
    started: list[Any] = []

    def _run(image: str, **kwargs: Any) -> MagicMock:
        c = MagicMock()
        c.id = f"container-{len(started)}"
        c.image = image
        c.kwargs = kwargs
        c.exec_run = MagicMock(return_value=MagicMock(exit_code=0, output=b"check passed\n"))
        started.append(c)
        return c

    client = MagicMock()
    client.containers.run.side_effect = _run
    client.networks.create.return_value = MagicMock(name="test-net", remove=MagicMock())
    # Set .name on the network mock (MagicMock auto-creates .name as a
    # property and ignores constructor args — assign explicitly).
    client.networks.create.return_value.name = "test-runtime-python-pytest-abcd"
    return client, started


def _spec_for_python_pytest(**overrides: Any) -> Any:
    from shared_test_runtimes.catalog import get
    from workers.test_runtime import AcceptanceCheck, RuntimePlan, TestRuntimeSpec

    plan = RuntimePlan(
        template=get("python-pytest"),
        checks=(
            AcceptanceCheck(
                id="a",
                description="unit tests",
                runtime="python-pytest",
                command="pytest tests/unit -v",
            ),
        ),
    )
    base: dict[str, Any] = {
        "plan": plan,
        "worktree_host_path": "/data/projects/t1/p1/worktrees/task-1",
        "dep_cache_host_path": "/data/dep-cache/pip-abcd",
    }
    base.update(overrides)
    return TestRuntimeSpec(**base)


def test_launch_creates_private_internal_bridge() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    runner = TestRuntimeRunner(Settings(), client=client)
    result = runner.launch(_spec_for_python_pytest())

    # Network created with internal=True (default network_policy='none').
    client.networks.create.assert_called_once()
    _name, kwargs = (
        client.networks.create.call_args.args,
        client.networks.create.call_args.kwargs,
    )
    assert kwargs["internal"] is True
    assert kwargs["driver"] == "bridge"
    assert result.network_name.startswith("test-runtime-python-pytest")


def test_launch_mounts_worktree_and_depcache() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec_for_python_pytest())

    # The main test-runtime container is the only one started (no aux,
    # no proxy). Inspect its mounts.
    assert len(started) == 1
    main = started[0]
    mounts = main.kwargs["mounts"]
    targets = {m["Target"] for m in mounts}
    assert "/workspace" in targets
    assert "/root/.cache/pip" in targets


def test_launch_applies_hardening_envelope() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec_for_python_pytest())

    main = started[0]
    assert main.kwargs["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in main.kwargs["security_opt"]
    assert main.kwargs["read_only"] is True
    assert main.kwargs["user"] == "1000:1000"
    # Bridge is the task's private one — not the default agent network.
    assert main.kwargs["network"].startswith("test-runtime-python-pytest")


def test_launch_runs_default_pre_install_then_check() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    runner = TestRuntimeRunner(Settings(), client=client)
    result = runner.launch(_spec_for_python_pytest())

    assert result.all_passed() is True
    assert result.exit_codes == (0,)
    # The python-pytest template has 2 pre_install commands + 1 check.
    main = started[0]
    assert main.exec_run.call_count == 3
    # First two exec_runs are the pre_install commands.
    exec_calls = [call.args[0] for call in main.exec_run.call_args_list]
    assert "pip install --upgrade pip" in exec_calls[0][-1]
    assert "pip install -r requirements.txt" in exec_calls[1][-1]
    assert "pytest tests/unit -v" in exec_calls[2][-1]


def test_pre_install_failure_marks_all_checks_as_failed() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()

    # Override the side_effect so the first exec_run fails with rc=2.
    def _run_failing(image: str, **kwargs: Any) -> MagicMock:
        c = MagicMock()
        c.id = "fail-1"
        c.exec_run = MagicMock()
        c.exec_run.side_effect = [
            MagicMock(exit_code=2, output=b"network error\n"),
        ]
        started.append(c)
        return c

    client.containers.run.side_effect = _run_failing
    runner = TestRuntimeRunner(Settings(), client=client)
    result = runner.launch(_spec_for_python_pytest())

    # One check declared, one exit_code reported (and it's the failed rc).
    assert result.exit_codes == (2,)
    assert result.all_passed() is False


def test_cleanup_always_runs_on_success() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    network = client.networks.create.return_value
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec_for_python_pytest())

    # Every started container removed, network removed.
    for c in started:
        c.remove.assert_called_once_with(force=True)
    network.remove.assert_called_once()


def test_cleanup_runs_even_if_main_run_raises() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    network = client.networks.create.return_value

    def _run_then_raise(image: str, **kwargs: Any) -> MagicMock:
        c = MagicMock()
        c.id = f"container-{len(started)}"
        c.exec_run = MagicMock(side_effect=RuntimeError("daemon panic"))
        started.append(c)
        return c

    client.containers.run.side_effect = _run_then_raise
    runner = TestRuntimeRunner(Settings(), client=client)
    with pytest.raises(RuntimeError, match="daemon panic"):
        runner.launch(_spec_for_python_pytest())

    # Cleanup ran in finally even though main_run raised.
    for c in started:
        c.remove.assert_called_once_with(force=True)
    network.remove.assert_called_once()


def test_run_command_runs_single_command_without_pre_install() -> None:
    # ADR 0093 / stack_exec: run_command executes ONE caller command in the stack
    # runtime — NO default_pre_install (the agent's own `composer install` would
    # otherwise be doubled), NO acceptance checks.
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    runner = TestRuntimeRunner(Settings(), client=client)
    rc, logs = runner.run_command(
        _spec_for_python_pytest(), "composer install --no-interaction", timeout_s=120
    )

    assert len(started) == 1
    main = started[0]
    # Exactly one exec_run (the command) — no pre_install, no checks.
    assert main.exec_run.call_count == 1
    assert "composer install --no-interaction" in main.exec_run.call_args.args[0][-1]
    assert rc == 0
    assert "check passed" in logs  # the fake exec_run output


def test_run_command_always_cleans_up() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    network = client.networks.create.return_value
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.run_command(_spec_for_python_pytest(), "php spark migrate", timeout_s=60)

    for c in started:
        c.remove.assert_called_once_with(force=True)
    network.remove.assert_called_once()


def test_open_network_policy_creates_non_internal_bridge() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, _ = _fake_client()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec_for_python_pytest(network_policy="open"))

    kwargs = client.networks.create.call_args.kwargs
    assert kwargs["internal"] is False


def test_template_cpu_memory_defaults_carry_through() -> None:
    """node-playwright declares 2 CPU / 2048 MB by default."""
    from shared_test_runtimes.catalog import get
    from workers.test_runtime import (
        AcceptanceCheck,
        RuntimePlan,
        TestRuntimeRunner,
        TestRuntimeSpec,
    )

    client, started = _fake_client()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(
        TestRuntimeSpec(
            plan=RuntimePlan(
                template=get("node-playwright"),
                checks=(
                    AcceptanceCheck(
                        id="e2e",
                        description="e2e",
                        runtime="node-playwright",
                        command="npx playwright test",
                    ),
                ),
            ),
            worktree_host_path="/data/worktrees/x",
        )
    )

    main = started[0]
    # nano_cpus is 1e9 per core → 2e9.
    assert main.kwargs["nano_cpus"] == 2 * 1_000_000_000
    assert main.kwargs["mem_limit"] == "2048m"
