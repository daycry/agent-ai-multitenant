"""El diff de la tarea que recibe el reviewer (`task_wf_60`).

El reviewer juzgaba sobre ficheros ENTEROS cosechados del worktree y truncados a
los 15 primeros. En una tarea que toca 12 líneas de un fichero de 800, el 98 %
del prompt es código que nadie tocó; en una que toca 30 ficheros, la mitad del
trabajo ni llegaba. Y sin saber qué líneas son nuevas, el veredicto no puede
citar nada concreto.

El diff lo calcula el WORKER —es quien tiene `data_root` y git— y se entrega ya
hecho, igual que el `<test-report>`: al sandbox no se le da git (principio 2).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from workers.review_diff import MAX_DIFF_CHARS, compute_task_review_diff

pytestmark = pytest.mark.unit

_TASK_ID = "11111111-2222-3333-4444-555555555555"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": __import__("os").environ.get("PATH", ""),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    work = tmp_path / "worktree"
    work.mkdir()
    _git("init", "-q", "-b", "main", cwd=work)
    (work / "app.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-qm", "base", cwd=work)
    return work


def test_uncommitted_work_is_what_the_first_review_judges(repo: Path) -> None:
    # El caso del primer review: el implementador acaba de dejar el worktree y
    # su commit aún no ha ocurrido. `git diff HEAD` es todo lo que hay.
    (repo / "app.py").write_text("def a():\n    return 2\n", encoding="utf-8")

    diff = compute_task_review_diff(str(repo), _TASK_ID)

    assert diff is not None
    assert "-    return 1" in diff
    assert "+    return 2" in diff


def test_committed_work_is_found_by_the_task_trailer(repo: Path) -> None:
    # El caso del RE-review tras un rechazo: el trabajo ya está commiteado, así
    # que `git diff HEAD` sale vacío y sin esta segunda fuente el reviewer no
    # vería nada. Los commits de la tarea se localizan por el trailer `Task-Id`
    # que la plataforma ya escribe en todos ellos.
    (repo / "app.py").write_text("def a():\n    return 3\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", f"feat: algo\n\nTask-Id: {_TASK_ID}", cwd=repo)

    diff = compute_task_review_diff(str(repo), _TASK_ID)

    assert diff is not None
    assert "+    return 3" in diff


def test_another_tasks_commits_are_not_in_the_diff(repo: Path) -> None:
    # Un plan es una rama compartida: si el diff arrastrara los commits de las
    # tareas hermanas, el reviewer rechazaría por trabajo que no es de esta
    # tarea — y no tendría forma de saberlo.
    (repo / "otro.py").write_text("x = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git(
        "commit",
        "-qm",
        "feat: de otra tarea\n\nTask-Id: 99999999-0000-0000-0000-000000000000",
        cwd=repo,
    )
    (repo / "app.py").write_text("def a():\n    return 4\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", f"feat: la mía\n\nTask-Id: {_TASK_ID}", cwd=repo)

    diff = compute_task_review_diff(str(repo), _TASK_ID)

    assert diff is not None
    assert "return 4" in diff
    assert "otro.py" not in diff


def test_uncommitted_wins_over_the_committed_range(repo: Path) -> None:
    # Si hay las dos cosas, lo que el reviewer tiene que juzgar es el estado
    # ACTUAL del worktree: es lo que el implementador acaba de dejar.
    _git("commit", "-qm", f"feat: viejo\n\nTask-Id: {_TASK_ID}", "--allow-empty", cwd=repo)
    (repo / "app.py").write_text("def a():\n    return 99\n", encoding="utf-8")

    diff = compute_task_review_diff(str(repo), _TASK_ID)

    assert diff is not None
    assert "return 99" in diff


def test_a_clean_worktree_with_no_task_commits_yields_nothing(repo: Path) -> None:
    # Sin cambios no hay diff que dar, y forzar uno vacío haría creer al
    # reviewer que la tarea no tocó nada cuando quizá es que se ejecutó en un
    # tmpfs. `None` = el reviewer sigue con la cosecha de ficheros de siempre.
    assert compute_task_review_diff(str(repo), _TASK_ID) is None


def test_no_worktree_yields_nothing(tmp_path: Path) -> None:
    # Runs de análisis/diseño: no hay worktree y el review en prosa funciona.
    assert compute_task_review_diff(None, _TASK_ID) is None


def test_a_git_failure_never_blocks_the_review(tmp_path: Path) -> None:
    # Best-effort de principio a fin: el diff es una AYUDA para juzgar mejor, y
    # no poder calcularlo no puede impedir que se juzgue.
    not_a_repo = tmp_path / "vacio"
    not_a_repo.mkdir()
    assert compute_task_review_diff(str(not_a_repo), _TASK_ID) is None


def test_a_huge_diff_is_truncated_and_says_so(repo: Path) -> None:
    # Un diff de megabytes desplazaría del prompt los criterios de aceptación,
    # que es lo que el reviewer tiene que certificar. Se corta, pero AVISANDO:
    # certificar sobre un cambio que no se vio entero, sin saberlo, es peor.
    (repo / "app.py").write_text("\n".join(f"line {i}" for i in range(200_000)), encoding="utf-8")

    diff = compute_task_review_diff(str(repo), _TASK_ID)

    assert diff is not None
    assert len(diff) < MAX_DIFF_CHARS + 500
    assert "truncado" in diff


# ---------------------------------------------------------------------------
# El diff llega al prompt del reviewer
# ---------------------------------------------------------------------------
def test_the_spec_carries_the_diff_inside_the_review_context() -> None:
    from workers.run_contract import ExecutionRequest
    from workers.run_spec import _agent_spec

    request = ExecutionRequest(
        tenant_id="tn1",
        task_id="t1",
        agent_id=None,
        task={"title": "x"},
        model={},
        review=True,
        review_context={"acceptance_criteria": "- hace algo"},
    )
    spec: dict[str, Any] = _agent_spec(request, None, code_diff="--- a/app.py\n+++ b/app.py\n")

    # En el MISMO bloque que el resto del contexto de review, no en una clave
    # nueva: para el runtime es «lo que hay que juzgar», igual que los criterios.
    assert spec["review_context"]["code_diff"].startswith("--- a/app.py")
    assert spec["review_context"]["acceptance_criteria"] == "- hace algo"


def test_an_implementer_run_carries_no_diff() -> None:
    from workers.run_contract import ExecutionRequest
    from workers.run_spec import _agent_spec

    request = ExecutionRequest(
        tenant_id="tn1", task_id="t1", agent_id=None, task={"title": "x"}, model={}
    )
    spec: dict[str, Any] = _agent_spec(request, None, code_diff="lo que sea")

    assert "review_context" not in spec


def test_a_sibling_commit_between_two_attempts_is_not_part_of_the_task_diff(repo: Path) -> None:
    """Auditoría 2026-09-01 (C-02): el rango `oldest^..newest` arrastraba lo ajeno.

    Tras un rechazo, el worktree se sincroniza al HEAD de la rama del plan —que
    ya lleva lo que empujaron las tareas hermanas— y el segundo intento se
    rebasea encima. Entre A1 y A2 hay un commit B de otra tarea, y el diff que
    juzgaba el reviewer lo incluía: rechazaba por trabajo que no era de la tarea
    o certificaba trabajo que nadie le pidió revisar. Reproducido con git real.
    """
    otra = "99999999-8888-7777-6666-555555555555"
    (repo / "app.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", f"feat: primer intento\n\nTask-Id: {_TASK_ID}", cwd=repo)
    (repo / "otro.py").write_text("x = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", f"feat: tarea hermana\n\nTask-Id: {otra}", cwd=repo)
    (repo / "app.py").write_text("def a():\n    return 3\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", f"feat: segundo intento\n\nTask-Id: {_TASK_ID}", cwd=repo)

    diff = compute_task_review_diff(str(repo), _TASK_ID)

    assert diff is not None
    assert "otro.py" not in diff, "el diff de la tarea lleva el commit de la hermana"
    assert (
        "+    return 2" in diff and "+    return 3" in diff
    ), "los dos intentos de la tarea tienen que estar, en orden"
    assert diff.index("return 2") < diff.index("return 3")
