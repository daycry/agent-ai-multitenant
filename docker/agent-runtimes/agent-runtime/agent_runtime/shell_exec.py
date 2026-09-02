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

import os
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


# `task_cv_20` (auditoría 2026-09-01, D-01): el hijo de `shell_exec` heredaba el
# env COMPLETO del runtime — con `AGENT_TASK_SPEC` (cabeceras de MCP,
# `approved_actions`, código de python_function) y `AGENTIC_INTERNAL_TOKEN`
# dentro—, así que un `env` del modelo, o cualquier inyección, los leía. Lo que
# hereda es una allowlist explícita: lo que un toolchain necesita para correr
# (PATH, HOME, locale, el proxy de egress) y nada que identifique al run.
_CHILD_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TERM",
        "TMPDIR",
        "TZ",
        "PYTHONDONTWRITEBYTECODE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


def _child_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in _CHILD_ENV_KEYS or key.startswith("AGENT_TOOLCHAIN_")
    }
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("TERM", "dumb")
    return env


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

    def _resolve_cwd(self, args: dict[str, object]) -> str | ToolResult:
        """The working directory: the workspace, or an optional `cwd`
        relative to it. A `cwd` that escapes the workspace is rejected so
        the command cannot run outside the task sandbox.
        """
        cwd = args.get("cwd")
        if cwd is None or (isinstance(cwd, str) and not cwd.strip()):
            return self.workspace
        if not isinstance(cwd, str):
            return ToolResult(ok=False, error="shell_exec 'cwd' must be a string")
        root = Path(self.workspace).resolve()
        target = (root / cwd).resolve()
        if target != root and root not in target.parents:
            return ToolResult(ok=False, error=f"cwd escapes the workspace: {cwd}")
        return str(target)

    def __call__(self, args: dict[str, object]) -> ToolResult:
        resolved = self._resolve_argv(args)
        if isinstance(resolved, ToolResult):
            return resolved
        argv = resolved
        program = Path(argv[0]).name

        cwd = self._resolve_cwd(args)
        if isinstance(cwd, ToolResult):
            return cwd

        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                cwd=cwd,
                env=_child_env(),
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
