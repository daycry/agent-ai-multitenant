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

**Versionado no basta: además tiene que ser TRABAJO.** El criterio del ADR 0164
es «versionado = trabajo aceptado por una tarea anterior», y un directorio de
dependencias no lo es aunque esté versionado — de hecho la plataforma ya lo
afirma en otros dos sitios (`sync_to_head(preserve=...)` y la exclusión del
`git add -A` de `commit_task`). Por eso esta lista los RESTA. Ver
:func:`_directorios_de_dependencias`.

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
    dependencias = _directorios_de_dependencias()
    entries = [entry for entry in raw.split("\0") if entry and entry not in dependencias]
    _log.info("tracked_paths.resolved", worktree=worktree_path, entries=len(entries))
    return entries


def _directorios_de_dependencias() -> frozenset[str]:
    """Los directorios que NUNCA son trabajo aceptado, estén versionados o no.

    **Por qué hay que restarlos y no basta con que no lleguen a versionarse.**
    Esta lista se calcula en la PROVISIÓN, desde el HEAD de la rama; el
    des-versionado que los saca del índice ocurre al FINAL, en `commit_task`. En
    una rama que ya se llevó `vendor/` por delante —1.151 ficheros, plan
    `01a059db` de mediapro, medido el 2026-09-01— eso deja una ejecución entera
    en la que `vendor` sigue estando en HEAD: sin esta resta,
    `file_delete('vendor', recursive)` seguiría respondiendo «refusing to
    recursively delete 'vendor': it is tracked in this branch» y la tarea
    seguiría sin poder andamiar. Comprobado con la lista que tendría esa
    ejecución siguiente.

    **No es una lista nueva escrita a mano.** Es la MISMA declaración por runtime
    del catálogo (`vendor` en los php, `node_modules` en los node, `.venv`/`venv`
    en python) que la plataforma ya usa dos veces: `sync_to_head(preserve=...)`
    la respeta al limpiar el worktree entre tareas, y la exclusión del
    `git add -A` de `plan_git.commit_task` impide que vuelvan a entrar. Una
    tercera copia a mano sería la forma de que se desincronizara de las otras dos
    sin que nada avisara.

    Import perezoso y degradado CONSERVADOR: si el catálogo no se puede leer se
    devuelve el conjunto vacío, o sea que no se resta nada y se protege de MÁS.
    Pasarse cuesta que una tarea se queje de no poder borrar `vendor/`; quedarse
    corto costó los 85 ficheros de `app/` del 2026-08-31.
    """
    try:
        from shared_test_runtimes import catalog as runtime_catalog

        return frozenset(runtime_catalog.dependency_dirs())
    except Exception as exc:  # catálogo ausente, manifiesto de release ilegible…
        _log.warning(
            "tracked_paths.dependency_dirs_unavailable",
            error=str(exc)[:200],
            detail="no se resta nada: se protege de más, nunca de menos",
        )
        return frozenset()


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
