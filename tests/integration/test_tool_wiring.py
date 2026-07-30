"""Tests for the Tool-row → ToolRegistry factory (Plan 05 task_05_17).

`tool_wiring.register_tool_specs` is the single seam that takes a
list of `ToolSpec` (one per Tool row wired to an agent) and registers
each on the loop's `ToolRegistry` with the right executor for its
`implementation_type`.

We assert:

* the three Plan-05 types (http_endpoint, python_function,
  docker_command) build + register correctly with their fields
  forwarded;
* builtin + mcp_tool are intentionally skipped (those are wired by
  other paths);
* an unknown implementation_type raises at boot, not silently at
  first tool call;
* the per-project `allowed_domains` reach http_endpoint Tools via
  the `WiringContext`;
* tools registered by this factory survive the loop's "exception
  → ToolResult" guarantee (no crash propagates to the agent).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from agent_runtime.tool_wiring import (
    ToolSpec,
    WiringContext,
    register_tool_specs,
)
from agent_runtime.tools import ToolRegistry

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# http_endpoint
# ---------------------------------------------------------------------------
def test_http_endpoint_spec_registers_and_inherits_allowlist() -> None:
    registry = ToolRegistry()
    specs = [
        ToolSpec(
            name="weather",
            implementation_type="http_endpoint",
            config={
                "url_template": "https://api.weather.example/v1?q={city}",
                "method": "GET",
                "static_headers": {"X-Api-Key": "abc"},
                "timeout_s": 15.0,
            },
        )
    ]
    ctx = WiringContext(allowed_domains=frozenset({"api.weather.example"}))
    registered = register_tool_specs(registry, specs, ctx=ctx)

    assert registered == ["weather"]
    assert "weather" in registry.names()

    # Calling through the registry hits the executor. Off-allowlist
    # URL would be rejected — but our URL is on it, so allowlist
    # check passes. The actual HTTP call fails (no MockTransport
    # here) and folds to ToolResult.ok=False via the registry's
    # exception → result guard.
    result = registry.call("weather", {"city": "Madrid"})
    # We don't assert ok=True (would need MockTransport); we assert
    # the call did NOT crash AND the error is NOT about the allowlist
    # (which would mean the ctx didn't get through).
    assert result.error is None or "not allowed" not in result.error.lower()


# ---------------------------------------------------------------------------
# python_function
# ---------------------------------------------------------------------------
def test_python_function_spec_registers_and_runs() -> None:
    registry = ToolRegistry()
    specs = [
        ToolSpec(
            name="adder",
            implementation_type="python_function",
            config={
                "code": "def run(args):\n    return args['a'] + args['b']\n",
                "timeout_s": 5.0,
            },
        )
    ]
    registered = register_tool_specs(registry, specs)
    assert registered == ["adder"]

    result = registry.call("adder", {"a": 2, "b": 3})
    assert result.ok is True
    assert result.output == 5


# ---------------------------------------------------------------------------
# docker_command
# ---------------------------------------------------------------------------
def test_docker_command_spec_registers_with_mocked_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory builds a DockerCommandTool with `docker_client=None`
    (production path). We monkeypatch the lazy `docker.from_env()` to
    return a MagicMock so the test doesn't need a daemon."""
    fake_client = MagicMock()
    fake_client.containers.run.return_value = b"hello\n"

    import agent_runtime.docker_command_tool as dct

    monkeypatch.setattr(dct, "_FORBIDDEN_RUN_KWARGS", dct._FORBIDDEN_RUN_KWARGS)

    # Patch the lazy import by inserting a fake `docker` module.
    import sys
    import types

    fake_docker = types.ModuleType("docker")
    fake_docker.from_env = lambda: fake_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    registry = ToolRegistry()
    specs = [
        ToolSpec(
            name="hello",
            implementation_type="docker_command",
            config={
                "image": "alpine:3.20",
                "command_template": ["echo", "hello"],
                "timeout_s": 10.0,
            },
        )
    ]
    registered = register_tool_specs(registry, specs)
    assert registered == ["hello"]

    # Contrato honesto (ADR 0093): dentro del sandbox NO hay Docker (principio
    # 2: sin socket) — un docker_command no puede ejecutar y lo dice,
    # redirigiendo a stack_exec (el worker corre el toolchain en el
    # runtime-template). Antes fingía ejecutar vía un client inyectado que en
    # producción jamás existe.
    result = registry.call("hello", {})
    assert result.ok is False
    assert "stack_exec" in (result.error or "")
    fake_client.containers.run.assert_not_called()


