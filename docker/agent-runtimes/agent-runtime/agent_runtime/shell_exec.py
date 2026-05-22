"""The shell_exec builtin tool (task_02_15).

Runs a command inside the agent-runtime container. Two guards on top of
the container sandbox (Fase B):

  * a per-project **allowlist** — only the listed programs may run;
  * a **timeout** — a hung command is killed.

The command is split with `shlex` and run as an argv vector — never
through a shell — so there is no shell-injection surface. stdout,
stderr and the exit code are captured and returned.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent_runtime.tools import ToolResult

# Hard ceiling on captured output so a chatty command cannot blow up
# the steps_log; stdout/stderr beyond this are truncated.
_MAX_OUTPUT_CHARS = 20_000


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return text[:_MAX_OUTPUT_CHARS] + "\n…[truncated]"


@dataclass
class ShellExecTool:
    """A `shell_exec` tool bound to one project's command allowlist.

    `allowed_commands` holds program *basenames* (`pytest`, `python`,
    `git`, …) — the first token of the command is matched against it.
    """

    allowed_commands: frozenset[str]
    timeout_s: float = 30.0
    workspace: str = "/workspace"

    def _resolve_argv(self, args: dict[str, object]) -> list[str] | ToolResult:
        """Validate the request into an argv vector, or a failed ToolResult."""
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return ToolResult(ok=False, error="shell_exec requires a non-empty 'command' string")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return ToolResult(ok=False, error=f"could not parse command: {exc}")
        if not argv:
            return ToolResult(ok=False, error="empty command")

        program = Path(argv[0]).name
        if program not in self.allowed_commands:
            return ToolResult(
                ok=False,
                error=f"command not allowed: {program}",
                output={"allowed": sorted(self.allowed_commands)},
            )
        return argv

    def __call__(self, args: dict[str, object]) -> ToolResult:
        resolved = self._resolve_argv(args)
        if isinstance(resolved, ToolResult):
            return resolved
        argv = resolved
        program = Path(argv[0]).name

        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                cwd=self.workspace,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                ok=False, error=f"command timed out after {self.timeout_s}s: {program}"
            )
        except OSError as exc:
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        return ToolResult(
            ok=proc.returncode == 0,
            output={
                "exit_code": proc.returncode,
                "stdout": _truncate(proc.stdout),
                "stderr": _truncate(proc.stderr),
            },
            error=None if proc.returncode == 0 else f"command exited with code {proc.returncode}",
        )
