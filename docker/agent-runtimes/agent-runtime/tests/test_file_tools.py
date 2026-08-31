"""Unit tests for the file family tools — focus on file_delete (ADR 0089 / R6).

A run that produces a coherent deliverable sometimes has to REMOVE a stale or
duplicate file left by an earlier attempt (the worktree persists across runs).
Before delete_file the agent had no way to do this (`rm`/`git rm` gated, no
delete tool), so it could not reconcile competing implementations and never
converged. ``file_delete`` closes that gap, path-jailed to the workspace.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.file_tools import WorkspaceFiles


def _files(tmp_path: Path) -> WorkspaceFiles:
    return WorkspaceFiles(root=str(tmp_path))


def test_delete_removes_an_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "dup.php"
    target.write_text("<?php // duplicate", encoding="utf-8")
    res = _files(tmp_path).file_delete({"path": "dup.php"})
    assert res.ok is True
    assert res.output == {"path": "dup.php", "deleted": True}
    assert not target.exists()


def test_delete_missing_file_fails_cleanly(tmp_path: Path) -> None:
    res = _files(tmp_path).file_delete({"path": "nope.php"})
    assert res.ok is False
    # "not found", no "not a file": desde que `recursive` acepta directorios, un
    # camino que no existe no es «no es un fichero» —podría haber sido un
    # directorio válido— sino que no está. El mensaje viejo mandaba al agente a
    # dudar del TIPO cuando el problema era la RUTA.
    assert "not found" in (res.error or "")


def test_delete_directory_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    res = _files(tmp_path).file_delete({"path": "sub"})
    assert res.ok is False
    assert "directory" in (res.error or "")
    assert (tmp_path / "sub").is_dir()  # not removed


def test_delete_path_escaping_workspace_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("keep me", encoding="utf-8")
    try:
        res = _files(tmp_path).file_delete({"path": "../secret.txt"})
        assert res.ok is False
        assert "escapes the workspace" in (res.error or "")
        assert outside.exists()  # path-jail prevented the delete
    finally:
        outside.unlink()


def test_delete_empty_path_is_rejected(tmp_path: Path) -> None:
    res = _files(tmp_path).file_delete({"path": "   "})
    assert res.ok is False
    assert "non-empty 'path'" in (res.error or "")


# ---------------------------------------------------------------------------
# `.git` es intocable — medido en vivo, no supuesto
# ---------------------------------------------------------------------------
# El 2026-08-31, primer run del proyecto `Hello World CI4 v3` de Mediapro:
#
#     stack_exec  composer create-project codeigniter4/framework .   -> falla
#     delete_file .git                                               -> deleted: true
#     stack_exec  composer create-project codeigniter4/framework .   -> falla
#     delete_file README.md                                          -> deleted: true
#     stack_exec  composer create-project codeigniter4/framework .   -> OK
#
# El agente instaló CodeIgniter 4.7.4 y `php spark routes` respondió. Al cerrar:
#
#     git add -A failed (rc=128): fatal: not a git repository
#
# Trabajo hecho, imposible de entregar. Y desde el lado del agente no fue un
# error: `composer create-project` EXIGE directorio vacío, y `.git` es un
# fichero que estorba. Nada le dice que sostiene los principios 4 y 5.
#
# La guarda que ya existía no lo cubría, y ahí está la lección: `file_delete`
# rechaza DIRECTORIOS, y en un clon normal `.git` es un directorio. En un
# WORKTREE es un FICHERO con un puntero `gitdir:`, así que la protección dejaba
# de aplicar justo en la disposición que usa la plataforma.


def _worktree_git_file(tmp_path: Path) -> Path:
    """Reproduce la forma REAL: `.git` como fichero con puntero `gitdir:`."""
    enlace = tmp_path / ".git"
    enlace.write_text("gitdir: /data/repos/proyecto.git/worktrees/abc\n", encoding="utf-8")
    return enlace


def test_delete_refuses_the_worktree_git_link(tmp_path: Path) -> None:
    enlace = _worktree_git_file(tmp_path)
    res = _files(tmp_path).file_delete({"path": ".git"})
    assert res.ok is False
    assert ".git" in (res.error or "")
    assert enlace.exists(), "el enlace del worktree se borró pese a la guarda"


def test_delete_refuses_paths_inside_a_git_directory(tmp_path: Path) -> None:
    """Un clon normal: `.git` es directorio y su contenido tampoco se toca."""
    (tmp_path / ".git").mkdir()
    head = tmp_path / ".git" / "HEAD"
    head.write_text("ref: refs/heads/master\n", encoding="utf-8")
    res = _files(tmp_path).file_delete({"path": ".git/HEAD"})
    assert res.ok is False
    assert head.exists()


def test_write_refuses_to_overwrite_the_git_link(tmp_path: Path) -> None:
    """La otra puerta: sobrescribir el puntero rompe lo mismo que borrarlo."""
    enlace = _worktree_git_file(tmp_path)
    original = enlace.read_text(encoding="utf-8")
    res = _files(tmp_path).file_write({"path": ".git", "content": "roto"})
    assert res.ok is False
    assert enlace.read_text(encoding="utf-8") == original


def test_the_refusal_says_what_to_do_instead(tmp_path: Path) -> None:
    """Un «no» sin salida hace que el agente lo reintente o lo rodee.

    El caso real venía de una herramienta que EXIGE directorio vacío; si el
    error no ofrece alternativa, el agente prueba otra forma de borrarlo.
    """
    _worktree_git_file(tmp_path)
    res = _files(tmp_path).file_delete({"path": ".git"})
    assert res.ok is False
    error = (res.error or "").lower()
    assert "commit" in error or "push" in error, "no explica la consecuencia"
    assert "subdirectory" in error or "subdirectorio" in error, "no ofrece salida"


def test_a_file_merely_starting_with_git_is_untouched(tmp_path: Path) -> None:
    """La guarda casa el SEGMENTO `.git`, no un prefijo.

    `.gitignore` y `.github/` son ficheros normales del proyecto: bloquearlos
    convertiría una guarda en un estorbo, y el agente aprendería a rodearla.
    """
    for nombre in (".gitignore", ".gitattributes"):
        objetivo = tmp_path / nombre
        objetivo.write_text("vendor/\n", encoding="utf-8")
        res = _files(tmp_path).file_delete({"path": nombre})
        assert res.ok is True, f"{nombre} debería poder borrarse: {res.error}"


# ---------------------------------------------------------------------------
# `recursive`: borrar un DIRECTORIO, con la intención explícita
# ---------------------------------------------------------------------------
# El hueco se midió en vivo el 2026-08-31: el agente que instalaba CodeIgniter
# intentó `shell_exec("rm -rf ./* ./.??*")` y rebotó contra el allowlist del
# proyecto. Fichero a fichero un `vendor/` son miles de llamadas — inviable, así
# que la necesidad era real aunque la vía fuera la mala.
#
# Se resuelve aquí y no abriendo `rm` en el allowlist porque `shell_exec` es la
# puerta equivocada del ADR 0162, porque `rm -rf ./*` es ilimitado por
# naturaleza, y porque así queda AUDITADO: el `steps_log` guarda la ruta y
# cuántas entradas se llevó.


def test_delete_directory_needs_recursive(tmp_path: Path) -> None:
    """Sin la bandera sigue rechazándose, y el error dice cómo hacerlo."""
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "x.php").write_text("<?php", encoding="utf-8")

    res = _files(tmp_path).file_delete({"path": "vendor"})
    assert res.ok is False
    assert "recursive=true" in (res.error or ""), "el error no dice cómo borrarlo"
    assert (tmp_path / "vendor").is_dir()


def test_delete_directory_with_recursive(tmp_path: Path) -> None:
    (tmp_path / "vendor" / "pkg").mkdir(parents=True)
    (tmp_path / "vendor" / "pkg" / "a.php").write_text("<?php", encoding="utf-8")
    (tmp_path / "vendor" / "autoload.php").write_text("<?php", encoding="utf-8")

    res = _files(tmp_path).file_delete({"path": "vendor", "recursive": True})
    assert res.ok is True
    assert not (tmp_path / "vendor").exists()
    assert res.output is not None
    assert res.output["entries"] == 3, f"el recuento no cuadra: {res.output}"


def test_recursive_never_empties_the_workspace_root(tmp_path: Path) -> None:
    """La única operación que no es «un árbol menos» sino «el deliverable».

    Ninguna necesidad legítima la pide: para andamiar sobre un directorio limpio
    está el ADR 0163, que quita de en medio lo único que estorbaba.
    """
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "Hello.php").write_text("<?php", encoding="utf-8")

    for ruta in (".", "./", "app/.."):
        res = _files(tmp_path).file_delete({"path": ruta, "recursive": True})
        assert res.ok is False, f"se aceptó vaciar la raíz con path={ruta!r}"
        assert "workspace root" in (res.error or "")
    assert (tmp_path / "app" / "Hello.php").exists()


def test_recursive_still_refuses_the_git_link(tmp_path: Path) -> None:
    """La bandera no es una puerta trasera a la guarda del ADR 0163."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")

    res = _files(tmp_path).file_delete({"path": ".git", "recursive": True})
    assert res.ok is False
    assert (tmp_path / ".git").is_dir()


def test_recursive_stays_inside_the_workspace(tmp_path: Path) -> None:
    """La jaula de ruta vale igual con la bandera puesta."""
    fuera = tmp_path.parent / "fuera_del_workspace"
    fuera.mkdir(exist_ok=True)
    (fuera / "importante.txt").write_text("no tocar\n", encoding="utf-8")

    res = _files(tmp_path).file_delete({"path": "../fuera_del_workspace", "recursive": True})
    assert res.ok is False
    assert (fuera / "importante.txt").exists()


def test_recursive_on_a_file_works_as_before(tmp_path: Path) -> None:
    """La bandera no cambia el caso del fichero: sigue siendo un `unlink`."""
    objetivo = tmp_path / "viejo.php"
    objetivo.write_text("<?php", encoding="utf-8")
    res = _files(tmp_path).file_delete({"path": "viejo.php", "recursive": True})
    assert res.ok is True
    assert not objetivo.exists()
