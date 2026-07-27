"""El cierre de plan genera y commitea su changelog (T8 de `tools-y-cierre-plan-fixes`).

Hallazgo **c4** de la auditoría de plataforma 2026-07-03: el criterio de cierre 4
de `CLAUDE.md` («entrada generada en `docs/07-changelog/{plan_id}.md`») no lo
cumplía **ningún camino automático**. `generate_plan_docs` y `render_changelog`
existían, estaban testeados… y solo los llamaban los tests. El agente Technical
Writer estaba sembrado pero nadie le creaba la tarea.

Aquí se pinea el camino real: dado un plan cerrado, el worker escribe el
changelog en un worktree de la rama del plan, lo commitea con los trailers y lo
empuja al bare — **antes** de que se abra el PR, para que el PR lo contenga.

Git de verdad contra `tmp_path` (sin remoto, sin red, sin DB): la parte que toca
git recibe ya resueltos tenant/proyecto/plan, igual que
`_push_branch_to_remote_gated`, para que se pueda ejercer sin sembrar filas.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from api_server.tech_writer.changelog import ChangelogTask, PlanMeta

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration

_TENANT = "acme"
_PROJECT = "api-ci"
_PLAN_ID = "0199aa11-2233-4455-6677-889900aabbcc"
_PLAN_SLUG = "cierre-del-visor"


def _git_out(*args: str, cwd: Path) -> str:
    # `encoding="utf-8"` explícito: en Windows `text=True` decodifica con la
    # codepage local (cp1252) y los glifos ✅/❌ del changelog revientan con
    # UnicodeDecodeError dentro del hilo lector de subprocess.
    return subprocess.run(  # — args explícitos, sin shell
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    ).stdout


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    """Un `data_root` con el bare del proyecto ya sembrado, como lo deja la
    ejecución de la primera tarea del plan."""
    bare = tmp_path / "projects" / _TENANT / _PROJECT / "repos" / f"{_PROJECT}.git"
    seed_bare_repo(bare)
    return tmp_path


def _plan_meta(**over: object) -> PlanMeta:
    base: dict[str, object] = {
        "plan_id": _PLAN_ID,
        "title": "Cierre del visor",
        "summary": "Lo que hizo el plan.",
        "tasks": (
            ChangelogTask(task_key="task_01", title="Primera", done=True),
            ChangelogTask(task_key="task_02", title="Segunda", done=False),
        ),
        "docs_language": "es",
    }
    base.update(over)
    return PlanMeta(**base)  # type: ignore[arg-type]


def _write(data_root: Path, *, meta: PlanMeta | None = None) -> str:
    from workers.plan_docs import write_plan_docs_to_branch

    return write_plan_docs_to_branch(
        data_root=data_root,
        tenant_slug=_TENANT,
        project_slug=_PROJECT,
        plan_id=_PLAN_ID,
        plan_slug=_PLAN_SLUG,
        plan_meta=meta or _plan_meta(),
    )


def _branch_files(data_root: Path) -> list[str]:
    from workers.plan_git import make_plan_branch_name

    bare = data_root / "projects" / _TENANT / _PROJECT / "repos" / f"{_PROJECT}.git"
    branch = make_plan_branch_name(_PLAN_ID, _PLAN_SLUG)
    return _git_out("ls-tree", "-r", "--name-only", branch, cwd=bare).split()


def _branch_show(data_root: Path, path: str) -> str:
    from workers.plan_git import make_plan_branch_name

    bare = data_root / "projects" / _TENANT / _PROJECT / "repos" / f"{_PROJECT}.git"
    branch = make_plan_branch_name(_PLAN_ID, _PLAN_SLUG)
    return _git_out("show", f"{branch}:{path}", cwd=bare)


# ---------------------------------------------------------------------------
# El caso que cierra c4.
# ---------------------------------------------------------------------------
def test_changelog_lands_committed_on_the_plan_branch(data_root: Path) -> None:
    assert _write(data_root) == "written"
    assert f"docs/07-changelog/{_PLAN_ID}.md" in _branch_files(data_root)


def test_the_changelog_content_is_the_rendered_one(data_root: Path) -> None:
    _write(data_root)
    body = _branch_show(data_root, f"docs/07-changelog/{_PLAN_ID}.md")
    assert "Cierre del visor" in body
    assert "task_01" in body and "task_02" in body
    # El glifo distingue una tarea cerrada de una que no lo está: un changelog
    # que las pinta todas iguales miente sobre el cierre parcial.
    assert "✅" in body and "❌" in body


def test_the_commit_carries_the_plan_trailer(data_root: Path) -> None:
    from workers.plan_git import make_plan_branch_name

    _write(data_root)
    bare = data_root / "projects" / _TENANT / _PROJECT / "repos" / f"{_PROJECT}.git"
    branch = make_plan_branch_name(_PLAN_ID, _PLAN_SLUG)
    message = _git_out("log", "-1", "--format=%B", branch, cwd=bare)
    assert f"Plan-Id: {_PLAN_ID}" in message


# ---------------------------------------------------------------------------
# Idempotencia: el cierre se reintenta (reconciler, re-veredicto, backfill).
# ---------------------------------------------------------------------------
def test_second_run_writes_nothing_and_commits_nothing(data_root: Path) -> None:
    from workers.plan_git import make_plan_branch_name

    _write(data_root)
    bare = data_root / "projects" / _TENANT / _PROJECT / "repos" / f"{_PROJECT}.git"
    branch = make_plan_branch_name(_PLAN_ID, _PLAN_SLUG)
    before = _git_out("rev-parse", branch, cwd=bare).strip()

    assert _write(data_root) == "skipped:already_generated"
    assert _git_out("rev-parse", branch, cwd=bare).strip() == before


def test_a_human_edited_changelog_is_never_clobbered(data_root: Path) -> None:
    """`generate_plan_docs` es skip-if-exists; el segundo pase con OTRO contenido
    no debe pisar lo que un humano haya reescrito."""
    _write(data_root)
    original = _branch_show(data_root, f"docs/07-changelog/{_PLAN_ID}.md")

    _write(data_root, meta=_plan_meta(title="Título distinto", summary="otra cosa"))
    assert _branch_show(data_root, f"docs/07-changelog/{_PLAN_ID}.md") == original


# ---------------------------------------------------------------------------
# Degradaciones: nunca romper un cierre ya comprometido.
# ---------------------------------------------------------------------------
def test_missing_bare_repo_degrades_instead_of_raising(tmp_path: Path) -> None:
    """Un proyecto sin repo (nunca ejecutó una tarea) no tiene dónde escribir.
    El plan ya está `completed` en BD: reventar aquí no lo desharía, solo
    dejaría un traceback en el log del worker."""
    assert _write(tmp_path).startswith("skipped:")


def test_the_branch_is_created_when_the_plan_never_committed(data_root: Path) -> None:
    """Un plan cuyas tareas no produjeron código no tiene rama todavía.

    Que el changelog exista igualmente es justo el criterio 4 de CLAUDE.md: no
    depende de que el plan tocara ficheros.
    """
    assert _write(data_root) == "written"
    assert f"docs/07-changelog/{_PLAN_ID}.md" in _branch_files(data_root)
