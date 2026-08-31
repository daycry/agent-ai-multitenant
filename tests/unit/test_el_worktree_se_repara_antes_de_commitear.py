"""El worker reconstruye el enlace del worktree si el agente lo borró.

## Lo que pasó, medido en vivo

Proyecto `Hello World CI4 v3` del tenant Mediapro, 2026-08-31, primer run:

    stack_exec  composer create-project codeigniter4/framework .   -> falla
    delete_file .git                                               -> deleted: true
    stack_exec  composer create-project codeigniter4/framework .   -> falla
    delete_file README.md                                          -> deleted: true
    stack_exec  composer create-project codeigniter4/framework .   -> OK

El agente instaló CodeIgniter 4.7.4 y `php spark routes` respondió. Al cerrar:

    git add -A failed (rc=128): fatal: not a git repository

Deliverable hecho, en disco, fuera de toda rama. La tarea acabó `blocked`.

## Por qué la guarda de la tool no basta

`file_tools` ya rechaza `.git` desde el mismo día, pero la allowlist base del
SDK incluye `rm`: `shell_exec("rm .git")` consigue lo mismo. Una guarda por
puerta deja las demás abiertas. Ésta cubre el RESULTADO —que el enlace exista
cuando toca commitear— y por eso vale para cualquier mecanismo.

## El orden, que es lo que se descubrió midiendo

`git worktree repair` reconstruye el puntero **sólo mientras sobrevivan los
metadatos del bare** (`<bare>/worktrees/<id>`). En cuanto un git ve un puntero
roto dispara `worktree prune`, esos metadatos desaparecen y ya no hay nada que
reparar. Comprobado en los dos casos, y los dos están abajo.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from workers.plan_git import repair_worktree_link

pytestmark = pytest.mark.unit


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
        check=False,  # el rc se inspecciona abajo, con el stderr a la vista
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} -> rc={proc.returncode}: {proc.stderr}")
    return proc.stdout


@pytest.fixture
def proyecto(tmp_path: Path) -> Path:
    """Reproduce la disposición real: <project>/repos/<repo>.git + <project>/worktrees/<id>."""
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

    _git(
        "--git-dir", str(bare), "worktree", "add", "-q", str(project / "worktrees" / "t1"), "master"
    )
    return project


def test_sin_enlace_roto_no_toca_nada(proyecto: Path) -> None:
    """El camino normal: si el puntero está, la función no hace nada."""
    wt = proyecto / "worktrees" / "t1"
    antes = (wt / ".git").read_text(encoding="utf-8")
    assert repair_worktree_link(wt) is False
    assert (wt / ".git").read_text(encoding="utf-8") == antes


def test_reconstruye_el_enlace_que_el_agente_borro(proyecto: Path) -> None:
    """El caso de producción, exactamente: `.git` borrado, metadatos intactos."""
    wt = proyecto / "worktrees" / "t1"
    (wt / ".git").unlink()
    assert not (wt / ".git").exists()

    assert repair_worktree_link(wt) is True
    assert (wt / ".git").exists(), "el enlace no se reconstruyó"


def test_tras_reparar_el_trabajo_del_agente_se_puede_commitear(proyecto: Path) -> None:
    """Lo que de verdad importa: que el deliverable deje de perderse.

    Sin esto lo anterior sería cosmética — el enlace podría existir y `git
    add -A` fallar igual.
    """
    wt = proyecto / "worktrees" / "t1"
    (wt / ".git").unlink()
    (wt / "app").mkdir()
    (wt / "app" / "Controller.php").write_text("<?php // trabajo del agente\n", encoding="utf-8")

    repair_worktree_link(wt)

    _git("add", "-A", cwd=wt)
    _git("commit", "-qm", "deliverable", cwd=wt)
    registrado = _git("show", "--name-only", "--format=", "HEAD", cwd=wt)
    assert "app/Controller.php" in registrado


def test_si_los_metadatos_ya_se_podaron_no_miente(proyecto: Path) -> None:
    """El caso irrecuperable devuelve False en vez de fingir que reparó.

    Es el estado en el que quedó el run real: para cuando alguien miró, algún
    git había disparado `worktree prune` y el puntero ya no se podía
    reconstruir. Que la función lo diga es lo que permite al llamante emitir un
    error accionable en vez del críptico «not a git repository».
    """
    wt = proyecto / "worktrees" / "t1"
    (wt / ".git").unlink()
    _git("--git-dir", str(proyecto / "repos" / "app.git"), "worktree", "prune")
    assert not (proyecto / "repos" / "app.git" / "worktrees" / "t1").exists()

    assert repair_worktree_link(wt) is False


def test_sin_bare_no_revienta(tmp_path: Path) -> None:
    """Una disposición inesperada no puede tumbar el cierre de la tarea."""
    wt = tmp_path / "proyecto" / "worktrees" / "t1"
    wt.mkdir(parents=True)
    assert repair_worktree_link(wt) is False


def test_commit_task_repara_antes_de_tocar_git() -> None:
    """El orden dentro de `commit_task`, fijado sobre el AST.

    No es estilo: `git add -A` sobre un puntero roto dispara `worktree prune`,
    y la poda es lo que vuelve el caso irrecuperable. Reparar después de eso no
    sirve de nada, y el test que sólo comprobara «se llama a reparar» pasaría
    igual con la llamada al final.
    """
    import ast
    import inspect

    from workers import plan_git

    fuente = inspect.getsource(plan_git.commit_task)
    arbol = ast.parse(fuente.strip())
    llamadas = [
        n.func.id
        for n in ast.walk(arbol)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert "repair_worktree_link" in llamadas, "commit_task no repara el enlace"
    assert "_run_git" in llamadas, "commit_task ya no invoca git: ¿cambió de forma?"
    assert llamadas.index("repair_worktree_link") < llamadas.index("_run_git"), (
        "se repara DESPUÉS de invocar git: el primer git sobre un puntero roto "
        "dispara `worktree prune` y deja el caso irrecuperable"
    )
