"""El hermano transitorio de las tools de fichero no entra en el commit del plan.

## De dónde sale esto

Las tres tools destructivas de la familia `file` dejaron de destruir en su
sitio. En vez de eso **apartan** —un renombrado, que es atómico y no destruye— y
descartan después; si la operación falla, lo apartado se devuelve a su sitio.
Ese patrón cerró un defecto medido el 2026-08-31 en la imagen real del runtime
con uid no-root: un `delete_file vendor --recursive` sobre un árbol con un
subdirectorio a `0o500` perdía la mitad de las entradas y devolvía `ok=False`,
así que el agente leía «no ha pasado nada».

El precio del patrón es un residuo: cuando el descarte final no se puede hacer
—el mismo `EACCES` que motivaba todo— queda un hermano
`.agent-runtime-tmp.<nombre>.<n>` en el workspace.

## Por qué hace falta este fichero

`commit_task` hace `git add -A` sobre el worktree entero. Sin nada que lo
excluya, ese residuo **se commitea en la rama del plan** y viaja al PR: el
deliverable acaba con una copia oculta del árbol que se quiso retirar, con un
nombre que no significa nada para quien lo revise.

El prefijo va DELANTE del nombre (`.agent-runtime-tmp.vendor.0`, no
`vendor.replaced-0`) precisamente para que un solo patrón lo cubra todo, sin
comodines por los dos lados.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from workers.plan_git import CommitTrailers, commit_task

pytestmark = pytest.mark.unit

#: El mismo literal que `agent_runtime.file_tools._PREFIJO_TRANSITORIO`. Se
#: repite a propósito: son dos procesos distintos —el runtime corre dentro del
#: contenedor efímero y el worker fuera— sin ningún paquete común, así que un
#: import cruzado sería una dependencia que no existe. Lo que sí existe es este
#: test, que falla si alguien cambia uno de los dos lados.
PREFIJO = ".agent-runtime-tmp."


def _git(*args: str, cwd: Path | None = None) -> str:
    """git con identidad fija: el arnés no depende del ~/.gitconfig del host."""
    proc = subprocess.run(
        [
            "git",
            "-c",
            "user.email=harness@example.com",
            "-c",
            "user.name=harness",
            "-c",
            "safe.bareRepository=all",
            *args,
        ],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} -> rc={proc.returncode}: {proc.stderr}")
    return proc.stdout


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """La disposición real: <project>/repos/<repo>.git + <project>/worktrees/<id>."""
    project = tmp_path / "proyecto"
    (project / "repos").mkdir(parents=True)
    (project / "worktrees").mkdir()

    bare = project / "repos" / "app.git"
    _git("init", "--bare", "-q", str(bare))

    semilla = tmp_path / "semilla"
    _git("clone", "-q", str(bare), str(semilla))
    (semilla / "README.md").write_text("# hola\n", encoding="utf-8")
    _git("add", "-A", cwd=semilla)
    _git("commit", "-qm", "init", cwd=semilla)
    _git("push", "-q", "origin", "HEAD:master", cwd=semilla)

    wt = project / "worktrees" / "t1"
    _git("--git-dir", str(bare), "worktree", "add", "-q", str(wt), "master")
    return wt


def _ficheros_del_commit(worktree_path: Path, sha: str) -> list[str]:
    salida = _git("ls-tree", "-r", "--name-only", sha, cwd=worktree_path)
    return [linea for linea in salida.splitlines() if linea]


def _commit(worktree_path: Path) -> str:
    return commit_task(
        worktree_path,
        message="wip: la tarea entrega su trabajo",
        trailers=CommitTrailers(
            plan_id="01a05848-89aa-75ca-bd4d-ca2922da0129",
            task_id="01a05849-0438-769e-86ba-712d44e2c38a",
            execution_id="01a05881-89d7-79fa-be72-bd0e7c1a9fbb",
        ),
    )


def test_el_deliverable_de_verdad_si_se_commitea(worktree: Path) -> None:
    """La cara positiva, primero: sin ella el test de abajo pasaría en vacío.

    Un filtro que excluyera de más dejaría el commit sin el trabajo de la tarea
    y este fichero seguiría en verde, que es la peor forma de «arreglarlo».
    """
    (worktree / "app").mkdir()
    (worktree / "app" / "Controller.php").write_text("<?php\n", encoding="utf-8")

    ficheros = _ficheros_del_commit(worktree, _commit(worktree))

    assert "app/Controller.php" in ficheros, (
        "el trabajo de la tarea no entró en el commit: el filtro se está llevando "
        "por delante el deliverable"
    )


def test_un_arbol_apartado_en_la_raiz_no_entra(worktree: Path) -> None:
    """El caso medido: `delete_file vendor --recursive` que no pudo descartar."""
    (worktree / "app").mkdir()
    (worktree / "app" / "Controller.php").write_text("<?php\n", encoding="utf-8")

    residuo = worktree / f"{PREFIJO}vendor.0"
    (residuo / "paquete").mkdir(parents=True)
    (residuo / "paquete" / "autoload.php").write_text("<?php\n", encoding="utf-8")

    ficheros = _ficheros_del_commit(worktree, _commit(worktree))

    colados = [f for f in ficheros if PREFIJO in f]
    assert not colados, (
        f"el residuo del runtime entró en el commit del plan: {colados}. Viaja al "
        "PR como una copia oculta del árbol que se quiso retirar."
    )
    assert "app/Controller.php" in ficheros, "y el deliverable tiene que seguir entrando"


def test_tambien_si_quedo_en_un_subdirectorio(worktree: Path) -> None:
    """El residuo aparece AL LADO de su objetivo, que puede estar a cualquier
    profundidad: `delete_file app/Config/cache --recursive` lo deja dentro de
    `app/Config/`. Un patrón anclado a la raíz no serviría."""
    hondo = worktree / "app" / "Config"
    hondo.mkdir(parents=True)
    (hondo / "App.php").write_text("<?php\n", encoding="utf-8")
    (hondo / f"{PREFIJO}cache.3").mkdir()
    (hondo / f"{PREFIJO}cache.3" / "viejo.php").write_text("<?php\n", encoding="utf-8")

    ficheros = _ficheros_del_commit(worktree, _commit(worktree))

    colados = [f for f in ficheros if PREFIJO in f]
    assert not colados, f"residuo anidado colado en el commit: {colados}"
    assert "app/Config/App.php" in ficheros


def test_un_fichero_suelto_apartado_tampoco(worktree: Path) -> None:
    """`write_file` deja un transitorio de FICHERO, no de directorio, si el
    `os.replace` falló. Misma regla."""
    (worktree / "composer.json").write_text("{}\n", encoding="utf-8")
    (worktree / f"{PREFIJO}composer.json.0").write_text("{parcial", encoding="utf-8")

    ficheros = _ficheros_del_commit(worktree, _commit(worktree))

    colados = [f for f in ficheros if PREFIJO in f]
    assert not colados, f"transitorio de fichero colado en el commit: {colados}"
    assert "composer.json" in ficheros


def test_un_fichero_que_solo_se_parece_al_residuo_si_entra(worktree: Path) -> None:
    """La exclusión no puede ser más ancha de lo que dice.

    Un fichero que el agente escribió a propósito y que contiene el prefijo en
    mitad del nombre —o que empieza por algo parecido— es deliverable y entra.
    """
    (worktree / "docs").mkdir()
    (worktree / "docs" / "sobre-agent-runtime-tmp.md").write_text("# nota\n", encoding="utf-8")
    (worktree / ".agent-runtime-notes.md").write_text("# notas\n", encoding="utf-8")

    ficheros = _ficheros_del_commit(worktree, _commit(worktree))

    assert "docs/sobre-agent-runtime-tmp.md" in ficheros, (
        "la exclusión está cazando ficheros legítimos por parecerse al residuo"
    )
    assert ".agent-runtime-notes.md" in ficheros
