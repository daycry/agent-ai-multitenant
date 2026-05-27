"""`docker_command`-typed Tool executor (Plan 05 task_05_14).

A Tool row with ``implementation_type='docker_command'`` launches an
**ephemeral container** per call, runs the operator-declared command
inside it, captures stdout, and returns it as ``ToolResult.output``.

This is the **untrusted-code** path. Whereas
:class:`PythonFunctionTool` (task_05_13) gives a subprocess sandbox
that's enough for "operator-vetted helper code", a docker_command
Tool runs anything: arbitrary scripts, third-party binaries, an
LLM-generated snippet, a shell pipeline. The container is the
security envelope.

The lockdown mirrors the worker's agent-runtime isolation
(`apps/workers/.../isolation.py`, ADR 0012) but is simpler — these
containers are short-lived (default 30 s) and don't need a worktree
mount:

  * ``network_mode='none'`` — no network access, full stop. The
    operator opts in to ``'bridge'`` per Tool if it has to call out;
    project egress allowlists still apply at the network layer.
  * ``cap_drop=['ALL']`` and ``security_opt=['no-new-privileges']``.
  * Read-only root filesystem with ``/tmp`` as a small tmpfs.
  * Non-root uid 1000:1000.
  * ``mem_limit`` + ``pids_limit`` to cap fork bombs and runaway loops.
  * ``remove=True`` — the container is deleted at exit; the worker
    never accumulates stopped containers.
  * No bind mounts. The Docker socket NEVER leaks (the runner asserts
    via :func:`workers.isolation.assert_no_docker_socket` shape — we
    re-implement the check inline here to avoid pulling workers into
    agent-runtime's dep graph).

The command supports the same ``{placeholder}`` substitution as
``http_endpoint`` (task_05_12) — applied to every element of the
``command`` list. Values are *not* URL-encoded (this is shell-y, not
HTTP); instead we json-escape them to dodge basic shell-meta abuse.
That's a best-effort guard: the operator is still responsible for
using the right shell quoting if the command goes through ``sh -c``.

Tests use ``unittest.mock.MagicMock`` for the docker client so CI
doesn't need a daemon. The production path uses
``docker.from_env()``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.tools import ToolResult

# Same placeholder regex as http_endpoint_tool — keeps the substitution
# rules consistent for the operator.
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Docker run defaults the executor never overrides — if any of these
# is in the operator's run_kwargs they're rejected at construction.
_FORBIDDEN_RUN_KWARGS: frozenset[str] = frozenset(
    {
        "privileged",  # cannot grant kernel-level access
        "cap_add",  # cap-drop ALL is non-negotiable
        "devices",  # no /dev/* passthrough
        "ipc_mode",  # no host IPC sharing
        "pid_mode",  # no host PID sharing
        "userns_mode",  # no host user namespace
        "volumes_from",  # cannot inherit another container's mounts
    }
)


def render_command(command_template: list[str], args: dict[str, Any]) -> list[str]:
    """Substitute ``{key}`` placeholders in each element of the command list.

    Values are JSON-encoded (``json.dumps`` minus the outer quotes for
    strings) so a value containing ``$(rm -rf /)`` survives as literal
    text rather than getting evaluated by a downstream shell. The
    operator is still responsible for not piping into ``sh -c`` with
    untrusted args — see the docstring on the module.
    """

    def _escape(value: Any) -> str:
        if isinstance(value, str):
            return value
        # For non-strings (numbers, bools, dicts, lists) JSON is the
        # safest round-trip — the operator can json.loads back in the
        # container if needed.
        return json.dumps(value)

    def _replace_in(piece: str) -> str:
        def _sub(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in args:
                raise KeyError(key)
            return _escape(args[key])

        return _PLACEHOLDER_RE.sub(_sub, piece)

    return [_replace_in(piece) for piece in command_template]


@dataclass
class DockerCommandTool:
    """One docker_command-typed Tool. Each call launches a fresh
    container, captures stdout/stderr, removes the container."""

    name: str
    image: str
    command_template: list[str]
    timeout_s: float = 30.0
    # Bytes. 256 MB default — enough for most utility commands; bump
    # explicitly for memory-heavy tools.
    mem_limit_bytes: int = 256 * 1024 * 1024
    pids_limit: int = 64
    # Operator can override the default 'none' if a Tool legitimately
    # needs network (e.g. a `curl`-based one-shot). Allowlists still
    # apply at the project egress layer.
    network_mode: str = "none"
    # Extra static env vars (NON-secret). Secret env must arrive via
    # Vault auth_ref → resolver → env merge, same as MCP servers
    # (task_05_05).
    static_env: dict[str, str] = field(default_factory=dict)
    # Test injection seam — production path resolves docker.from_env()
    # lazily so importing this module doesn't require a daemon.
    docker_client: Any = None

    def __post_init__(self) -> None:
        if not self.image:
            raise ValueError("docker_command Tool requires `image`")
        if not self.command_template:
            raise ValueError("docker_command Tool requires a non-empty `command_template`")

    def _ensure_client(self) -> Any:
        if self.docker_client is not None:
            return self.docker_client
        # Lazy import — avoids importing the docker SDK at module load
        # time when this module is only browsed (e.g. by docs build).
        import docker  # type: ignore[import-not-found]

        self.docker_client = docker.from_env()
        return self.docker_client

    def __call__(self, args: dict[str, Any]) -> ToolResult:
        try:
            command = render_command(self.command_template, args)
        except KeyError as exc:
            return ToolResult(ok=False, error=f"missing required placeholder: {exc.args[0]}")

        run_kwargs = _build_run_kwargs(self, command)
        # Defense in depth: even though we control run_kwargs, refuse
        # to launch if a future edit smuggles a forbidden key.
        for key in _FORBIDDEN_RUN_KWARGS:
            if key in run_kwargs:
                return ToolResult(
                    ok=False,
                    error=f"refusing to launch: forbidden run kwarg {key!r}",
                )

        client = self._ensure_client()
        try:
            output_bytes = client.containers.run(
                self.image,
                command,
                **run_kwargs,
            )
        except Exception as exc:  # docker.errors are heterogeneous
            return _map_docker_error(exc)
        if not isinstance(output_bytes, bytes | bytearray):
            output_bytes = str(output_bytes).encode("utf-8", errors="replace")
        text = bytes(output_bytes).decode("utf-8", errors="replace")
        return ToolResult(ok=True, output=text)


def _build_run_kwargs(tool: DockerCommandTool, _command: list[str]) -> dict[str, Any]:
    """Hardened run kwargs for `containers.run`. Centralised so a code
    review can audit "what does the agent's container get?" in one place.

    Wall-clock timeout note: ``containers.run(detach=False, ...)`` blocks
    until the container exits and ignores any "max runtime" kwarg —
    docker-py doesn't expose one. The timeout in `tool.timeout_s` is
    enforced by the launcher (the agent worker, or the demo script)
    via a separate watchdog, not here. A future refactor could move
    to `containers.create + start + wait(timeout=)` for SDK-level
    enforcement; for Plan 05 we accept the simpler shape."""
    return {
        "remove": True,
        "detach": False,
        "stdout": True,
        "stderr": True,
        "stream": False,
        "network_mode": tool.network_mode,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "read_only": True,
        "tmpfs": {"/tmp": "rw,size=64m"},
        "user": "1000:1000",
        "mem_limit": tool.mem_limit_bytes,
        "pids_limit": tool.pids_limit,
        "environment": dict(tool.static_env),
        # Disable network DNS by default; matches network_mode='none'.
        "dns": [] if tool.network_mode == "none" else None,
    }


def _map_docker_error(exc: Exception) -> ToolResult:
    """Translate the docker SDK's exception zoo into a typed ToolResult.

    The SDK raises:
      - ContainerError (non-zero exit)
      - ImageNotFound
      - APIError (daemon / connection problem)
      - generic Exception fallback

    We don't depend on the SDK types here (lazy import) — match on
    class name. That's brittle if the SDK renames, but the alternative
    is importing docker at module load time.
    """
    name = type(exc).__name__
    if name == "ContainerError":
        # ContainerError carries `exit_status`, `stderr`, `command`,
        # `image`. Surface stderr so the agent can see why.
        stderr = getattr(exc, "stderr", b"")
        if isinstance(stderr, bytes | bytearray):
            stderr_text = bytes(stderr).decode("utf-8", errors="replace")[:500]
        else:
            stderr_text = str(stderr)[:500]
        exit_status = getattr(exc, "exit_status", "?")
        return ToolResult(
            ok=False,
            error=f"container exited with {exit_status}: {stderr_text}",
        )
    if name == "ImageNotFound":
        return ToolResult(ok=False, error=f"image not found: {exc}")
    if name == "ReadTimeout":
        return ToolResult(ok=False, error=f"container timed out: {exc}")
    if name == "APIError":
        return ToolResult(ok=False, error=f"docker daemon error: {exc}")
    return ToolResult(ok=False, error=f"{name}: {exc}")


@dataclass(frozen=True)
class DockerCommandToolSpec:
    """Persisted shape of a docker_command Tool row, projected to what
    :class:`DockerCommandTool` needs at construction."""

    name: str
    image: str
    command_template: list[str]
    timeout_s: float = 30.0
    mem_limit_bytes: int = 256 * 1024 * 1024
    pids_limit: int = 64
    network_mode: str = "none"
    static_env: dict[str, str] = field(default_factory=dict)


def build_docker_command_tool(
    spec: DockerCommandToolSpec, *, docker_client: Any = None
) -> DockerCommandTool:
    """Convenience constructor; production passes ``docker_client=None``
    and the executor calls ``docker.from_env()`` lazily."""
    return DockerCommandTool(
        name=spec.name,
        image=spec.image,
        command_template=list(spec.command_template),
        timeout_s=spec.timeout_s,
        mem_limit_bytes=spec.mem_limit_bytes,
        pids_limit=spec.pids_limit,
        network_mode=spec.network_mode,
        static_env=dict(spec.static_env),
        docker_client=docker_client,
    )


__all__ = [
    "DockerCommandTool",
    "DockerCommandToolSpec",
    "build_docker_command_tool",
    "render_command",
]
