"""Unit tests for the ToolRegistry per-mode allowlist (task_06_14_07).

The agent-runtime `ToolRegistry` gained a lightweight, call-time tool
allowlist: when a chat mode's `allowed_tools` set is configured on the
registry, `call()` rejects any tool outside the set *before* the tool
function runs. This is the in-scope enforcement that makes the chat-mode
whitelist real; the full layered guardrail engine (pre_llm / post_llm /
pre_tool / post_tool) is Plan 11.

These are pure-Python tests — no DB, no I/O.
"""

from __future__ import annotations

import pytest
from agent_runtime.tools import ToolRegistry, ToolResult, default_registry

pytestmark = pytest.mark.unit


def _ok_tool(args: dict[str, object]) -> ToolResult:
    return ToolResult(ok=True, output=args.get("text", "ran"))


def _registry_with(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(name, _ok_tool)
    return registry


# ---------------------------------------------------------------------------
# Default: no allowlist → every registered tool runs
# ---------------------------------------------------------------------------
def test_no_allowlist_means_unrestricted() -> None:
    registry = _registry_with("file_read", "shell_exec")
    assert registry.is_allowed("file_read") is True
    assert registry.is_allowed("shell_exec") is True
    assert registry.call("file_read", {"text": "hi"}).ok is True
    assert registry.call("shell_exec", {}).ok is True


def test_default_registry_starts_unrestricted() -> None:
    registry = default_registry()
    # echo + noop ship as builtins, and with no allowlist both run.
    assert registry.call("echo", {"text": "x"}).ok is True
    assert registry.call("noop", {}).ok is True


# ---------------------------------------------------------------------------
# Allowlist set → only listed tools run
# ---------------------------------------------------------------------------
def test_allowlist_permits_listed_tool() -> None:
    registry = _registry_with("file_read", "shell_exec")
    registry.set_allowed_tools(["file_read"])
    result = registry.call("file_read", {"text": "hi"})
    assert result.ok is True
    assert result.output == "hi"


def test_allowlist_rejects_unlisted_tool_before_it_runs() -> None:
    calls: list[str] = []

    def _spy(_args: dict[str, object]) -> ToolResult:
        calls.append("ran")
        return ToolResult(ok=True)

    registry = ToolRegistry()
    registry.register("shell_exec", _spy)
    registry.set_allowed_tools(["file_read"])  # shell_exec NOT in set

    result = registry.call("shell_exec", {})
    assert result.ok is False
    assert result.error == "tool 'shell_exec' not allowed in this mode"
    # The crucial property: the blocked tool's function never executed.
    assert calls == []


def test_blocked_error_message_names_the_tool() -> None:
    registry = _registry_with("file_read")
    registry.set_allowed_tools(["task_comment"])
    result = registry.call("file_read", {})
    assert result.ok is False
    assert result.error == "tool 'file_read' not allowed in this mode"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
def test_empty_allowlist_blocks_every_tool() -> None:
    """The `discussion` mode ships allowed_tools=() — pure conversation.
    An empty (but present) allowlist must block all tools, NOT fall back
    to 'unrestricted'."""
    registry = _registry_with("file_read", "shell_exec")
    registry.set_allowed_tools([])
    assert registry.is_allowed("file_read") is False
    assert registry.call("file_read", {}).ok is False
    assert registry.call("shell_exec", {}).ok is False


def test_none_clears_a_previously_set_allowlist() -> None:
    registry = _registry_with("file_read", "shell_exec")
    registry.set_allowed_tools(["file_read"])
    assert registry.call("shell_exec", {}).ok is False
    registry.set_allowed_tools(None)  # clear → unrestricted again
    assert registry.call("shell_exec", {}).ok is True


def test_not_allowed_check_precedes_unknown_tool_check() -> None:
    """A restricted mode must not let a caller distinguish 'unknown tool'
    from 'tool exists but blocked' — both surface the not-allowed error.
    This prevents probing the registry's contents through a locked mode."""
    registry = _registry_with("file_read")
    registry.set_allowed_tools(["file_read"])
    # 'shell_exec' is neither registered nor allowed: not-allowed wins.
    result = registry.call("shell_exec", {})
    assert result.ok is False
    assert result.error == "tool 'shell_exec' not allowed in this mode"
    assert "unknown tool" not in (result.error or "")


def test_allowed_but_unregistered_tool_reports_unknown() -> None:
    """A tool that IS in the allowlist but was never registered still
    fails cleanly as 'unknown tool' (allowlist gates, registration runs)."""
    registry = ToolRegistry()  # nothing registered
    registry.set_allowed_tools(["file_read"])
    result = registry.call("file_read", {})
    assert result.ok is False
    assert result.error == "unknown tool: file_read"


def test_set_allowed_tools_is_order_independent_and_idempotent() -> None:
    registry = _registry_with("a", "b", "c")
    registry.set_allowed_tools(["c", "a"])
    registry.set_allowed_tools(["a", "c"])  # same set, different order
    assert registry.call("a", {}).ok is True
    assert registry.call("c", {}).ok is True
    assert registry.call("b", {}).ok is False


def test_allowlist_accepts_any_iterable() -> None:
    registry = _registry_with("file_read", "shell_exec")
    registry.set_allowed_tools(t for t in ("file_read",))  # a generator
    assert registry.call("file_read", {}).ok is True
    assert registry.call("shell_exec", {}).ok is False
