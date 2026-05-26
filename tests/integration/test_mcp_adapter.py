"""Plan 05 task_05_03 — tests for the MCP→ToolRegistry adapter.

The adapter is the sync-over-async bridge that lets the agent loop
(sync) talk to MCP servers (async + connection-oriented). The toy
server (`_toy_mcp_server.py`) exposes `echo` + `add`; we register
them on a `ToolRegistry` and assert:

  - the registered names are namespaced as `<server>.<tool>`;
  - `registry.call("toy.add", {"a":2,"b":3})` returns ToolResult.ok=True
    with output==5 (the adapter pre-parses JSON);
  - a nonexistent tool routes to MCPToolError → ToolResult.ok=False
    (the agent loop never sees the raw exception);
  - calling after `runner.close()` raises (no silent zombie state);
  - the runner is context-manager safe (`with MCPToolRunner() as r:` ...).

The runner uses a background thread so we DELIBERATELY exercise it
from a sync test function to mirror the agent loop's call pattern.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from agent_runtime.mcp_tools import MCPToolRunner, register_mcp_server
from agent_runtime.tools import ToolRegistry
from shared_mcp import MCPServerConfig

pytestmark = pytest.mark.integration


_TOY_SERVER = Path(__file__).resolve().parent / "_toy_mcp_server.py"


def _stdio_config(name: str = "toy") -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        transport="stdio",
        command=sys.executable,
        args=(str(_TOY_SERVER), "--transport", "stdio"),
        timeout_s=15.0,
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def test_runner_start_close_is_idempotent() -> None:
    """Calling start/close twice must not raise."""
    runner = MCPToolRunner()
    runner.start()
    runner.start()  # no-op, no exception
    runner.close()
    runner.close()  # no-op, no exception


def test_runner_as_context_manager() -> None:
    """`with MCPToolRunner() as r` opens + closes cleanly."""
    with MCPToolRunner() as runner:
        runner.connect(_stdio_config("toy-ctx"))
        assert {t.name for t in runner.tools("toy-ctx")} == {"echo", "add"}


def test_connect_returns_tool_list() -> None:
    """`runner.connect()` returns the same tools the toy server
    advertises — saves the caller a round-trip to `tools()`."""
    with MCPToolRunner() as runner:
        tools = runner.connect(_stdio_config("toy"))
        assert {t.name for t in tools} == {"echo", "add"}


def test_connect_twice_with_same_name_raises() -> None:
    """Defensive: double-connect of the same server name is a
    programmer error, not a silent re-open."""
    with MCPToolRunner() as runner:
        runner.connect(_stdio_config("toy"))
        with pytest.raises(ValueError, match="already connected"):
            runner.connect(_stdio_config("toy"))


def test_call_before_start_raises() -> None:
    """Using the runner without `start()` first must raise — keeps
    bugs visible rather than queuing into a dead loop."""
    runner = MCPToolRunner()
    with pytest.raises(RuntimeError, match="start"):
        runner.connect(_stdio_config("ghost"))


# ---------------------------------------------------------------------------
# Adapter: tools land on the registry as <server>.<tool>
# ---------------------------------------------------------------------------
def test_register_mcp_server_namespaces_tools_with_separator() -> None:
    registry = ToolRegistry()
    with MCPToolRunner() as runner:
        tools = runner.connect(_stdio_config("toy"))
        names = register_mcp_server(registry, runner, "toy", tools)
        assert set(names) == {"toy.echo", "toy.add"}
        assert set(registry.names()) >= {"toy.echo", "toy.add"}


def test_register_uses_runner_cache_when_tools_arg_omitted() -> None:
    """The `tools=` arg is optional; if omitted, the helper grabs the
    cached list the runner already fetched at `connect()` time."""
    registry = ToolRegistry()
    with MCPToolRunner() as runner:
        runner.connect(_stdio_config("toy"))
        names = register_mcp_server(registry, runner, "toy")
        assert set(names) == {"toy.echo", "toy.add"}


# ---------------------------------------------------------------------------
# Sync call round-trip through ToolRegistry
# ---------------------------------------------------------------------------
def test_call_via_registry_returns_parsed_output() -> None:
    """`registry.call("toy.add", {a,b})` returns ToolResult(ok=True,
    output=5) — the adapter pre-parses the toy server's JSON output."""
    registry = ToolRegistry()
    with MCPToolRunner() as runner:
        runner.connect(_stdio_config("toy"))
        register_mcp_server(registry, runner, "toy")

        result = registry.call("toy.add", {"a": 2, "b": 3})
        assert result.ok is True
        assert result.output == 5  # JSON-pre-parsed

        echoed = registry.call("toy.echo", {"text": "hello"})
        assert echoed.ok is True
        assert echoed.output == "hello"


def test_call_unknown_tool_returns_toolresult_not_raise() -> None:
    """A tool that doesn't exist on the server folds into
    ToolResult(ok=False) — the agent loop never sees an exception."""
    registry = ToolRegistry()
    with MCPToolRunner() as runner:
        runner.connect(_stdio_config("toy"))
        # Register a non-existent tool on the registry directly to
        # simulate the agent calling something the server rejects.
        from agent_runtime.mcp_tools import _make_tool_fn

        registry.register("toy.ghost", _make_tool_fn(runner, "toy", "does_not_exist"))
        result = registry.call("toy.ghost", {})
        assert result.ok is False
        assert "mcp tool error" in (result.error or "").lower()


def test_call_after_close_raises_keyerror() -> None:
    """After closing the runner all sessions are dropped; further
    calls must raise KeyError, not silently return wrong data."""
    runner = MCPToolRunner()
    runner.start()
    runner.connect(_stdio_config("toy"))
    runner.close()
    with pytest.raises(KeyError):
        runner.call_tool("toy", "echo", {"text": "x"})