# ---------------------------------------------------------------------------
# builtin + mcp_tool are skipped
# ---------------------------------------------------------------------------
def test_builtin_and_mcp_tool_specs_are_ignored() -> None:
    """`register_tool_specs` does NOT register builtin or mcp_tool.
    Those are wired by other paths — registering them here would
    create duplicates / wrong wrappers."""
    registry = ToolRegistry()
    specs = [
        ToolSpec(name="shell_exec", implementation_type="builtin", config={}),
        ToolSpec(name="github.search_repos", implementation_type="mcp_tool", config={}),
    ]
    registered = register_tool_specs(registry, specs)
    assert registered == []
    assert registry.names() == []


# ---------------------------------------------------------------------------
# Unknown type
# ---------------------------------------------------------------------------
def test_unknown_implementation_type_raises_at_boot() -> None:
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="unknown implementation_type"):
        register_tool_specs(
            registry,
            [
                ToolSpec(
                    name="x",
                    implementation_type="quantum_computer",
                    config={},
                )
            ],
        )


# ---------------------------------------------------------------------------
# A malformed/incomplete spec is SKIPPED, never crashes the whole run. A single
# misconfigured Tool row (e.g. an http_endpoint with no url_template — observed in
# production: agent-runtime error KeyError 'url_template' killed the run at
# iteration 0) must not take down every task on the agent.
# ---------------------------------------------------------------------------
def test_http_endpoint_without_url_template_is_skipped_not_crash() -> None:
    registry = ToolRegistry()
    specs = [
        # KNOWN type, but its config is incomplete (no url_template) — unusable.
        ToolSpec(name="broken-http", implementation_type="http_endpoint", config={}),
        # A valid sibling in the same batch must still register.
        ToolSpec(
            name="adder",
            implementation_type="python_function",
            config={"code": "def run(args):\n    return 1\n"},
        ),
    ]
    registered = register_tool_specs(registry, specs)
    assert registered == ["adder"]
    assert "broken-http" not in registry.names()


def test_malformed_python_function_without_code_is_skipped() -> None:
    registry = ToolRegistry()
    specs = [ToolSpec(name="no-code", implementation_type="python_function", config={})]
    # No `code` key → the builder would KeyError; it must be skipped, not crash.
    assert register_tool_specs(registry, specs) == []


# ---------------------------------------------------------------------------
# Mixed batch — heterogeneous types in one boot
# ---------------------------------------------------------------------------
def test_mixed_batch_registers_only_the_active_types() -> None:
    """Realistic boot scenario: an agent has a builtin + an MCP tool +
    a python_function + an http_endpoint. The factory registers only
    the latter two; the rest are wired by other paths."""
    registry = ToolRegistry()
    specs = [
        ToolSpec(name="shell_exec", implementation_type="builtin", config={}),
        ToolSpec(name="gh.search", implementation_type="mcp_tool", config={}),
        ToolSpec(
            name="adder",
            implementation_type="python_function",
            config={"code": "def run(args):\n    return 1\n"},
        ),
        ToolSpec(
            name="hello",
            implementation_type="http_endpoint",
            config={"url_template": "https://x.example/"},
        ),
    ]
    ctx = WiringContext(allowed_domains=frozenset({"x.example"}))
    registered = register_tool_specs(registry, specs, ctx=ctx)
    assert set(registered) == {"adder", "hello"}
