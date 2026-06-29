"""The ``stack_exec`` builtin tool (ADR 0093).

Runs a command in the project's STACK runtime (``php-phpunit``, ``node-jest``, …)
— where the toolchain (``composer``/``php``/``phpunit``, ``npm``, …) actually
exists — by asking the worker over ``/internal/agent/run-stack``.

The agent-runtime itself is a thin Python+git sandbox with no Docker and no
language toolchains (principles 2/3), so ``shell_exec`` (which runs IN the
sandbox) cannot run ``composer install``. ``stack_exec`` can: the worker launches
the stack container over the task's worktree, runs the command there, and returns
``rc``+logs. The command is gated by the project's ``allowed_commands`` in the
worker (deny-by-default).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime.internal_api import InternalAgentAPI, InternalAPIError
from agent_runtime.tools import ToolResult


@dataclass
class StackExecTool:
    """A ``stack_exec`` bound to one run's internal API client + task id."""

    api: InternalAgentAPI
    task_id: str
    default_timeout_s: int = 600

    def __call__(self, args: dict[str, Any]) -> ToolResult:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return ToolResult(ok=False, error="stack_exec requires a non-empty 'command' string")
        raw_timeout = args.get("timeout_s")
        try:
            timeout_s = int(raw_timeout) if raw_timeout is not None else self.default_timeout_s
        except (TypeError, ValueError):
            return ToolResult(ok=False, error="'timeout_s' must be an integer")
        try:
            result = self.api.run_stack(task_id=self.task_id, command=command, timeout_s=timeout_s)
        except InternalAPIError as exc:
            return ToolResult(ok=False, error=f"stack_exec failed to reach the worker: {exc}")
        exit_code = int(result.get("exit_code", -1))
        return ToolResult(
            ok=exit_code == 0,
            output={
                "exit_code": exit_code,
                "logs": str(result.get("logs", "")),
                "timed_out": bool(result.get("timed_out", False)),
            },
            error=None if exit_code == 0 else f"command exited with code {exit_code}",
        )
