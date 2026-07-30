"""Gate mypy de pre-commit con el ENTORNO DEL PROYECTO (mypy-total, 2026-07-08).

El hook `mirrors-mypy` corría en un venv aislado que no veía los paquetes
hermanos editables (api_server, workers, orchestrator, agent_runtime,
shared_*), así que el corazón del pipeline vivía EXCLUIDO del gate por path
(ver docs/03-guides/gotchas/mypy-local-package-imports.md). Este wrapper corre
mypy con un intérprete que sí resuelve todo el workspace:

  1. el intérprete actual (CI instala el workspace en el python del job);
  2. el `.venv` del repo como fallback (desarrollo local, commits desde un
     shell sin el venv activado).

Chequea SIEMPRE el árbol completo (pass_filenames: false) — el caché
incremental de mypy (.mypy_cache) hace baratas las pasadas siguientes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_TARGETS = ["apps", "packages", "docker/agent-runtimes/agent-runtime/agent_runtime"]


def _has_mypy(python: str) -> bool:
    probe = subprocess.run(
        [python, "-c", "import mypy"],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


def main() -> int:
    candidates = [sys.executable]
    for venv_python in (_REPO / ".venv/Scripts/python.exe", _REPO / ".venv/bin/python"):
        if venv_python.exists():
            candidates.append(str(venv_python))
    for python in candidates:
        if _has_mypy(python):
            return subprocess.run(
                [python, "-m", "mypy", "--config-file=pyproject.toml", *_TARGETS],
                cwd=_REPO,
                check=False,
            ).returncode
    sys.stderr.write(
        "mypy_gate: mypy no está instalado ni en el intérprete actual ni en .venv "
        "— instala el entorno dev (pip install -e apps/... + mypy).\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
