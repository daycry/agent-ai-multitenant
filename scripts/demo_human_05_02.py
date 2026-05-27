"""Demo: human_05_02 — aislamiento de tools docker_command.

Lee el state que `setup_demo_05.py` dejó sembrado (un Tool de tipo
`docker_command` wired al agente "Sandbox Runner") y:

  1. Lanza un `DockerCommandTool` real contra `python:3.12-alpine`
     con el envelope de hardening del platform.
  2. Reporta uid + writability del root + writability de /tmp +
     conectividad de red (network=none debe bloquearla).
  3. Verifica que el container se elimina al exit (remove=True).
  4. Te dice qué URL del admin-panel abrir para ver la Tool wired
     al agente correspondiente.

Uso:

    .venv/Scripts/python scripts/demo_human_05_02.py

Requisitos: Docker daemon corriendo. La imagen python:3.12-alpine
se pulla al vuelo (≈50MB) o se reutiliza si ya está cacheada.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STATE_FILE = _REPO_ROOT / "scripts" / ".demo_state_05.json"
_ADMIN_URL = os.environ.get("DEMO_ADMIN_URL", "http://localhost:3000")


def _banner(text: str) -> None:
    bar = "-" * 60
    print(f"\n{bar}\n  {text}\n{bar}", flush=True)


def _check(ok: bool, label: str, detail: str = "") -> bool:
    mark = "[ OK ]" if ok else "[FAIL]"
    print(f"  {mark} {label}" + (f" - {detail}" if detail else ""))
    return ok


def _load_state() -> dict[str, str] | None:
    if not _STATE_FILE.exists():
        return None
    return json.loads(_STATE_FILE.read_text(encoding="utf-8"))


def _count_alpine_containers() -> int:
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

    _banner("demo human_05_02 - docker_command isolation")

    state = _load_state()
    if state is None:
        print("FAIL — no state file. Lanza primero:")
        print("       .venv/Scripts/python scripts/setup_demo_05.py")
        return 1
    project_id = state["project_id"]
    agent_docker_id = state["agent_docker_id"]
    tool_docker_id = state["tool_docker_id"]

    print("\n=> Tool wired al agente Sandbox Runner")
    print(f"     project_id : {project_id}")
    print(f"     agent_id   : {agent_docker_id}")
    print(f"     tool_id    : {tool_docker_id}")

    pre = _count_alpine_containers()
    if pre < 0:
        print("\nFAIL - `docker` command no esta en PATH o daemon no responde")
        print("       Arranca Docker Desktop y reintenta.")
        return 1
    print(f"\n=> pre-run alpine containers (cualquier estado): {pre}")

    # Run a probe inside python:3.12-alpine.
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

    print("\n=> lanzando python:3.12-alpine con el envelope del platform")
    tool = DockerCommandTool(
        name="alpine-probe",
        image="python:3.12-alpine",
        command_template=["python", "-c", probe],
        timeout_s=60.0,
    )
    result = tool({})
    if not result.ok:
        print(f"FAIL - tool returned error: {result.error}")
        return 1
    print(f"     container output:\n     {result.output.strip()}")

    try:
        probe_data = eval(result.output.strip(), {"__builtins__": {}}, {})
    except Exception as exc:
        print(f"FAIL - could not parse probe output: {exc}")
        return 1

    print("\n=> checks del envelope de seguridad:")
    ok_uid = _check(probe_data["uid"] == 1000, "uid != root (1000:1000)")
    ok_ro = _check(probe_data["cwd_writable"] is False, "root fs es read-only")
    ok_tmp = _check(probe_data["tmp_writable"] is True, "/tmp es writable (tmpfs)")
    ok_net = _check(
        "blocked" in str(probe_data["net"]),
        "network=none bloquea egress",
        str(probe_data["net"]),
    )

    print("\n=> ephemeral check: container eliminado al salir")
    post = _count_alpine_containers()
    print(f"     post-run alpine containers: {post}")
    ok_eph = _check(post <= pre, "no quedan containers leftover")

    _banner("Que abrir en el admin-panel ahora")
    print("\n  Panel diagnostico (read-only - solo se mira, no hay botones):")
    print(f"     {_ADMIN_URL}/admin/projects/{project_id}/agent-tools-diagnostic")
    print("\n  Que tienes que VER en esa pagina (sin hacer click en nada):")
    print("\n    1) Card 'MCP servers del proyecto' al inicio")
    print("       lista 'toy-mcp' con badge 'stdio'.")
    print("\n    2) Card del agente 'Sandbox Runner'")
    print("       (badge azul 'devops', badge gris 'project_local').")
    print("       Dentro, fila para el tool 'alpine-probe' con:")
    print("         - badge rojo  'docker_command'    (implementation_type)")
    print("         - badge rojo  'privileged'         (security_level)")
    print("         - texto       'timeout 60s . category code'")
    print("\n    3) Card del agente 'HTTP Lookup Bot' tambien aparece")
    print("       (la cubrira el demo 05_03 - es el sitio de 'example-weather').")

    all_ok = all([ok_uid, ok_ro, ok_tmp, ok_net, ok_eph])
    if all_ok:
        _banner("demo human_05_02 PASSED")
        return 0
    _banner("demo human_05_02 - revisa items FAIL arriba")
    return 1


if __name__ == "__main__":
    sys.exit(main())
