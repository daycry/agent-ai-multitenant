"""El DIFF de la tarea, para que el reviewer juzgue el cambio (`task_wf_60`).

El reviewer recibía ficheros ENTEROS cosechados del worktree, truncados a los 15
primeros. En una tarea que toca 12 líneas de un fichero de 800, el 98 % del
prompt es código que nadie tocó — y en una tarea que toca 30 ficheros, la mitad
del trabajo ni siquiera llega. Juzgar así es caro y además impreciso: el modelo
no puede citar «esta línea está mal» porque no sabe cuáles son nuevas.

Lo calcula el WORKER, no el sandbox. Es quien tiene el `data_root` y git; el
contenedor no tiene credenciales de git ni tiene por qué tenerlas (principio 2).
Se entrega ya hecho dentro del `review_context`, igual que el `<test-report>`.

Dos fuentes, en este orden, porque el momento del review no es siempre el mismo:

  1. **Trabajo sin commitear** en el worktree (`git diff HEAD`). Es el caso del
     primer review: el implementador acaba de dejar el worktree y su commit aún
     no ha ocurrido.
  2. **Los commits de ESTA tarea** en la rama del plan, localizados por el
     trailer `Task-Id` que la plataforma ya escribe en todos ellos. Es el caso
     del re-review tras un rechazo: el trabajo ya está commiteado y `git diff
     HEAD` saldría vacío.

Si ninguna da nada, se devuelve `None` y el reviewer sigue con la cosecha de
ficheros de siempre — que es lo correcto para los runs sin worktree (análisis,
diseño), donde el review en prosa funciona bien.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import structlog

_log = structlog.get_logger("workers.review_diff")

# Tope del bloque de diff en el prompt. Generoso frente al volcado de ficheros
# que sustituye (15 ficheros enteros son mucho más), pero acotado: un diff de
# megabytes desplazaría del prompt los criterios de aceptación, que es lo que el
# reviewer tiene que certificar.
MAX_DIFF_CHARS = 60_000


def _truncate(diff: str) -> str:
    if len(diff) <= MAX_DIFF_CHARS:
        return diff
    # Se corta por el FINAL y se dice: un diff a medias sin avisar haría que el
    # reviewer certificara sobre un cambio que no vio entero.
    return (
        diff[:MAX_DIFF_CHARS] + f"\n\n[... diff truncado a {MAX_DIFF_CHARS} caracteres —"
        " revisa el resto con read_file ...]"
    )


def _uncommitted_diff(worktree_path: str) -> str:
    from workers.git_repos import _run_git

    # `HEAD` cubre lo modificado Y lo ya indexado; `--no-color` para que el
    # prompt no lleve secuencias ANSI.
    out = _run_git("diff", "--no-color", "HEAD", cwd=Path(worktree_path))
    return str(out or "").strip()


def _task_commit_range(worktree_path: str, task_id: str) -> str:
    """El diff de los commits que llevan el trailer `Task-Id: <task_id>`."""
    from workers.git_repos import _run_git

    # `--format=%H` en orden cronológico inverso (el de `git log`): el primero
    # de la lista es el MÁS RECIENTE.
    raw = _run_git(
        "log",
        "--format=%H",
        f"--grep=Task-Id: {task_id}",
        "HEAD",
        cwd=Path(worktree_path),
    )
    shas = [line.strip() for line in str(raw or "").splitlines() if line.strip()]
    if not shas:
        return ""
    newest, oldest = shas[0], shas[-1]
    out = _run_git("diff", "--no-color", f"{oldest}^..{newest}", cwd=Path(worktree_path))
    return str(out or "").strip()


def compute_task_review_diff(worktree_path: str | None, task_id: str) -> str | None:
    """El diff que el reviewer debe juzgar, o ``None`` si no hay ninguno.

    Best-effort de principio a fin: cualquier fallo de git devuelve ``None`` y
    el review sigue con la cosecha de ficheros. Un diff es una AYUDA para
    juzgar mejor; no poder calcularlo no puede impedir que se juzgue.
    """
    if not worktree_path:
        return None
    path = worktree_path
    sources: tuple[tuple[str, Callable[[], str]], ...] = (
        ("uncommitted", lambda: _uncommitted_diff(path)),
        ("task_commits", lambda: _task_commit_range(path, task_id)),
    )
    for source, compute in sources:
        try:
            diff = compute()
        except Exception as exc:
            _log.warning(
                "review_diff.compute_failed",
                source=source,
                task_id=task_id,
                error=str(exc)[:200],
            )
            continue
        if diff:
            _log.info("review_diff.resolved", source=source, task_id=task_id, chars=len(diff))
            return _truncate(diff)
    return None
