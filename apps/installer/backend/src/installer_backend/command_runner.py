"""Subprocess seam for the installer's host-touching steps (Plan prod-01 task_16).

The real ``StepExecutor`` / ``StackTeardown`` / ``DataPurger`` shell out to
``docker compose``. Hiding that behind the :class:`CommandRunner` Protocol lets
the executors be unit-tested with :class:`FakeCommandRunner` — which records the
exact argv + cwd and fabricates returncodes/output — so the orchestration (order
of ``pull`` → ``up`` → ``run migrations``, the ``-f``/``-p`` flags, fail
propagation) is verified WITHOUT a Docker daemon. The real
:class:`SubprocessRunner` is exercised only by the e2e / human tests.
"""

from __future__ import annotations

import subprocess  # we never use shell=True; argv is always a list
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CommandResult:
    """Outcome of a command: its exit code + the combined stdout/stderr lines.

    ``output_lines`` is already combined (stdout+stderr) and is meant to be
    secret-free — the caller emits it as progress / log lines.
    """

    returncode: int
    output_lines: tuple[str, ...] = ()


@runtime_checkable
class CommandRunner(Protocol):
    """Runs a command (argv list, never a shell string) and returns its result.

    ``on_line`` receives each output line as it is produced (streaming), so the
    installer can surface ``docker compose pull`` progress live.
    """

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult: ...


@dataclass
class SubprocessRunner:
    """Real runner: streams a child process's combined output. Host-only.

    NEVER uses ``shell=True`` — argv is always a list, so no shell injection.
    A missing binary (``docker`` not on PATH) is turned into a non-zero
    :class:`CommandResult` (returncode 127) rather than a raw ``OSError``, so the
    caller fails loud with an actionable message instead of crashing.
    """

    def run(  # pragma: no cover - host-only, exercised by the e2e/human tests
        self,
        args: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        import os

        full_env = {**os.environ, **(env or {})}
        lines: list[str] = []
        try:
            proc = subprocess.Popen(
                list(args),
                cwd=cwd,
                env=full_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except (FileNotFoundError, OSError) as exc:
            msg = f"comando no encontrado o no ejecutable: {args[0]!r} ({exc})"
            if on_line is not None:
                on_line(msg)
            return CommandResult(returncode=127, output_lines=(msg,))

        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                line = raw.rstrip("\n")
                lines.append(line)
                if on_line is not None:
                    on_line(line)
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            timeout_msg = f"timeout tras {timeout}s: {' '.join(args)}"
            lines.append(timeout_msg)
            if on_line is not None:
                on_line(timeout_msg)
            return CommandResult(returncode=124, output_lines=tuple(lines))
        return CommandResult(returncode=returncode, output_lines=tuple(lines))


@dataclass
class FakeCommandRunner:
    """Test runner: records argv/cwd, fabricates returncodes/output. No subprocess.

    * ``responses`` maps an exact argv tuple → a scripted :class:`CommandResult`.
    * ``fail_on`` is an argv PREFIX that resolves to a non-zero result.
    * ``calls`` / ``cwds`` record every invocation IN ORDER (the central
      assertion for the executor/teardown tests).
    """

    responses: dict[tuple[str, ...], CommandResult] = field(default_factory=dict)
    fail_on: tuple[str, ...] | None = None
    calls: list[tuple[str, ...]] = field(default_factory=list)
    cwds: list[str | None] = field(default_factory=list)

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,  # noqa: ARG002 - Protocol parity
        timeout: int | None = None,  # noqa: ARG002 - Protocol parity
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        argv = tuple(args)
        self.calls.append(argv)
        self.cwds.append(cwd)

        if self.fail_on is not None and argv[: len(self.fail_on)] == self.fail_on:
            result = CommandResult(
                returncode=1,
                output_lines=(f"<fallo simulado: {' '.join(argv)}>",),
            )
        else:
            result = self.responses.get(
                argv, CommandResult(returncode=0, output_lines=(f"<simulado: {' '.join(argv)}>",))
            )

        if on_line is not None:
            for line in result.output_lines:
                on_line(line)
        return result
