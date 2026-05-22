"""human_02_02 — el aislamiento del contenedor es real.

Lanza un contenedor bajo el MISMO perfil endurecido que el worker aplica
a cada agent-runtime (cap-drop ALL, FS raíz read-only, red interna sin
salida, sin socket Docker, seccomp) y ejecuta sondas desde dentro.
Imprime un checklist de aislamiento.

Uso (con el venv, desde la raíz del repo):

    .venv/Scripts/python scripts/demo_human_02_02.py

Requiere Docker. La imagen `python:3.12-slim` (base de agent-runtime)
suele estar ya en caché; el perfil de aislamiento es el mismo sea cual
sea la imagen — lo que se verifica es el sandbox, no el agent loop.
"""

from __future__ import annotations

import json
import sys

from _demo_common import banner, check

# Sonda que corre DENTRO del contenedor: comprueba el aislamiento y emite
# una línea JSON con los resultados.
_PROBE = r"""
import json, os, socket
r = {}
# 1) El socket del daemon Docker no está montado.
r["no_docker_sock"] = not os.path.exists("/var/run/docker.sock")
# 2) El FS raíz es read-only: no se puede escribir fuera de /workspace.
try:
    with open("/probe-fuera.txt", "w") as fh:
        fh.write("x")
    r["root_readonly"] = False
except OSError:
    r["root_readonly"] = True
# 3) /workspace SÍ es escribible (tmpfs efímero).
try:
    with open("/workspace/probe.txt", "w") as fh:
        fh.write("x")
    r["workspace_writable"] = True
except OSError:
    r["workspace_writable"] = False
# 4) PID namespace propio: solo se ven los procesos del contenedor.
pids = [p for p in os.listdir("/proc") if p.isdigit()]
r["pids_visibles"] = len(pids)
r["pid_namespace_aislado"] = len(pids) < 20
# 5) Red interna: no hay salida a internet.
try:
    socket.create_connection(("1.1.1.1", 53), timeout=4).close()
    r["sin_egress"] = False
except OSError:
    r["sin_egress"] = True
print(json.dumps(r))
"""


def main() -> int:
    from workers.config import Settings
    from workers.container import AgentContainerRunner, ContainerSpec

    banner("human_02_02 — aislamiento del contenedor agent-runtime")
    print("  Lanzando un contenedor bajo el perfil endurecido del worker...")
    print()

    result = AgentContainerRunner(Settings()).run(
        ContainerSpec(image="python:3.12-slim", command=["python", "-c", _PROBE]),
        timeout=60,
    )
    line = next((ln for ln in result.logs.splitlines() if ln.strip().startswith("{")), None)
    if line is None:
        print("  No se obtuvo salida de la sonda. Logs del contenedor:")
        print(result.logs or "    (sin logs)")
        return 1
    r = json.loads(line)

    ok = True
    ok &= check("No existe /var/run/docker.sock dentro del contenedor", r["no_docker_sock"])
    ok &= check(
        "El FS raíz es read-only — no se puede escribir fuera de /workspace",
        r["root_readonly"],
    )
    ok &= check("/workspace sí es escribible (tmpfs efímero)", r["workspace_writable"])
    ok &= check(
        "PID namespace aislado — no se ven procesos del host",
        r["pid_namespace_aislado"],
        f"{r['pids_visibles']} procesos visibles",
    )
    ok &= check("Red interna — sin salida a internet desde el contenedor", r["sin_egress"])

    print()
    print("  RESULTADO:", "aislamiento verificado" if ok else "REVISAR — alguna sonda falló")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # - script de demo: errores legibles
        print(f"\n  ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("  ¿Está el daemon de Docker levantado?", file=sys.stderr)
        sys.exit(1)
