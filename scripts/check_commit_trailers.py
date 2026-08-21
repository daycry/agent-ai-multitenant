#!/usr/bin/env python
"""Hook `commit-msg`: exige `Plan-Id`/`Task-Id` en los commits de tareas de plan.

Plan prod-15 `task_gov_trailers_09`, decisión **D2 opción B**.

El problema (hallazgo quality-9): `docs/context/conventions.md` declaraba los
trailers "obligatorios" y la práctica decía otra cosa — medido el 2026-07-29,
**643 de 1460** commits no-merge los llevan (44 %). Una regla que se incumple la
mitad de las veces no es una regla, es decoración; y la decoración normativa es
peor que nada, porque enseña que las reglas del repo se pueden ignorar.

La política real, ahora también la escrita:

* rama `plan/*`  → **obligatorios** `Plan-Id` y `Task-Id` (es el trabajo de una
  tarea de plan: sin trailers no hay trazabilidad tarea↔commit, que es la que
  usa el sistema para poblar el PR del plan);
* cualquier otra rama → **opcionales** (mantenimiento);
* merges, `Revert`, `fixup!`/`squash!`/`amend!` → exentos siempre (el mensaje
  no lo redacta una tarea, o se reescribe al rebasar).

Uso::

    python scripts/check_commit_trailers.py .git/COMMIT_EDITMSG
    python scripts/check_commit_trailers.py MSG --branch plan/foo   # para tests
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

#: Trailers exigidos en rama de plan. `Execution-Id` NO se exige: solo existe
#: cuando el commit lo produjo una ejecución de agente, y `conventions.md` ya
#: admite `Plan-Id` + `Task-Id` para planes documentales o de tooling.
REQUIRED_TRAILERS = ("Plan-Id", "Task-Id")

#: Prefijos de mensaje exentos. `fixup!`/`squash!`/`amend!` heredan el mensaje
#: del commit destino al rebasar; `Merge`/`Revert` los redacta git.
_EXEMPT_PREFIXES = ("merge ", "revert", "fixup!", "squash!", "amend!")

_PLAN_BRANCH = re.compile(r"^plan/")


def current_branch() -> str:
    """Rama actual, o `HEAD` si no se puede saber (detached, sin git…)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "HEAD"
    return out.stdout.strip() or "HEAD"


def _has_trailer(message: str, name: str) -> bool:
    """¿Aparece `name:` como TRAILER (inicio de línea), no como prosa?

    La distinción importa: si valiera cualquier aparición del texto, escribir
    "hablo del Plan-Id" en el cuerpo colaría y el hook sería decorativo.
    """
    return re.search(rf"^{re.escape(name)}:\s*\S+", message, re.M) is not None


def check_message(message: str, *, branch: str) -> tuple[bool, str]:
    """`(ok, motivo)`. `ok=True` cuando el commit puede pasar."""
    stripped = message.lstrip().lower()
    if stripped.startswith(_EXEMPT_PREFIXES):
        return True, "mensaje exento (merge/revert/fixup/squash/amend)"

    if not _PLAN_BRANCH.match(branch):
        return True, f"rama `{branch}` no es de plan: trailers opcionales"

    missing = [name for name in REQUIRED_TRAILERS if not _has_trailer(message, name)]
    if missing:
        return False, (
            f"rama `{branch}` es de plan y al mensaje le faltan trailers: "
            f"{', '.join(missing)}.\n"
            f"Requeridos en ramas `plan/*`: {', '.join(REQUIRED_TRAILERS)}.\n"
            "Ejemplo:\n"
            "    Plan-Id: 06.8-rbac-enforcement\n"
            "    Task-Id: task_06_8_03\n"
            "En mantenimiento (master, work/*, hotfix/*) son opcionales — ver "
            "docs/context/conventions.md §Commits."
        )
    return True, "trailers presentes"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message_file", help="ruta al fichero del mensaje de commit")
    parser.add_argument(
        "--branch",
        default=None,
        help="rama a evaluar (por defecto, la actual). Solo para tests.",
    )
    args = parser.parse_args(argv)

    try:
        with open(args.message_file, encoding="utf-8") as handle:
            message = handle.read()
    except OSError as exc:  # pragma: no cover - ruta de error de git
        print(f"check_commit_trailers: no pude leer {args.message_file}: {exc}")
        return 1

    ok, reason = check_message(message, branch=args.branch or current_branch())
    if not ok:
        print(f"commit-msg: {reason}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
