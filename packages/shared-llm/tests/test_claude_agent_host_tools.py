"""The claude_sdk host-tool path must DISABLE the SDK's native toolset.

When the platform advertises its HOST tools (MCP, host-executed), the model must
call THOSE — not the Claude Agent SDK's native tools (Bash/Read/Write/Edit/
ToolSearch/Task/…). A native call is harvested with its native name, which the
host's ToolRegistry doesn't know and rejects ("tool '<X>' not allowed in this
mode"), so the agent spins and times out. So `_build_tool_options` must set
``disallowed_tools`` to the native set — minus whatever the caller explicitly
re-enables via ``allowed_tools`` (the córtex's WebSearch/WebFetch, ADR 0076).

We fake the ``claude_agent_sdk`` module so the option-building runs without the
real (optional) SDK installed.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from shared_llm.providers import ClaudeAgentProvider


def _fake_sdk(captured: dict[str, Any]) -> types.ModuleType:
    fake = types.ModuleType("claude_agent_sdk")

    class _Options:
        def __init__(self, **kw: Any) -> None:
            captured.update(kw)

    class _Deny:
        def __init__(self, **kw: Any) -> None:
            pass

    def _tool(name: str, desc: str, schema: Any):  # type: ignore[no-untyped-def]
        def _deco(fn: Any) -> Any:
            return ("tool", name)

        return _deco

    fake.ClaudeAgentOptions = _Options  # type: ignore[attr-defined]
    fake.PermissionResultDeny = _Deny  # type: ignore[attr-defined]
    fake.create_sdk_mcp_server = lambda **kw: ("server", kw)  # type: ignore[attr-defined]
    fake.tool = _tool  # type: ignore[attr-defined]
    return fake


def _options(monkeypatch: pytest.MonkeyPatch, *, allowed_tools: list[str] | None) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _fake_sdk(captured))
    provider = ClaudeAgentProvider(query_fn=lambda **_k: None, default_model="claude-opus-4-8")
    provider._build_tool_options(
        system="s",
        model="claude-opus-4-8",
        specs=[{"name": "read_file", "description": "read a file", "parameters": {}}],
        max_turns=8,
        effort=None,
        allowed_tools=allowed_tools,
    )
    return captured


def test_native_sdk_tools_are_disabled_when_host_tools_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _options(monkeypatch, allowed_tools=None)
    disallowed = captured.get("disallowed_tools")
    assert disallowed is not None, "disallowed_tools must be set so the model can't use SDK natives"
    # The names the model actually reached for (and the rest of the toolset).
    reached_for = (
        "Bash",
        "Read",
        "Write",
        "Edit",
        "ToolSearch",
        "Task",
        "AskUserQuestion",
        "Workflow",
    )
    for native in reached_for:
        assert native in disallowed, f"{native} must be disabled (it shadows the host tools)"


def test_caller_allowed_natives_are_not_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # The córtex (ADR 0076) re-enables WebSearch/WebFetch via allowed_tools; those
    # must NOT end up in disallowed_tools, and the allow-list is still forwarded.
    captured = _options(monkeypatch, allowed_tools=["WebSearch", "WebFetch"])
    disallowed = captured.get("disallowed_tools") or []
    assert "WebSearch" not in disallowed
    assert "WebFetch" not in disallowed
    assert captured.get("allowed_tools") == ["WebSearch", "WebFetch"]
