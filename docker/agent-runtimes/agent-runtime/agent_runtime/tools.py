"""The tool registry the agent loop acts through (task_02_10).

The `act` node calls a builtin tool via `ToolRegistry.call`. Fase C
ships only the trivial `echo` / `noop` tools — enough to exercise the
loop end to end. The real builtin tools (shell_exec, file_*,
http_request, kanban_update, …) land in Fase D (task_02_15+).

**Per-mode allowlist (task_06_14_07).** A chat mode (planning /
discussion / execution / custom) carries an ``allowed_tools`` whitelist
(see ``api_server.chat.modes.ChatModeConfig``). The worker forwards that
list to the runtime in the task spec; ``__main__`` applies it to the
registry with :meth:`ToolRegistry.set_allowed_tools`. From then on
:meth:`ToolRegistry.call` rejects any tool name outside the set with a
failed :class:`ToolResult` *before* the tool function runs — a
lightweight, call-time check. This is **not** the full layered guardrail
engine (pre_llm / post_llm / pre_tool / post_tool), which lands in
Plan 11; it is the minimal enforcement that makes the mode whitelist
real instead of advisory. When no allowlist is configured (the default)
every registered tool is callable.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

ToolFn = Callable[[dict[str, Any]], "ToolResult"]

# Error message a blocked call surfaces. Kept as a module-level template
# (not a scattered literal) so callers/tests can assert on it stably.
_NOT_ALLOWED_TEMPLATE = "tool '{name}' not allowed in this mode"


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
    """Name → tool function. The loop only ever calls tools through this.

    ``_allowed_tools`` is the optional per-mode whitelist. ``None`` (the
    default) means *no restriction* — every registered tool is callable.
    A set means *only these names may run*; anything else is rejected at
    :meth:`call` time. An empty set therefore blocks every tool, which is
    exactly what the ``discussion`` mode (pure conversation) wants.
    """

    _tools: dict[str, ToolFn] = field(default_factory=dict)
    _allowed_tools: frozenset[str] | None = None

    def register(self, name: str, fn: ToolFn) -> None:
        self._tools[name] = fn

    def names(self) -> list[str]:
        return sorted(self._tools)

    def set_allowed_tools(self, allowed: Iterable[str] | None) -> None:
        """Configure the per-mode allowlist.

        Passing ``None`` clears the restriction (every tool callable).
        Passing any iterable — including an empty one — installs the
        whitelist; only the listed names may run thereafter. Idempotent
        and order-independent.
        """
        self._allowed_tools = None if allowed is None else frozenset(allowed)

    def is_allowed(self, name: str) -> bool:
        """Whether ``name`` may run under the current allowlist (if any)."""
        return self._allowed_tools is None or name in self._allowed_tools

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Invoke a tool by name. An unknown tool, a blocked tool, or a
        raised exception becomes a failed `ToolResult` — the loop never
        crashes on a tool.

        When a per-mode allowlist is configured, a tool outside it is
        rejected *before* its function runs (task_06_14_07). The
        not-allowed check precedes the unknown-tool check so a caller
        cannot probe which tools exist by name through a restricted mode.
        """
        if not self.is_allowed(name):
            return ToolResult(ok=False, error=_NOT_ALLOWED_TEMPLATE.format(name=name))
        fn = self._tools.get(name)
        if fn is None:
            return ToolResult(ok=False, error=f"unknown tool: {name}")
        try:
            return fn(args)
        except Exception as exc:  # a tool must never take the loop down
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")


def _echo_tool(args: dict[str, Any]) -> ToolResult:
    return ToolResult(ok=True, output=args.get("text", ""))


def _noop_tool(args: dict[str, Any]) -> ToolResult:
    # I-5 (auditoría 2026-07-10): sin efectos secundarios, pero si el caller (los
    # guards F32 de `_decision_from`) pasa un `reason`, se devuelve como output —
    # la observación del turno siguiente lleva POR QUÉ se rechazó el FINISH en vez
    # de un `{"ok": true, "output": null}` ciego que invita a repetir el mismo
    # output truncado hasta quemar max_iterations.
    return ToolResult(ok=True, output=args.get("reason"))


def default_registry() -> ToolRegistry:
    """A registry with the Fase C placeholder tools."""
    registry = ToolRegistry()
    registry.register("echo", _echo_tool)
    registry.register("noop", _noop_tool)
    return registry
