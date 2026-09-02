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

**Todos los DIRECTORIOS versionados, con presupuesto.** La primera versión
publicaba sólo el primer nivel («para la decisión basta con la raíz»), y la
auditoría del 2026-09-01 midió lo que eso dejaba abierto: el mismo destrozo de
los 85 ficheros con una llamada por subdirectorio (`app/Config`,
`app/Controllers`…). Ahora viaja la lista de directorios (`git ls-tree -r -d`,
no los ficheros: en ese proyecto eran 5.192 ficheros y ~300 directorios fuera de
`vendor/`), y si no cabe en el env se recorta POR NIVELES, nunca a mitad de uno,
avisando: la protección degrada en profundidad, no en anchura, y el primer nivel
no se pierde jamás.

**Versionado no basta: además tiene que ser TRABAJO.** El criterio del ADR 0164
es «versionado = trabajo aceptado», y un directorio de dependencias que la propia
plataforma metió en la rama por accidente —un `commit_task` sin `.gitignore`— no
lo es aunque esté versionado. Esta lista RESTA esos accidentes, y sólo ésos: un
`vendor/` que una persona commiteó a propósito (Go, Laravel, Symfony…) es del
proyecto y se protege como cualquier otro árbol. El criterio, y por qué es la
autoría y no el nombre, está en :mod:`workers.dependency_dirs`.

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

#: Tope en bytes de la lista que viaja por el env del contenedor. Un directorio
#: ocupa ~30 B de media, así que 48 KiB dan para ~1.600: cubre entero cualquier
#: proyecto sin dependencias vendorizadas (el árbol del incidente tenía ~300
#: directorios fuera de `vendor/`). Cuando no cabe, :func:`_recortar_por_niveles`
#: quita niveles enteros empezando por el más profundo y lo registra.
_PRESUPUESTO_BYTES = 48 * 1024


def compute_tracked_paths(worktree_path: str | None) -> list[str]:
    """Los directorios versionados en la rama, a cualquier profundidad, o lista vacía.

    Vacía en los tres casos en que no hay nada que proteger o no se puede saber:
    run sin worktree, worktree sin commit todavía (proyecto vacío, primera tarea
    del plan) y fallo de git. Los directorios de dependencias que la propia
    plataforma versionó por accidente se restan, con todo lo que cuelga de
    ellos (:func:`_accidentes_de_la_plataforma`).
    """
    if not worktree_path:
        return []
    from workers.git_repos import GitCommandError, _run_git

    path = Path(worktree_path)
    try:
        # `-z` NO es cosmético: sin él git devuelve los nombres no-ASCII
        # C-quoted (`"\303\261andu"`), y el runtime compara contra rutas reales
        # del disco — esas entradas no casarían con nada y la protección se
        # perdería en silencio justo en los nombres acentuados. `-r -d`: todos
        # los directorios, y sólo los directorios.
        raw = _run_git("ls-tree", "-r", "-d", "--name-only", "-z", "HEAD", cwd=path)
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
    directorios = [entry for entry in raw.split("\0") if entry]
    accidentes = _accidentes_de_la_plataforma(path)
    fuera_de_accidentes = [d for d in directorios if not _bajo_alguno(d, accidentes)]
    entries, profundidad, recortado = _recortar_por_niveles(fuera_de_accidentes)
    _log.info(
        "tracked_paths.resolved",
        worktree=worktree_path,
        entries=len(entries),
        depth=profundidad,
        platform_accidents_excluded=sorted(accidentes),
    )
    if recortado:
        _log.warning(
            "tracked_paths.truncated_by_depth",
            worktree=worktree_path,
            directories=len(fuera_de_accidentes),
            kept=len(entries),
            depth_kept=profundidad,
            detail="la protección cubre hasta esa profundidad; más abajo no",
        )
    return entries


def _bajo_alguno(ruta: str, raices: frozenset[str]) -> bool:
    return ruta in raices or any(ruta.startswith(raiz + "/") for raiz in raices)


def _recortar_por_niveles(directorios: list[str]) -> tuple[list[str], int, bool]:
    """Quita niveles enteros, del más profundo hacia arriba, hasta caber en el env.

    Devuelve ``(lista, profundidad máxima conservada, se recortó)``. El primer
    nivel se conserva siempre: es el que cubre el incidente medido, y un env que
    no pueda con él tiene un problema más grave que esta lista.
    """
    por_nivel: dict[int, list[str]] = {}
    for d in directorios:
        por_nivel.setdefault(d.count("/") + 1, []).append(d)
    conservados: list[str] = []
    ocupado = 0
    profundidad = 0
    for nivel in sorted(por_nivel):
        coste = sum(len(d.encode("utf-8")) + 1 for d in por_nivel[nivel])
        if nivel > 1 and ocupado + coste > _PRESUPUESTO_BYTES:
            return conservados, profundidad, True
        conservados.extend(por_nivel[nivel])
        ocupado += coste
        profundidad = nivel
    return conservados, profundidad, False


def _accidentes_de_la_plataforma(path: Path) -> frozenset[str]:
    """Los directorios de dependencias que la PLATAFORMA versionó, a cualquier profundidad.

    **Por qué hay que restarlos y no basta con que no lleguen a versionarse.**
    Esta lista se calcula en la PROVISIÓN, desde el HEAD de la rama; el
    des-versionado que los saca del índice ocurre al FINAL, en `commit_task`. En
    una rama que ya se llevó `vendor/` por delante —1.151 ficheros, plan
    `01a059db` de mediapro, medido el 2026-09-01— eso deja una ejecución entera
    en la que `vendor` sigue estando en HEAD: sin esta resta,
    `file_delete('vendor', recursive)` seguiría respondiendo «refusing to
    recursively delete 'vendor': it is tracked in this branch» y la tarea
    seguiría sin poder andamiar.

    **Y sólo los accidentes.** Un `vendor/` que commiteó una persona NO se resta:
    es trabajo del proyecto (auditoría 2026-09-01, reproducido con un proyecto Go
    que vendoriza a propósito). El criterio de autoría vive en
    :func:`workers.dependency_dirs.clasificar_versionados`, el mismo que usa
    `commit_task` para decidir qué des-versiona: una sola decisión, tomada en un
    solo sitio, leída por los dos.

    Degradado CONSERVADOR: si el catálogo o git no contestan, no se resta nada y
    se protege de MÁS. Pasarse cuesta que una tarea se queje de no poder borrar
    `vendor/`; quedarse corto costó los 85 ficheros de `app/` del 2026-08-31.
    """
    try:
        from workers import dependency_dirs

        clasificacion = dependency_dirs.clasificar_versionados(path, dependency_dirs.nombres())
    except Exception as exc:  # catálogo ausente, manifiesto de release ilegible…
        _log.warning(
            "tracked_paths.dependency_dirs_unavailable",
            error=str(exc)[:200],
            detail="no se resta nada: se protege de más, nunca de menos",
        )
        return frozenset()
    return frozenset(clasificacion.accidentes)


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
