"""Unit tests for the installer's subprocess seam (Plan prod-01 task_16).

The real ``StepExecutor``/``StackTeardown`` shell out to ``docker compose``.
That host-touching call is isolated behind the :class:`CommandRunner` Protocol
so the executors can be driven with a :class:`FakeCommandRunner` that records
the exact argv (the central assertion of task_16/18) WITHOUT any Docker.
"""

from __future__ import annotations

import pytest
from installer_backend.command_runner import (
    CommandResult,
    CommandRunner,
    FakeCommandRunner,
)

pytestmark = pytest.mark.unit


def test_fake_runner_is_a_command_runner() -> None:
    assert isinstance(FakeCommandRunner(), CommandRunner)


def test_fake_runner_records_argv_and_cwd_in_order() -> None:
    runner = FakeCommandRunner()
    runner.run(["docker", "compose", "pull"], cwd="/srv/stack")
    runner.run(["docker", "compose", "up", "-d"], cwd="/srv/stack")
    assert runner.calls == [
        ("docker", "compose", "pull"),
        ("docker", "compose", "up", "-d"),
    ]
    assert runner.cwds == ["/srv/stack", "/srv/stack"]


def test_fake_runner_defaults_to_success() -> None:
    result = FakeCommandRunner().run(["docker", "compose", "ps"])
    assert isinstance(result, CommandResult)
    assert result.returncode == 0


def test_fake_runner_fail_on_prefix_returns_nonzero() -> None:
    runner = FakeCommandRunner(fail_on=("docker", "compose", "up"))
    ok = runner.run(["docker", "compose", "pull"])
    bad = runner.run(["docker", "compose", "up", "-d"])
    assert ok.returncode == 0
    assert bad.returncode != 0


def test_fake_runner_scripted_response_by_argv() -> None:
    scripted = CommandResult(returncode=0, output_lines=('[{"Health":"healthy"}]',))
    runner = FakeCommandRunner(
        responses={("docker", "compose", "ps", "--format", "json"): scripted}
    )
    result = runner.run(["docker", "compose", "ps", "--format", "json"])
    assert result.output_lines == ('[{"Health":"healthy"}]',)


def test_fake_runner_streams_output_via_on_line() -> None:
    scripted = CommandResult(returncode=0, output_lines=("pulling", "extracting", "done"))
    runner = FakeCommandRunner(responses={("docker", "compose", "pull"): scripted})
    seen: list[str] = []
    runner.run(["docker", "compose", "pull"], on_line=seen.append)
    assert seen == ["pulling", "extracting", "done"]
