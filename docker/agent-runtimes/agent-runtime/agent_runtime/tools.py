"""The tool registry the agent loop acts through (task_02_10).

The `act` node calls a builtin tool via `ToolRegistry.call`. Fase C
ships only the trivial `echo` / `noop` tools — enough to exercise the
loop end to end. The real builtin tools (shell_exec, file_*,
http_request, kanban_update, …) land in Fase D (task_02_15+).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

ToolFn = Callable[[dict[str, Any]], "ToolResult"]


@dataclass(frozen=True)
class ToolResult:
    """The outcome of one tool call."""

    ok: bool
    output: Any = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "output": self.output, "error": self.error}


@dataclass
class ToolRegistry:
    """Name → tool function. The loop only ever calls tools through this."""

    _tools: dict[str, ToolFn] = field(default_factory=dict)

    def register(self, name: str, fn: ToolFn) -> None:
        self._tools[name] = fn

    def names(self) -> list[str]:
        return sorted(self._tools)

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Invoke a tool by name. An unknown tool or a raised exception
        becomes a failed `ToolResult` — the loop never crashes on a tool."""
        fn = self._tools.get(name)
        if fn is None:
            return ToolResult(ok=False, error=f"unknown tool: {name}")
        try:
            return fn(args)
        except Exception as exc:  # a tool must never take the loop down
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")


def _echo_tool(args: dict[str, Any]) -> ToolResult:
    return ToolResult(ok=True, output=args.get("text", ""))


def _noop_tool(args: dict[str, Any]) -> ToolResult:  # noqa: ARG001
    return ToolResult(ok=True, output=None)


def default_registry() -> ToolRegistry:
    """A registry with the Fase C placeholder tools."""
    registry = ToolRegistry()
    registry.register("echo", _echo_tool)
    registry.register("noop", _noop_tool)
    return registry
