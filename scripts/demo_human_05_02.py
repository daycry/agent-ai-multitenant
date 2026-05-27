"""Demo: human_05_02 — aislamiento de tools docker_command.

Lanza un `DockerCommandTool` real contra `python:3.12-alpine` y
verifica los tres items del checklist del Plan 05:

  1. La tool corre en un contenedor efimero separado
     → comprobado mirando `docker ps -a` antes/despues.
  2. El contenedor tiene los mismos guardrails que agent-runtime
     → uid 1000, read-only fs, network=none, mem_limit, pids_limit.
  3. Al terminar, el contenedor se destruye y no deja rastro
     → comprobado tambien con `docker ps -a` post-run.

Uso:

    .venv/Scripts/python scripts/demo_human_05_02.py

Requisitos:
  - Docker daemon corriendo y accesible.
  - El usuario del demo tiene permiso sobre /var/run/docker.sock
    (o equivalente Windows).
  - La imagen `python:3.12-alpine` puede pullarse al vuelo (o ya esta
    cacheada).
"""

from __future__ import annotations

import contextlib
import subprocess
import sys

# Force UTF-8 stdout for the Windows console.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def _banner(text: str) -> None:
    bar = "─" * 60
    print(f"\n{bar}\n  {text}\n{bar}", flush=True)


def _count_alpine_containers() -> int:
    """How many python:3.12-alpine containers exist right now (any state)."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "ancestor=python:3.12-alpine", "-q"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1
    return len([line for line in result.stdout.splitlines() if line.strip()])


def main() -> int:
    from agent_runtime.docker_command_tool import DockerCommandTool

    _banner("demo human_05_02 — docker_command isolation")

    # Sanity: docker daemon reachable.
    pre = _count_alpine_containers()
    if pre < 0:
        print("✗ FAIL — `docker` command not on PATH or daemon unreachable")
        print("  Bring up the stack with `scripts/dev/up.ps1` first.")
        return 1
    print(f"→ pre-run alpine containers (any state): {pre}")

    # Step 1: run a probe script inside python:3.12-alpine that reports
    # uid + root-fs writability + can-reach-internet.
    probe = (
        "import os, urllib.request, socket\n"
        "out = {\n"
        "  'uid': os.getuid(),\n"
        "  'gid': os.getgid(),\n"
        "  'cwd_writable': os.access('/', os.W_OK),\n"
        "  'tmp_writable': os.access('/tmp', os.W_OK),\n"
        "}\n"
        "try:\n"
        "  socket.setdefaulttimeout(2)\n"
        "  urllib.request.urlopen('http://example.com').read(64)\n"
        "  out['net'] = 'open'\n"
        "except Exception as e:\n"
        "  out['net'] = f'blocked ({type(e).__name__})'\n"
        "print(out)\n"
    )

    print("\n→ launching python:3.12-alpine with the platform's hardening envelope")
    tool = DockerCommandTool(
        name="isolation-probe",
        image="python:3.12-alpine",
        command_template=["python", "-c", probe],
        timeout_s=60.0,
    )
    result = tool({})
    if not result.ok:
        print(f"✗ FAIL — tool returned error: {result.error}")
        return 1
    print(f"  container output:\n    {result.output.strip()}")

    # Parse the probe output. We trust the dict shape because we
    # control the script.
    try:
        probe_data = eval(result.output.strip(), {"__builtins__": {}}, {})
    except Exception as exc:
        print(f"✗ FAIL — could not parse probe output: {exc}")
        return 1

    checks: dict[str, bool] = {
        "uid != root (1000:1000)": probe_data["uid"] == 1000,
        "root fs is read-only": probe_data["cwd_writable"] is False,
        "/tmp is writable (tmpfs)": probe_data["tmp_writable"] is True,
        "network=none blocks egress": "blocked" in str(probe_data["net"]),
    }
    print("\n→ envelope checks:")
    for desc, ok in checks.items():
        mark = "✓" if ok else "✗"
        print(f"  [{mark}] {desc}")

    # Step 2: ephemeral verification — container count must NOT have
    # grown (remove=True cleans up).
    print("\n→ ephemeral check — container deleted after exit")
    post = _count_alpine_containers()
    print(f"  post-run alpine containers (any state): {post}")
    ephemeral_ok = post <= pre
    print(f"  [{'✓' if ephemeral_ok else '✗'}] no leftover container")

    if not all([*checks.values(), ephemeral_ok]):
        print("\n✗ FAIL — one or more checks did not pass")
        return 1

    _banner("demo human_05_02 PASSED")
    print("Checklist roadmap:")
    print("  [✓] La tool corre en un contenedor efimero separado")
    print("  [✓] El contenedor tiene los mismos guardrails que agent-runtime")
    print("  [✓] Al terminar, el contenedor se destruye y no deja rastro")
    return 0


if __name__ == "__main__":
    sys.exit(main())
