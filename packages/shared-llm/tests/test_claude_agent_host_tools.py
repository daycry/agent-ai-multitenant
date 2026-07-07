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
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

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


# ---------------------------------------------------------------------------
# F31/P1.6 — the chat-shaped path (no host tools) must ALSO disable natives.
# `_build_options` builds the options for complete()/stream()/run_agent(); only
# the first two pass disallow_native_tools=True. We exercise the production path
# (no query_fn) with a faked SDK so we can inspect the ClaudeAgentOptions kwargs.
# ---------------------------------------------------------------------------
def _build_options_captured(
    monkeypatch: pytest.MonkeyPatch,
    *,
    allowed_tools: list[str] | None,
    disallow_native_tools: bool,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _fake_sdk(captured))
    # No query_fn -> _build_options runs the real (faked-SDK) code path.
    provider = ClaudeAgentProvider(default_model="claude-opus-4-8")
    provider._build_options(
        model="claude-opus-4-8",
        system="s",
        allowed_tools=allowed_tools,
        max_turns=8,
        effort=None,
        disallow_native_tools=disallow_native_tools,
    )
    return captured


def test_no_host_tools_path_disables_native_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `decide()`-style complete() with NO tools must not let the SDK auto-run its
    native tools (Bash/Write/Read/WebSearch) outside the host-mediated loop."""
    captured = _build_options_captured(monkeypatch, allowed_tools=None, disallow_native_tools=True)
    disallowed = captured.get("disallowed_tools")
    assert disallowed is not None, "the no-host-tools path must also set disallowed_tools"
    for native in ("Bash", "Write", "Read", "WebSearch", "WebFetch", "Edit", "Task"):
        assert native in disallowed, f"{native} must be disabled on the chat-shaped path"


def test_no_host_tools_path_respects_caller_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicitly allowed natives (córtex WebSearch/WebFetch, ADR 0076) stay enabled
    even on the no-host-tools path."""
    captured = _build_options_captured(
        monkeypatch, allowed_tools=["WebSearch", "WebFetch"], disallow_native_tools=True
    )
    disallowed = captured.get("disallowed_tools") or []
    assert "WebSearch" not in disallowed
    assert "WebFetch" not in disallowed
    assert "Bash" in disallowed
    assert captured.get("allowed_tools") == ["WebSearch", "WebFetch"]


def test_run_agent_path_keeps_native_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run_agent() escape hatch (disallow_native_tools defaults to False) must
    NOT disable the natives — that mode wants the SDK's full toolset."""
    captured = _build_options_captured(monkeypatch, allowed_tools=None, disallow_native_tools=False)
    assert captured.get("disallowed_tools") is None


def test_complete_without_tools_requests_native_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end wiring: complete() with no tools calls _build_options with
    disallow_native_tools=True (spy on the kwargs, like the effort regression test)."""

    async def _q(prompt: str, options: Any) -> AsyncIterator[Any]:
        # Generador async VACÍO: el `yield` bajo TYPE_CHECKING (False en
        # runtime) mantiene la función como generador sin código inalcanzable.
        if TYPE_CHECKING:  # pragma: no cover
            yield None

    provider = ClaudeAgentProvider(query_fn=_q, default_model="claude-opus-4-8")
    seen: dict[str, Any] = {}
    original = provider._build_options

    def _spy(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return original(**kwargs)

    provider._build_options = _spy  # type: ignore[method-assign]
    import asyncio

    from shared_llm.types import Message

    asyncio.run(provider.complete([Message(role="user", content="hi")]))
    assert seen.get("disallow_native_tools") is True


def test_run_agent_does_not_request_native_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The escape hatch must not ask to disable natives."""

    async def _q(prompt: str, options: Any) -> AsyncIterator[Any]:
        # Generador async VACÍO: el `yield` bajo TYPE_CHECKING (False en
        # runtime) mantiene la función como generador sin código inalcanzable.
        if TYPE_CHECKING:  # pragma: no cover
            yield None

    provider = ClaudeAgentProvider(query_fn=_q, default_model="claude-opus-4-8")
    seen: dict[str, Any] = {}
    original = provider._build_options

    def _spy(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return original(**kwargs)

    provider._build_options = _spy  # type: ignore[method-assign]
    import asyncio

    async def _drain() -> None:
        async for _ in provider.run_agent("hi"):
            pass

    asyncio.run(_drain())
    assert seen.get("disallow_native_tools") in (None, False)
