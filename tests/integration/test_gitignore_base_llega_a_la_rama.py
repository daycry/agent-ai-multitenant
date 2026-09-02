"""El `.gitignore` base se escribe al CERRAR la tarea, nunca en la provisión.

El comportamiento de `ensure_base_gitignore` en aislamiento lo fija
`tests/unit/test_gitignore_base_de_la_rama_del_plan.py`. Aquí se prueban las dos
cosas que sólo se ven con el ciclo entero montado, y que es donde estuvo el
defecto:

  1. **que el agente NO lo vea.** Escribirlo en `_provision_worktree` dejaba el
     fichero en el workspace mientras corría el agente, y eso devuelve a FALLA la
     casilla que el ADR 0163 da por ganada. Medido el 2026-09-01 con la provisión
     real y Composer 2.9.4::

         workspace tras la provisión: ['.git', '.gitignore']
         lo que ve el agente:         ['.gitignore']
         composer create-project codeigniter4/framework .   ->  rc=1
             "Project directory is not empty."
         (control, workspace vacío: rc=0, instala v4.7.4)

     La causa está en el fuente del phar: ``Filesystem::isDirEmpty()`` usa
     ``->ignoreVCS(false)->ignoreDotFiles(false)``, o sea que CUENTA los
     dotfiles. Un `.gitignore` solo basta para tumbar el andamiador canónico del
     proyecto exacto del incidente.

  2. **que llegue igualmente al repositorio**, que es para lo que se quería:
     quien clone el proyecto y trabaje fuera de la plataforma no se puede comer
     el mismo `git add -A` que se llevó 1.151 ficheros de `vendor/` a la rama.

Escribirlo en `commit_task` da las dos: el fichero viaja en el commit de la tarea
y no existe en el workspace mientras el agente trabaja.

Git de verdad sobre ``tmp_path``, sin Docker ni red.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from workers.config import Settings
from workers.execution import _provision_worktree
from workers.git_identity import git_identity_env
from workers.git_repos import BareRepoLayout, BareRepoManager, GitCommandError, _run_git
from workers.plan_git import (
    CommitTrailers,
    PlanGitPolicies,
    PlanGitWorkflow,
    commit_task,
    make_plan_branch_name,
)

from tests.integration._git_helpers import commit_to_branch, seed_bare_repo

pytestmark = pytest.mark.integration


def _lo_que_ve_el_agente(workspace: str) -> list[str]:
    """Las entradas que cuenta un andamiador que exige directorio vacío.

    El `.git` no está en la lista porque el ADR 0163 lo esconde justo antes de
    lanzar al agente. Todo lo demás SÍ cuenta, dotfiles incluidos: es lo que hace
    `isDirEmpty()` de Composer, y el equivalente en `npm create`.
    """
    return sorted(e.name for e in Path(workspace).iterdir() if e.name != ".git")


def _commitea(worktree: str, plan_id: str, task_id: str) -> str:
    return commit_task(
        Path(worktree),
        message=f"wip: {task_id}",
        trailers=CommitTrailers(plan_id=plan_id, task_id=task_id, execution_id=str(uuid4())),
    )


def _empuja(tmp_path: Path, project: str, plan_id: str, plan_slug: str, worktree: str) -> None:
    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="mediapro", project_slug=project)
    PlanGitWorkflow(
        bare_repo_path=layout.bare_repo_path(project),
        plan_branch=make_plan_branch_name(plan_id, plan_slug),
        policies=PlanGitPolicies(),
    ).push_review_to_bare(Path(worktree))


def _en_la_rama(tmp_path: Path, project: str, plan_id: str, plan_slug: str) -> list[str]:
    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="mediapro", project_slug=project)
    salida = _run_git(
        "ls-tree",
        "-r",
        "--name-only",
        make_plan_branch_name(plan_id, plan_slug),
        cwd=layout.bare_repo_path(project),
    )
    return [linea for linea in salida.splitlines() if linea.strip()]


# ---------------------------------------------------------------------------
# 1. Lo que el agente NO puede encontrarse — la casilla del ADR 0163
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_workspace_del_agente_queda_vacio_para_el_andamiador(tmp_path: Path) -> None:
    """Proyecto nuevo: tras la provisión no hay NADA salvo el `.git` escondido.

    Es la casilla que el ADR 0163 declara ganada («`composer create-project .`
    pasa de FALLA a FUNCIONA») y que un `.gitignore` escrito aquí devolvía a
    FALLA. Un fichero solo basta: `isDirEmpty()` cuenta los dotfiles.
    """
    path = await _provision_worktree(
        Settings(data_root=str(tmp_path)),
        tenant_slug="mediapro",
        project_slug="hello-world-ci4",
        plan_id=str(uuid4()),
        plan_slug="arranque",
        task_id="task-1",
    )

    assert path is not None
    assert _lo_que_ve_el_agente(path) == [], (
        "la provisión deja algo en el workspace: el andamiador canónico del "
        "proyecto del incidente (`composer create-project .`) sale con rc=1 "
        "«Project directory is not empty»"
    )


@pytest.mark.asyncio
async def test_la_tarea_siguiente_tampoco_hereda_un_gitignore_de_relleno(tmp_path: Path) -> None:
    """El mismo fallo diferido una tarea, que es la trampa de escribirlo «igualmente».

    Si el cierre de una tarea que no produjo nada commiteara un `.gitignore`
    suelto, la tarea SIGUIENTE —la del esqueleto, en el plan del incidente— se
    encontraría el workspace con un fichero dentro y `composer create-project .`
    volvería a salir con rc=1. El `.gitignore` acompaña a contenido; nunca ES el
    contenido.
    """
    settings = Settings(data_root=str(tmp_path))
    plan_id = str(uuid4())
    comunes = {
        "tenant_slug": "mediapro",
        "project_slug": "hello-world-ci4",
        "plan_id": plan_id,
        "plan_slug": "arranque",
    }

    primera = await _provision_worktree(settings, task_id="task-1", **comunes)
    assert primera is not None
    # La tarea real del incidente: comprobar versiones. `composer install` deja
    # `vendor/` en disco y ningún entregable.
    (Path(primera) / "vendor" / "codeigniter4").mkdir(parents=True)
    (Path(primera) / "vendor" / "autoload.php").write_text("<?php\n", encoding="utf-8")

    with pytest.raises(GitCommandError, match="clean"):
        _commitea(primera, plan_id, "task-1")

    segunda = await _provision_worktree(settings, task_id="task-2", **comunes)

    assert segunda is not None
    # Vacío del todo: cada tarea tiene SU worktree, así que el `vendor/` que dejó
    # `composer install` en el de la tarea 1 ni siquiera llega aquí — lo único que
    # podría haber es lo que la rama traiga. Y la rama no trae nada, porque la
    # tarea 1 no entregó nada. Ése es exactamente el workspace que el ADR 0163
    # pelea por conseguir para el andamiador.
    assert _lo_que_ve_el_agente(segunda) == [], (
        "la tarea del esqueleto arranca con el workspace ya ocupado: "
        "`composer create-project .` volvería a salir con rc=1"
    )


# ---------------------------------------------------------------------------
# 2. Y aun así, que llegue al repositorio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_gitignore_base_viaja_a_la_rama_con_el_commit_de_la_tarea(
    tmp_path: Path,
) -> None:
    """El ciclo completo: no está mientras corre el agente, sí está en la rama."""
    settings = Settings(data_root=str(tmp_path))
    plan_id = str(uuid4())
    comunes = {
        "tenant_slug": "mediapro",
        "project_slug": "hello-world-ci4",
        "plan_id": plan_id,
        "plan_slug": "arranque",
    }

    primera = await _provision_worktree(settings, task_id="task-1", **comunes)
    assert primera is not None
    assert _lo_que_ve_el_agente(primera) == []
    # Lo que hizo la tarea del incidente: `composer install` + un entregable.
    (Path(primera) / "vendor" / "codeigniter4").mkdir(parents=True)
    (Path(primera) / "vendor" / "autoload.php").write_text("<?php\n", encoding="utf-8")
    (Path(primera) / "app").mkdir()
    (Path(primera) / "app" / "Home.php").write_text("<?php\n", encoding="utf-8")

    _commitea(primera, plan_id, "task-1")
    _empuja(tmp_path, "hello-world-ci4", plan_id, "arranque", primera)

    en_la_rama = _en_la_rama(tmp_path, "hello-world-ci4", plan_id, "arranque")
    assert ".gitignore" in en_la_rama, (
        "el `.gitignore` base no llegó a la rama: quien clone el proyecto se come "
        "el mismo `git add -A` que se llevó 1.151 ficheros de vendor/"
    )
    assert "app/Home.php" in en_la_rama, "el entregable tiene que seguir entrando"
    assert not [f for f in en_la_rama if f.startswith("vendor/")]

    segunda = await _provision_worktree(settings, task_id="task-2", **comunes)
    assert segunda is not None
    assert (Path(segunda) / ".gitignore").is_file()


@pytest.mark.asyncio
async def test_un_desversionado_que_vacia_el_proyecto_no_deja_el_gitignore_solo(
    tmp_path: Path,
) -> None:
    """El caso que junta las dos mitades de este cambio.

    Rama en el estado que dejó el incidente: SÓLO `vendor/`, versionado. Al
    cerrar la tarea, el des-versionado lo saca del índice y el árbol se queda
    vacío. Si el `.gitignore` se escribiera igualmente, el proyecto pasaría de
    vacío a «tiene un fichero» sin que nadie haya entregado nada — y la tarea del
    esqueleto se volvería a estrellar contra `isDirEmpty()`.
    """
    settings = Settings(data_root=str(tmp_path))
    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="mediapro", project_slug="api-ci")
    seed_bare_repo(BareRepoManager(layout).ensure_repo("api-ci"))
    plan_id = str(uuid4())
    comunes = {
        "tenant_slug": "mediapro",
        "project_slug": "api-ci",
        "plan_id": plan_id,
        "plan_slug": "arranque",
    }

    primera = await _provision_worktree(settings, task_id="task-1", **comunes)
    assert primera is not None
    # El estado de partida lo dejó el CÓDIGO ANTERIOR, así que se reproduce con
    # git desnudo: fabricarlo con `commit_task` no probaría nada. La semilla del
    # arnés (README.md) se retira para que `vendor/` sea el único contenido, que
    # es como quedó la rama del incidente.
    (Path(primera) / "README.md").unlink()
    (Path(primera) / "vendor").mkdir()
    (Path(primera) / "vendor" / "autoload.php").write_text("<?php\n", encoding="utf-8")
    # Firmado por la PLATAFORMA: el accidente fue un `commit_task` viejo. Un
    # `vendor/` que firmara una persona sería del proyecto y NO se retiraría
    # (auditoría 2026-09-01, `workers.dependency_dirs`).
    identidad = git_identity_env()
    _run_git("add", "-A", cwd=Path(primera), env_extra=identidad)
    _run_git(
        "commit", "-m", "la tarea anterior se llevo vendor/", cwd=Path(primera), env_extra=identidad
    )
    _empuja(tmp_path, "api-ci", plan_id, "arranque", primera)

    path = await _provision_worktree(settings, task_id="task-2", **comunes)
    assert path is not None
    assert _en_la_rama(tmp_path, "api-ci", plan_id, "arranque") == ["vendor/autoload.php"]

    _commitea(path, plan_id, "task-2")
    _empuja(tmp_path, "api-ci", plan_id, "arranque", path)

    en_la_rama = _en_la_rama(tmp_path, "api-ci", plan_id, "arranque")
    assert en_la_rama == [], (
        f"la rama se queda con {en_la_rama}: el proyecto ya no está vacío y el "
        "andamiador de la tarea siguiente vuelve a fallar por directorio no vacío"
    )


@pytest.mark.asyncio
async def test_no_pisa_el_gitignore_que_ya_trae_el_proyecto(tmp_path: Path) -> None:
    """Un proyecto con su `.gitignore` en la rama base no ve nada raro."""
    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="mediapro", project_slug="api-ci")
    bare = BareRepoManager(layout).ensure_repo("api-ci")
    seed_bare_repo(bare)
    plan_id = str(uuid4())
    rama = make_plan_branch_name(plan_id, "arranque")
    propio = "# el del proyecto\n/writable/cache\n"
    commit_to_branch(bare, rama, filename=".gitignore", content=propio)

    path = await _provision_worktree(
        Settings(data_root=str(tmp_path)),
        tenant_slug="mediapro",
        project_slug="api-ci",
        plan_id=plan_id,
        plan_slug="arranque",
        task_id="task-1",
    )
    assert path is not None
    (Path(path) / "app").mkdir()
    (Path(path) / "app" / "Home.php").write_text("<?php\n", encoding="utf-8")

    _commitea(path, plan_id, "task-1")

    assert (Path(path) / ".gitignore").read_text(encoding="utf-8") == propio
