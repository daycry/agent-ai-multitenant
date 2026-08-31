"""Qué parte del workspace está VERSIONADA (contrato ``AGENT_TRACKED_PATHS``).

El 2026-08-31, en el proyecto «Hello World CI4 v3» del tenant mediapro, un agente
ejecutó `delete_file` recursivo sobre `app/` y borró **85 ficheros** que eran el
entregable ya commiteado de la tarea anterior. No fue mala fe: desde dentro del
sandbox `app/` y `vendor/` son indistinguibles. El ADR 0163 esconde el `.git` del
worktree mientras corre el agente —porque dentro es inútil y estorba a los
andamiadores que exigen directorio vacío—, así que el runtime no tiene a quién
preguntar si un árbol es un entregable versionado o un artefacto reconstruible.

El WORKER sí lo sabe: es el único punto que tiene worktree y git a la vez, igual
que con el diff del reviewer (:mod:`workers.review_diff`). Calcula la lista aquí
y la entrega ya hecha en el env del contenedor; al sandbox no se le da git
(principio 2).

**Sólo el PRIMER NIVEL.** En ese mismo proyecto el árbol versionado eran 5.192
ficheros: el env de un contenedor no es sitio para eso, y para la decisión que el
runtime tiene que tomar —«¿este directorio que me piden borrar entero es un
entregable?»— basta con la raíz. Un `git ls-tree` no recursivo es además una
sola lectura de árbol, no un recorrido del índice.

Best-effort de principio a fin: cualquier fallo devuelve la lista vacía, que el
runtime interpreta como «sin protección nueva» (compat hacia atrás). Perder la
protección es malo; tumbar el run por no poder calcularla sería peor.
"""

from __future__ import annotations

from pathlib import Path

import structlog

_log = structlog.get_logger("workers.tracked_paths")

# El nombre de la variable de entorno ES el contrato con el runtime, que vive en
# otro repo-dentro-del-repo (docker/agent-runtimes/agent-runtime/). Se declara
# aquí para que el productor tenga un único sitio donde escribirlo.
TRACKED_PATHS_ENV = "AGENT_TRACKED_PATHS"


def compute_tracked_top_level_paths(worktree_path: str | None) -> list[str]:
    """Las entradas de primer nivel versionadas en la rama, o lista vacía.

    Vacía en los tres casos en que no hay nada que proteger o no se puede saber:
    run sin worktree, worktree sin commit todavía (proyecto vacío, primera tarea
    del plan) y fallo de git.
    """
    if not worktree_path:
        return []
    from workers.git_repos import GitCommandError, _run_git

    path = Path(worktree_path)
    try:
        # `-z` NO es cosmético: sin él git devuelve los nombres no-ASCII
        # C-quoted (`"\303\261andu.txt"`), y el runtime compara contra rutas
        # reales del disco — esas entradas no casarían con ningún fichero y la
        # protección se perdería en silencio justo en los nombres acentuados.
        raw = _run_git("ls-tree", "--name-only", "-z", "HEAD", cwd=path)
    except GitCommandError as exc:
        _log_git_failure(path, str(exc))
        return []
    except Exception as exc:  # timeout, OSError, git ausente…
        _log.warning(
            "tracked_paths.compute_failed",
            worktree=worktree_path,
            error=str(exc)[:200],
            detail="el run sigue SIN protección de rutas versionadas",
        )
        return []
    entries = [entry for entry in raw.split("\0") if entry]
    _log.info("tracked_paths.resolved", worktree=worktree_path, entries=len(entries))
    return entries


def _log_git_failure(path: Path, error: str) -> None:
    """Un worktree sin commit no es una avería; cualquier otro fallo de git sí.

    La distinción es sólo de nivel de log, pero importa: el caso «primera tarea
    de un plan sobre un proyecto vacío» es NORMAL y ocurre en cada plan nuevo.
    Avisarlo como problema entrenaría a quien lee los logs a ignorar el aviso
    que sí lo es (un `.git` que ya no está, un data_root a medio montar).
    """
    if _es_repo_sin_commit(path):
        _log.info("tracked_paths.worktree_sin_commit", worktree=str(path))
        return
    _log.warning(
        "tracked_paths.git_failed",
        worktree=str(path),
        error=error[:200],
        detail="el run sigue SIN protección de rutas versionadas",
    )


def _es_repo_sin_commit(path: Path) -> bool:
    """True si es un repo git al que aún no le han hecho el primer commit.

    Se paga sólo en el camino de fallo: el camino feliz sigue siendo una única
    llamada a git.
    """
    from workers.git_repos import GitCommandError, _run_git

    try:
        _run_git("rev-parse", "--git-dir", cwd=path)
    except Exception:
        # Ni siquiera es un repo — eso NO es el caso normal del proyecto vacío.
        return False
    try:
        _run_git("rev-parse", "--verify", "--quiet", "HEAD", cwd=path)
    except GitCommandError:
        return True
    except Exception:
        return False
    return False
