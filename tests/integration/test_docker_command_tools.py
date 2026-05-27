"""Tests for the `docker_command`-typed Tool executor (Plan 05 task_05_14).

CI doesn't run a Docker daemon (it'd be too slow + flaky to spin up
real containers for every assertion), so these tests inject a
``MagicMock`` as the docker SDK client and verify two things:

1. The kwargs the executor passes to ``client.containers.run`` carry
   the full hardening envelope (cap_drop ALL, no-new-privileges,
   network_mode='none', read-only fs, mem_limit, pids_limit, non-root
   user, remove=True). If a future edit weakens any of these, the
   spec catches it.

2. The error mapping — SDK exceptions (ContainerError, ImageNotFound,
   ReadTimeout, APIError) fold into typed ``ToolResult.error``
   messages, never crash into the agent loop.

A handful of tests also exercise the placeholder rendering rules
(same shape as http_endpoint_tool but JSON-escaped values, no URL
encoding).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from agent_runtime.docker_command_tool import (
    DockerCommandTool,
    DockerCommandToolSpec,
    build_docker_command_tool,
    render_command,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# render_command — placeholder rules
# ---------------------------------------------------------------------------
def test_render_substitutes_strings_verbatim() -> None:
    out = render_command(["echo", "hello {name}"], {"name": "world"})
    assert out == ["echo", "hello world"]


def test_render_jsonifies_non_string_values() -> None:
    """Non-string values become JSON so a list/dict survives intact."""
    out = render_command(["python", "-c", "print({payload})"], {"payload": [1, 2, 3]})
    assert out == ["python", "-c", "print([1, 2, 3])"]


def test_render_missing_key_raises_keyerror() -> None:
    with pytest.raises(KeyError, match="missing"):
        render_command(["echo", "{missing}"], {})


def test_render_ignores_non_identifier_placeholders() -> None:
    """{1abc}, {foo.bar} etc. must NOT match — same protection as
    http_endpoint_tool against accidental attribute walks."""
    out = render_command(["echo", "{1bad}", "{foo.bar}"], {"foo": "v"})
    # Both stay literal because the regex requires an identifier.
    assert out == ["echo", "{1bad}", "{foo.bar}"]


# ---------------------------------------------------------------------------
# Construction guards
# ---------------------------------------------------------------------------
def test_missing_image_raises_at_construction() -> None:
    with pytest.raises(ValueError, match="image"):
        DockerCommandTool(name="x", image="", command_template=["echo"])


def test_empty_command_template_raises_at_construction() -> None:
    with pytest.raises(ValueError, match="command_template"):
        DockerCommandTool(name="x", image="alpine", command_template=[])


# ---------------------------------------------------------------------------
# Hardened run kwargs — the security envelope
# ---------------------------------------------------------------------------
def _make_tool(**overrides: Any) -> tuple[DockerCommandTool, MagicMock]:
    client = MagicMock()
    client.containers.run.return_value = b"ok\n"
    tool = DockerCommandTool(
        name="t",
        image="alpine:3.20",
        command_template=["echo", "hello"],
        docker_client=client,
        **overrides,
    )
    return tool, client


def test_run_kwargs_include_full_hardening_envelope() -> None:
    tool, client = _make_tool()
    tool({})
    assert client.containers.run.called
    _args, kwargs = client.containers.run.call_args
    assert kwargs["remove"] is True
    assert kwargs["network_mode"] == "none"
    assert kwargs["cap_drop"] == ["ALL"]
    assert "no-new-privileges" in kwargs["security_opt"]
    assert kwargs["read_only"] is True
    assert kwargs["user"] == "1000:1000"
    assert kwargs["pids_limit"] == 64
    # mem_limit default = 256 MB.
    assert kwargs["mem_limit"] == 256 * 1024 * 1024
    # tmpfs grants a small writable /tmp.
    assert "/tmp" in kwargs["tmpfs"]
    # DNS is empty when network is none — defense in depth.
    assert kwargs["dns"] == []


def test_image_and_command_are_forwarded_to_run() -> None:
    tool, client = _make_tool()
    tool({})
    args, _kwargs = client.containers.run.call_args
    assert args[0] == "alpine:3.20"
    assert args[1] == ["echo", "hello"]


def test_command_placeholders_are_rendered_before_launch() -> None:
    client = MagicMock()
    client.containers.run.return_value = b"hi\n"
    tool = DockerCommandTool(
        name="render",
        image="alpine",
        command_template=["echo", "hello {name}"],
        docker_client=client,
    )
    tool({"name": "world"})
    _img, command = client.containers.run.call_args[0]
    assert command == ["echo", "hello world"]


def test_network_mode_can_be_overridden_per_tool() -> None:
    """A Tool that legitimately needs network opts in to 'bridge'.
    The default stays 'none' so the security envelope holds for
    everything else."""
    tool, client = _make_tool(network_mode="bridge")
    tool({})
    assert client.containers.run.call_args.kwargs["network_mode"] == "bridge"
    # When network != none, DNS is None (let the daemon decide), not [].
    assert client.containers.run.call_args.kwargs["dns"] is None


def test_static_env_is_forwarded() -> None:
    client = MagicMock()
    client.containers.run.return_value = b"ok\n"
    tool = DockerCommandTool(
        name="env",
        image="alpine",
        command_template=["env"],
        static_env={"FOO": "bar"},
        docker_client=client,
    )
    tool({})
    assert client.containers.run.call_args.kwargs["environment"] == {"FOO": "bar"}


# ---------------------------------------------------------------------------
# Successful round-trip
# ---------------------------------------------------------------------------
def test_stdout_bytes_are_decoded_into_output() -> None:
    client = MagicMock()
    client.containers.run.return_value = b"hello world\n"
    tool = DockerCommandTool(
        name="echo",
        image="alpine",
        command_template=["echo", "hello world"],
        docker_client=client,
    )
    result = tool({})
    assert result.ok is True
    assert result.output == "hello world\n"


def test_string_output_from_sdk_is_also_handled() -> None:
    """Some SDK versions return str instead of bytes — we still
    surface it as ToolResult.output."""
    client = MagicMock()
    client.containers.run.return_value = "already a string"
    tool = DockerCommandTool(
        name="str-out",
        image="alpine",
        command_template=["echo", "hi"],
        docker_client=client,
    )
    result = tool({})
    assert result.ok is True
    assert result.output == "already a string"


# ---------------------------------------------------------------------------
# SDK exception → typed ToolResult.error
# ---------------------------------------------------------------------------
class _FakeContainerError(Exception):
    """Stand-in for docker.errors.ContainerError so the test doesn't
    pull docker into CI. The executor matches by class name."""

    def __init__(self, exit_status: int, stderr: bytes) -> None:
        super().__init__("container failed")
        self.exit_status = exit_status
        self.stderr = stderr


_FakeContainerError.__name__ = "ContainerError"


def test_container_non_zero_exit_surfaces_as_failed_toolresult() -> None:
    client = MagicMock()
    client.containers.run.side_effect = _FakeContainerError(
        exit_status=127, stderr=b"sh: foo: not found\n"
    )
    tool = DockerCommandTool(
        name="bad",
        image="alpine",
        command_template=["foo"],
        docker_client=client,
    )
    result = tool({})
    assert result.ok is False
    assert "127" in (result.error or "")
    assert "foo: not found" in (result.error or "")


class _FakeImageNotFound(Exception):  # noqa: N818 — mirrors docker SDK's own naming
    pass


_FakeImageNotFound.__name__ = "ImageNotFound"


def test_image_not_found_surfaces_as_failed_toolresult() -> None:
    client = MagicMock()
    client.containers.run.side_effect = _FakeImageNotFound("missing:latest not in registry")
    tool = DockerCommandTool(
        name="missing",
        image="missing:latest",
        command_template=["echo"],
        docker_client=client,
    )
    result = tool({})
    assert result.ok is False
    assert "image not found" in (result.error or "").lower()


class _FakeReadTimeout(Exception):  # noqa: N818 — mirrors docker SDK's own naming
    pass


_FakeReadTimeout.__name__ = "ReadTimeout"


def test_timeout_surfaces_as_failed_toolresult() -> None:
    client = MagicMock()
    client.containers.run.side_effect = _FakeReadTimeout("ran past 30s")
    tool = DockerCommandTool(
        name="slow",
        image="alpine",
        command_template=["sleep", "9999"],
        docker_client=client,
    )
    result = tool({})
    assert result.ok is False
    assert "timed out" in (result.error or "")


def test_missing_placeholder_returns_failed_toolresult() -> None:
    client = MagicMock()
    tool = DockerCommandTool(
        name="x",
        image="alpine",
        command_template=["echo", "{missing}"],
        docker_client=client,
    )
    result = tool({})
    assert result.ok is False
    assert "missing required placeholder" in (result.error or "")
    # Critically: containers.run was never invoked.
    assert not client.containers.run.called


# ---------------------------------------------------------------------------
# Spec + builder convenience
# ---------------------------------------------------------------------------
def test_build_from_spec_propagates_fields() -> None:
    spec = DockerCommandToolSpec(
        name="lint",
        image="python:3.12-alpine",
        command_template=["python", "-c", "print(42)"],
        timeout_s=15.0,
        mem_limit_bytes=128 * 1024 * 1024,
        pids_limit=32,
        network_mode="none",
        static_env={"FOO": "bar"},
    )
    client = MagicMock()
    client.containers.run.return_value = b"42\n"
    tool = build_docker_command_tool(spec, docker_client=client)
    assert tool.image == "python:3.12-alpine"
    assert tool.mem_limit_bytes == 128 * 1024 * 1024
    assert tool.pids_limit == 32
    result = tool({})
    assert result.ok is True
    assert result.output == "42\n"
