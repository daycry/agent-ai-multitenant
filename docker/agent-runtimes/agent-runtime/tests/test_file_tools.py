"""Unit tests for the file family tools — focus on file_delete (ADR 0089 / R6).

A run that produces a coherent deliverable sometimes has to REMOVE a stale or
duplicate file left by an earlier attempt (the worktree persists across runs).
Before delete_file the agent had no way to do this (`rm`/`git rm` gated, no
delete tool), so it could not reconcile competing implementations and never
converged. ``file_delete`` closes that gap, path-jailed to the workspace.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest
from agent_runtime import file_tools
from agent_runtime.builtin_families import (
    ALL_FAMILIES,
    FAMILY_FILE,
    register_builtin_families,
)
from agent_runtime.file_tools import WorkspaceFiles
from agent_runtime.orchestration_tools import OrchestrationSink
from agent_runtime.tools import ToolRegistry, ToolResult


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


# ---------------------------------------------------------------------------
# `recursive` sobre un árbol VERSIONADO se lleva el deliverable de otra tarea
# ---------------------------------------------------------------------------
# Medido en vivo el 2026-08-31, proyecto `Hello World CI4 v3` del tenant
# mediapro, en la MISMA tool que acababa de estrenar `recursive`:
#
#     delete_file {"path": "app", "recursive": true}   ->  ok, entries=85
#
# Esas 85 entradas eran el deliverable YA COMMITEADO de la tarea anterior
# (commit db27e13 de la rama del plan). Nada lo frenó ni lo señaló. El agente lo
# hizo porque `composer create-project .` fallaba con «directorio no vacío» y
# quiso vaciarlo — la misma presión que en su día se llevó el `.git`.
#
# La guarda que existía protege sólo la RAÍZ, y `app/` no es la raíz. El
# discriminante correcto no es la profundidad sino ESTO:
#
#   * un árbol NO versionado (vendor/, node_modules/, build/) es un artefacto
#     reconstruible: borrarlo es el caso legítimo para el que se añadió la
#     bandera;
#   * un árbol VERSIONADO es trabajo aceptado de alguien, y borrarlo entero es
#     destruir el deliverable.
#
# El worker publica en el env del contenedor `AGENT_TRACKED_PATHS` con las
# entradas de PRIMER NIVEL versionadas en la rama.


def _files_rastreadas(tmp_path: Path, *rutas: str) -> WorkspaceFiles:
    return WorkspaceFiles(root=str(tmp_path), tracked_paths=rutas)


def _siembra_deliverable(tmp_path: Path) -> Path:
    """El `app/` de CodeIgniter tal como lo dejó la tarea anterior."""
    controllers = tmp_path / "app" / "Controllers"
    controllers.mkdir(parents=True)
    (controllers / "Home.php").write_text("<?php // deliverable", encoding="utf-8")
    return controllers / "Home.php"


def test_recursive_refuses_a_tracked_first_level_directory(tmp_path: Path) -> None:
    """El caso exacto que se midió: `delete_file app --recursive`."""
    entregable = _siembra_deliverable(tmp_path)

    res = _files_rastreadas(tmp_path, "app", "composer.json").file_delete(
        {"path": "app", "recursive": True}
    )

    assert res.ok is False, "se aceptó borrar el árbol versionado"
    assert entregable.exists(), "el deliverable de la tarea anterior se borró igual"


def test_the_tracked_refusal_says_what_to_do_instead(tmp_path: Path) -> None:
    """Un «no» a secas hace que el modelo reintente variantes hasta agotar presupuesto.

    Es lo mismo que ya se aprendió con `.git`: el error tiene que ofrecer la
    operación que SÍ resuelve la intención real (retirar unos ficheros
    concretos), no sólo cerrar la puerta.
    """
    _siembra_deliverable(tmp_path)

    res = _files_rastreadas(tmp_path, "app").file_delete({"path": "app", "recursive": True})

    error = (res.error or "").lower()
    assert "app" in error, "no dice qué ruta rechazó"
    assert "tracked" in error or "versionad" in error, "no explica POR QUÉ es distinto"
    assert "committed" in error or "deliverable" in error, "no explica la consecuencia"
    assert "specific file" in error, "no ofrece la salida (borrar los ficheros concretos)"


def test_recursive_still_removes_an_untracked_build_tree(tmp_path: Path) -> None:
    """El caso legítimo por el que existe la bandera no puede romperse.

    `vendor/` está en `.gitignore`, luego no llega en `AGENT_TRACKED_PATHS`:
    reinstalar dependencias sigue siendo una sola llamada.
    """
    (tmp_path / "vendor" / "pkg").mkdir(parents=True)
    (tmp_path / "vendor" / "pkg" / "a.php").write_text("<?php", encoding="utf-8")

    res = _files_rastreadas(tmp_path, "app", "composer.json").file_delete(
        {"path": "vendor", "recursive": True}
    )

    assert res.ok is True, f"se bloqueó un artefacto reconstruible: {res.error}"
    assert not (tmp_path / "vendor").exists()


def test_deleting_one_tracked_file_is_still_allowed(tmp_path: Path) -> None:
    """El caso ORIGINAL del ADR 0089 no puede caer de rebote.

    Reconciliar un deliverable rancio es borrar UN fichero versionado; si la
    guarda nueva lo bloqueara, cerraría la razón por la que existe la tool.
    """
    (tmp_path / "composer.json").write_text("{}", encoding="utf-8")
    entregable = _siembra_deliverable(tmp_path)
    files = _files_rastreadas(tmp_path, "app", "composer.json")

    assert files.file_delete({"path": "composer.json"}).ok is True
    assert files.file_delete({"path": "app/Controllers/Home.php"}).ok is True
    assert not entregable.exists()


def test_a_subtree_inside_a_tracked_directory_is_not_blocked(tmp_path: Path) -> None:
    """Lo que no llega en la lista no está versionado, y se puede retirar.

    El worker publica TODOS los directorios versionados; `app/Modules/Foo` no
    está entre ellos porque lo creó este mismo run. Ahí vive un caso legítimo
    —retirar entero un módulo mal andamiado—: bloquearlo convertiría la guarda
    en un estorbo, que es como el agente aprende a rodearla (misma lección que
    `.gitignore` frente a `.git`).
    """
    modulo = tmp_path / "app" / "Modules" / "Foo"
    modulo.mkdir(parents=True)
    (modulo / "Foo.php").write_text("<?php", encoding="utf-8")

    res = _files_rastreadas(tmp_path, "app").file_delete(
        {"path": "app/Modules/Foo", "recursive": True}
    )

    assert res.ok is True, f"se bloqueó un subárbol interno: {res.error}"
    assert not modulo.exists()
    assert (tmp_path / "app").is_dir()


def test_without_tracked_paths_the_new_protection_does_not_apply(tmp_path: Path) -> None:
    """Compatibilidad hacia atrás, tal como fija el contrato.

    Env vacía o ausente => el runtime no aplica la protección. Un stack a medio
    desplegar (worker viejo, imagen nueva) se comporta como antes en vez de
    rechazar borrados legítimos que nadie sabría explicar.
    """
    _siembra_deliverable(tmp_path)

    res = WorkspaceFiles(root=str(tmp_path)).file_delete({"path": "app", "recursive": True})

    assert res.ok is True
    assert not (tmp_path / "app").exists()


def test_tracked_entries_are_normalised_before_comparing(tmp_path: Path) -> None:
    """Lo que llega del env viene con ruido: barras finales, líneas en blanco.

    Si la comparación fuera literal, `app/` no casaría con `app` y la guarda
    pasaría en vacío sin que nada lo dijera — el peor modo de fallo, porque el
    test verde y el borrado también.
    """
    _siembra_deliverable(tmp_path)

    res = WorkspaceFiles(
        root=str(tmp_path), tracked_paths=["app/", "  system  ", "", "public"]
    ).file_delete({"path": "app", "recursive": True})

    assert res.ok is False, "la normalización dejó la guarda sin efecto"
    assert (tmp_path / "app").is_dir()


def test_tracked_paths_given_as_the_raw_env_block_still_protect(tmp_path: Path) -> None:
    """El contrato es un bloque con saltos de línea, y un `str` también es iterable.

    Si el constructor lo tratase como iterable de CARACTERES, el conjunto sería
    {'a','p','n',...} y NINGUNA ruta casaría: la guarda quedaría muerta y en
    verde. Se fija aquí porque ese fallo no da error, da un borrado.
    """
    _siembra_deliverable(tmp_path)

    res = WorkspaceFiles(
        root=str(tmp_path), tracked_paths="app\nsystem\npublic\ncomposer.json"
    ).file_delete({"path": "app", "recursive": True})

    assert res.ok is False
    assert (tmp_path / "app").is_dir()


def test_tracked_guard_also_covers_the_dotted_spellings(tmp_path: Path) -> None:
    """`./app` y `app/.` son la misma carpeta; la guarda mira la ruta resuelta."""
    _siembra_deliverable(tmp_path)
    files = _files_rastreadas(tmp_path, "app")

    for ruta in ("./app", "app/", "app/."):
        res = files.file_delete({"path": ruta, "recursive": True})
        assert res.ok is False, f"se coló por la grafía {ruta!r}"
    assert (tmp_path / "app").is_dir()


def test_the_root_and_git_guards_survive_the_tracked_one(tmp_path: Path) -> None:
    """Las dos guardas anteriores siguen en pie con rutas versionadas cargadas."""
    _siembra_deliverable(tmp_path)
    (tmp_path / ".git").write_text("gitdir: /data/repos/p.git/worktrees/abc\n", encoding="utf-8")
    files = _files_rastreadas(tmp_path, "app", ".git")

    raiz = files.file_delete({"path": ".", "recursive": True})
    assert raiz.ok is False and "workspace root" in (raiz.error or "")

    git = files.file_delete({"path": ".git", "recursive": True})
    assert git.ok is False and ".git" in (git.error or "")
    assert (tmp_path / ".git").exists()


# ---------------------------------------------------------------------------
# La guarda cubre CUALQUIER directorio versionado, no sólo el primer nivel
# ---------------------------------------------------------------------------
# Auditoría del 2026-09-01. La primera versión protegía sólo la raíz del árbol
# versionado («es de primer nivel; no persigue a quien insista»). Con eso, el
# destrozo de los 85 ficheros se reconstruye con una llamada por subdirectorio:
# `delete_file app/Config`, `delete_file app/Controllers`… El worker publica
# ahora TODOS los directorios versionados (`git ls-tree -r -d`), y la guarda:
#
#   * rechaza borrar recursivamente cualquier directorio de la lista, y
#     cualquier directorio que CONTENGA uno de la lista;
#   * deja mover un directorio versionado anidado (es un refactor, no una
#     demolición), pero la protección SIGUE AL CONTENIDO: el destino entra en
#     la lista, así que «mover a un temporal y borrar el temporal» se rechaza;
#   * sigue rechazando mover o pisar un árbol versionado de PRIMER NIVEL (ADR
#     0164): ésa es la forma de vaciar la raíz, y no tiene lectura de refactor.
#
# Lo que NO está en la lista no está versionado (lo creó este run): se borra y
# se mueve como siempre.


def _siembra_config(tmp_path: Path) -> Path:
    config = tmp_path / "app" / "Config"
    config.mkdir(parents=True)
    (config / "App.php").write_text("<?php // config", encoding="utf-8")
    return config


def test_recursive_refuses_a_tracked_nested_directory(tmp_path: Path) -> None:
    _siembra_deliverable(tmp_path)
    config = _siembra_config(tmp_path)

    res = _files_rastreadas(tmp_path, "app", "app/Config", "app/Controllers").file_delete(
        {"path": "app/Config", "recursive": True}
    )

    assert res.ok is False, "se aceptó borrar un directorio versionado anidado"
    assert "tracked" in (res.error or "")
    assert (config / "App.php").exists()


def test_recursive_refuses_a_directory_that_contains_tracked_ones(tmp_path: Path) -> None:
    """Mover a un temporal y borrar el temporal es el mismo destrozo en dos pasos."""
    _siembra_config(tmp_path)
    files = _files_rastreadas(tmp_path, "app", "app/Config")

    movido = files.file_move({"source": "app/Config", "destination": "tmp/Config"})
    assert movido.ok is True, movido.error

    borrado = files.file_delete({"path": "tmp", "recursive": True})
    assert borrado.ok is False, "se borró un temporal que contenía trabajo versionado"
    assert (tmp_path / "tmp" / "Config" / "App.php").exists()

    directo = files.file_delete({"path": "tmp/Config", "recursive": True})
    assert directo.ok is False, "la protección no siguió al directorio movido"


def test_moving_a_tracked_nested_directory_is_a_refactor(tmp_path: Path) -> None:
    """Renombrar `app/Config` a `app/Settings` no destruye nada: se permite."""
    _siembra_config(tmp_path)
    files = _files_rastreadas(tmp_path, "app", "app/Config")

    res = files.file_move({"source": "app/Config", "destination": "app/Settings"})

    assert res.ok is True, res.error
    assert (tmp_path / "app" / "Settings" / "App.php").is_file()
    # ...y el destino hereda la protección.
    assert files.file_delete({"path": "app/Settings", "recursive": True}).ok is False


def test_overwrite_refuses_a_tracked_nested_tree(tmp_path: Path) -> None:
    _siembra_config(tmp_path)
    (tmp_path / "ci4tmp" / "app" / "Config").mkdir(parents=True)
    (tmp_path / "ci4tmp" / "app" / "Config" / "App.php").write_text(
        "<?php // nuevo", encoding="utf-8"
    )

    res = _files_rastreadas(tmp_path, "app", "app/Config").file_move(
        {"source": "ci4tmp/app/Config", "destination": "app/Config", "overwrite": True}
    )

    assert res.ok is False
    assert (tmp_path / "app" / "Config" / "App.php").read_text(
        encoding="utf-8"
    ) == "<?php // config"


def test_an_untracked_module_created_this_run_is_still_removable(tmp_path: Path) -> None:
    """Lo que no está en la lista no está versionado: el caso legítimo sigue abierto."""
    modulo = tmp_path / "app" / "Modules" / "Foo"
    modulo.mkdir(parents=True)
    (modulo / "Foo.php").write_text("<?php", encoding="utf-8")

    res = _files_rastreadas(tmp_path, "app", "app/Config").file_delete(
        {"path": "app/Modules/Foo", "recursive": True}
    )

    assert res.ok is True, res.error
    assert not modulo.exists()


def test_a_first_level_tracked_tree_still_cannot_be_moved_away(tmp_path: Path) -> None:
    """La frontera del ADR 0164 no se relaja por cubrir más profundidad."""
    _siembra_config(tmp_path)

    res = _files_rastreadas(tmp_path, "app", "app/Config").file_move(
        {"source": "app", "destination": "tmp/app"}
    )

    assert res.ok is False
    assert (tmp_path / "app" / "Config" / "App.php").exists()


# ---------------------------------------------------------------------------
# El descarte da permiso de escritura y reintenta antes de dejar un residuo
# ---------------------------------------------------------------------------
# Auditoría del 2026-09-01. Un residuo `.agent-runtime-tmp.*` que no se pudo
# descartar tenía un coste que nadie había medido: el `git clean -fdx` de la
# provisión siguiente intenta borrarlo, no puede, y sale con rc=1 — la tarea
# queda `workspace_unavailable` en cada reintento. Los dos motivos por los que
# `rmtree` no puede (fichero de sólo lectura; directorio sin permiso de
# escritura) se resuelven dando permiso y repitiendo.


def test_recursive_delete_removes_a_read_only_file_without_residue(tmp_path: Path) -> None:
    (tmp_path / "build" / "pkg").mkdir(parents=True)
    fichero = tmp_path / "build" / "pkg" / "a.o"
    fichero.write_bytes(b"\x00")
    os.chmod(fichero, stat.S_IREAD)
    if os.name != "nt":
        os.chmod(tmp_path / "build" / "pkg", stat.S_IRUSR | stat.S_IXUSR)

    res = _files(tmp_path).file_delete({"path": "build", "recursive": True})

    assert res.ok is True, res.error
    assert not (tmp_path / "build").exists()
    residuos = [
        hijo.name for hijo in tmp_path.iterdir() if hijo.name.startswith(".agent-runtime-tmp.")
    ]
    assert residuos == [], (
        f"quedó un residuo que la provisión siguiente no podrá barrer: {residuos}"
    )


# ---------------------------------------------------------------------------
# `list_files` sin `path`: la operación más obvia que existe, y era un error
# ---------------------------------------------------------------------------
# Mismo run del 2026-08-31 (execution 01a05881-89d7-79fa-be72-bd0e7c1a9fbb):
# CATORCE de sus 22 `list_files` fueron rechazadas con
#
#     "a non-empty 'path' is required"
#
# El agente quería listar el workspace y se comió el 60% del presupuesto
# chocando contra un requisito de FORMA. Los tokens de entrada eran planos
# (~4.200 por llamada): no es que el contexto creciera, es que giraba en vacío.
# En la llamada 71 descubrió que tenía que pasar "." y funcionó a la primera.
#
# La forma exacta la da el `steps_log` de esa ejecución, y es la que explica por
# qué el defecto sobrevivió a una lectura del código:
#
#     {"path": "", "pattern": "*"}      x12
#     {"path": "", "pattern": "**/*"}   x2
#
# El modelo NO omite la clave: la manda VACÍA. La clave ausente ya funcionaba
# —`args.get("path", ".")`—, así que quien mirara el fuente concluiría que el
# caso estaba cubierto. El hueco era la cadena vacía, y con ella el `None`.
#
# La corrección va en `file_list`, NO en `_safe_path`: ese resolvedor lo
# comparten read/write/delete, donde un path vacío SÍ tiene que seguir siendo un
# error — «escribe lo que sea» o «borra lo que sea» no tienen interpretación
# obvia, y adivinarla ahí convertiría un error de forma en un borrado.


def test_list_without_path_lists_the_workspace_root(tmp_path: Path) -> None:
    (tmp_path / "composer.json").write_text("{}", encoding="utf-8")
    (tmp_path / "app").mkdir()

    res = _files(tmp_path).file_list({})

    assert res.ok is True, f"listar el workspace seguía siendo un error: {res.error}"
    assert res.output is not None
    assert {e["name"] for e in res.output["entries"]} == {"composer.json", "app"}
    assert res.output["path"] == ".", "el resultado no dice qué ruta se listó de verdad"


def test_list_with_a_blank_path_lists_the_workspace_root(tmp_path: Path) -> None:
    """Vacío o sólo espacios es la misma intención que omitirlo."""
    (tmp_path / "README.md").write_text("# hola", encoding="utf-8")

    for ruta in ("", "   ", None):
        res = _files(tmp_path).file_list({"path": ruta})
        assert res.ok is True, f"path={ruta!r} seguía fallando: {res.error}"
        assert res.output is not None
        assert [e["name"] for e in res.output["entries"]] == ["README.md"]


def test_list_with_an_explicit_path_is_unchanged(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "Home.php").write_text("<?php", encoding="utf-8")

    res = _files(tmp_path).file_list({"path": "app"})

    assert res.ok is True
    assert res.output is not None
    assert res.output["path"] == "app"
    assert [e["name"] for e in res.output["entries"]] == ["Home.php"]


def test_read_write_and_delete_still_require_a_path(tmp_path: Path) -> None:
    """La otra mitad del arreglo, y la que impide que se propague de más.

    Si el default por defecto viviera en `_safe_path`, `delete_file {}` pasaría
    a significar «borra el workspace» y `write_file {}` «escribe en la raíz».
    Aquí se fija que el default es de `file_list` y de nadie más.
    """
    files = _files(tmp_path)

    for llamada in (files.file_read, files.file_write, files.file_delete):
        res = llamada({})
        assert res.ok is False, f"{llamada.__name__} aceptó una llamada sin 'path'"
        assert "non-empty 'path'" in (res.error or "")

    for llamada in (files.file_read, files.file_write, files.file_delete):
        res = llamada({"path": "   "})
        assert res.ok is False, f"{llamada.__name__} aceptó un 'path' en blanco"
        assert "non-empty 'path'" in (res.error or "")


# ---------------------------------------------------------------------------
# El contrato tiene que LLEGAR: env -> constructor -> guarda
# ---------------------------------------------------------------------------
# Sin este test, todo lo de arriba puede estar verde y la protección seguir
# muerta en producción: basta con que nadie lea `AGENT_TRACKED_PATHS` al montar
# la familia `file`. Es el modo de fallo que más caro sale —una guarda que pasa
# en vacío— porque no da error, da un borrado.


def test_the_worker_contract_reaches_the_tool_through_the_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ejercita la ruta REAL: la tool se invoca por su nombre canónico."""
    _siembra_deliverable(tmp_path)
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_TRACKED_PATHS", "app\nsystem\npublic\ncomposer.json")

    registry = ToolRegistry()
    register_builtin_families(
        registry,
        api=None,
        sink=OrchestrationSink(),
        flags={f: f == FAMILY_FILE for f in ALL_FAMILIES},
    )

    res = registry.call("delete_file", {"path": "app", "recursive": True})

    assert res.ok is False, "la familia `file` se montó sin las rutas versionadas"
    assert (tmp_path / "app" / "Controllers" / "Home.php").exists()


def test_an_absent_env_leaves_the_tool_permissive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker anterior al contrato: se comporta como antes, no rechaza de más."""
    _siembra_deliverable(tmp_path)
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("AGENT_TRACKED_PATHS", raising=False)

    registry = ToolRegistry()
    register_builtin_families(
        registry,
        api=None,
        sink=OrchestrationSink(),
        flags={f: f == FAMILY_FILE for f in ALL_FAMILIES},
    )

    assert registry.call("delete_file", {"path": "app", "recursive": True}).ok is True


def test_list_files_wired_without_a_path_lists_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La otra mitad, por la misma puerta por la que la llama el agente."""
    (tmp_path / "composer.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))

    registry = ToolRegistry()
    register_builtin_families(
        registry,
        api=None,
        sink=OrchestrationSink(),
        flags={f: f == FAMILY_FILE for f in ALL_FAMILIES},
    )

    # La forma medida en vivo: la clave presente y VACÍA, con su `pattern`.
    res = registry.call("list_files", {"path": "", "pattern": "*"})

    assert res.ok is True, f"la llamada que quemó 14 turnos sigue fallando: {res.error}"
    assert res.output is not None
    assert [e["name"] for e in res.output["entries"]] == ["composer.json"]


# ---------------------------------------------------------------------------
# `move_file`: el paso que faltaba del plan que el propio agente encontró
# ---------------------------------------------------------------------------
# Medido en vivo el 2026-08-31, proyecto `Hello World CI4 v3` del tenant
# mediapro, modelo gpt-oss:120b. Segundo run, sobre el worktree que YA tenía
# CodeIgniter instalado y commiteado por la tarea anterior:
#
#      3 | composer create-project codeigniter4/framework .      -> "no vacío"
#     31 | composer create-project codeigniter4/framework tmpci  -> OK
#     35 | delete_file {"path":"app","recursive":true}           -> OK, 85 FICHEROS
#     39 | mkdir ci4tmp                                          -> BLOQUEADO
#     51 | composer create-project codeigniter4/framework .      -> sigue fallando
#
# El paso 31 es la clave y es lo que motiva esta tool: el agente llegó SOLO a la
# solución correcta —instalar en un directorio temporal y mover el resultado— y
# NO PUDO COMPLETARLA, porque la familia `file` era exactamente read/write/
# delete/list. De los tres pasos de su plan (instalar aparte, mover, limpiar) el
# único ejecutable era el destructivo. Así que ejecutó el destructivo.
#
# Y no vale «ábrele `mv` en el allowlist de shell_exec»: es el mismo argumento
# que se usó para el `delete_file` recursivo — `shell_exec` es la puerta
# equivocada del ADR 0162, `mv` es ilimitado por naturaleza, y por aquí queda
# AUDITADO en el `steps_log` y gateado como `code_changes`.


def _ci4_temporal(tmp_path: Path, nombre: str = "ci4tmp") -> Path:
    """Lo que deja `composer create-project codeigniter4/framework <nombre>`."""
    raiz = tmp_path / nombre
    (raiz / "app" / "Controllers").mkdir(parents=True)
    (raiz / "app" / "Controllers" / "Home.php").write_text("<?php // skeleton", encoding="utf-8")
    (raiz / "spark").write_text("#!/usr/bin/env php", encoding="utf-8")
    return raiz


def test_move_renames_a_file(tmp_path: Path) -> None:
    origen = tmp_path / "Home.php"
    origen.write_text("<?php // v1", encoding="utf-8")

    res = _files(tmp_path).file_move({"source": "Home.php", "destination": "app/Home.php"})

    assert res.ok is True, res.error
    assert not origen.exists()
    assert (tmp_path / "app" / "Home.php").read_text(encoding="utf-8") == "<?php // v1"


def test_move_moves_a_whole_tree(tmp_path: Path) -> None:
    _ci4_temporal(tmp_path)

    res = _files(tmp_path).file_move({"source": "ci4tmp", "destination": "framework"})

    assert res.ok is True, res.error
    assert not (tmp_path / "ci4tmp").exists()
    assert (tmp_path / "framework" / "app" / "Controllers" / "Home.php").is_file()


def test_move_creates_the_missing_intermediate_directories(tmp_path: Path) -> None:
    """`mkdir` está BLOQUEADO por el allowlist — paso 39 del run medido.

    Si la tool exigiera que el directorio padre existiera, el agente volvería a
    quedarse exactamente donde se quedó: con el plan correcto y sin la
    herramienta para ejecutarlo.
    """
    (tmp_path / "Home.php").write_text("<?php", encoding="utf-8")

    res = _files(tmp_path).file_move(
        {"source": "Home.php", "destination": "app/Controllers/Home.php"}
    )

    assert res.ok is True, res.error
    assert (tmp_path / "app" / "Controllers" / "Home.php").is_file()


def test_move_is_auditable_in_the_steps_log(tmp_path: Path) -> None:
    """Un movimiento silencioso es lo mismo que un `rm` silencioso.

    El `steps_log` tiene que decir QUÉ se movió, A DÓNDE y —si era un árbol—
    CUÁNTAS entradas se llevó, igual que `delete_file`. Sin el recuento, «se
    movió ci4tmp» no distingue entre mover un directorio vacío y mover 85
    ficheros del deliverable.
    """
    _ci4_temporal(tmp_path)

    res = _files(tmp_path).file_move({"source": "./ci4tmp/", "destination": "framework"})

    assert res.ok is True, res.error
    assert res.output is not None
    # Se registran las rutas EFECTIVAS (relativas al workspace, normalizadas),
    # no la grafía que mandó el modelo: `./ci4tmp/` y `ci4tmp` son la misma
    # carpeta y el log tiene que poder leerse sin resolverlo a mano.
    assert res.output["source"] == "ci4tmp"
    assert res.output["destination"] == "framework"
    assert res.output["moved"] is True
    # app, app/Controllers, app/Controllers/Home.php, spark
    assert res.output["entries"] == 4, f"el recuento no cuadra: {res.output}"


def test_moving_a_single_file_reports_no_entry_count(tmp_path: Path) -> None:
    """`entries` sólo tiene sentido en un árbol; en un fichero sobra y confunde."""
    (tmp_path / "a.php").write_text("<?php", encoding="utf-8")

    res = _files(tmp_path).file_move({"source": "a.php", "destination": "b.php"})

    assert res.ok is True, res.error
    assert res.output is not None
    assert "entries" not in res.output


def test_move_missing_source_fails_cleanly(tmp_path: Path) -> None:
    res = _files(tmp_path).file_move({"source": "nope", "destination": "otro"})
    assert res.ok is False
    assert "not found" in (res.error or "")


def test_move_requires_both_ends_and_names_the_missing_one(tmp_path: Path) -> None:
    """La lección de `list_files`: un error de FORMA que no dice cuál quema turnos.

    Catorce llamadas se perdieron contra «a non-empty 'path' is required» porque
    el mensaje no decía qué arreglar. Con DOS argumentos el riesgo se dobla, así
    que el error nombra el extremo concreto.
    """
    (tmp_path / "a.php").write_text("<?php", encoding="utf-8")
    files = _files(tmp_path)

    res = files.file_move({"destination": "b.php"})
    assert res.ok is False
    assert "source" in (res.error or ""), res.error

    res = files.file_move({"source": "a.php"})
    assert res.ok is False
    assert "destination" in (res.error or ""), res.error

    res = files.file_move({"source": "a.php", "destination": "   "})
    assert res.ok is False
    assert "destination" in (res.error or ""), res.error
    assert (tmp_path / "a.php").exists()


# --- La jaula de ruta vale en LOS DOS extremos ------------------------------


def test_move_source_escaping_the_workspace_is_rejected(tmp_path: Path) -> None:
    fuera = tmp_path.parent / "secreto_origen.txt"
    fuera.write_text("no tocar", encoding="utf-8")
    try:
        res = _files(tmp_path).file_move(
            {"source": "../secreto_origen.txt", "destination": "robado.txt"}
        )
        assert res.ok is False
        assert "escapes the workspace" in (res.error or "")
        assert "source" in (res.error or ""), "no dice qué extremo rechazó"
        assert fuera.exists()
        assert not (tmp_path / "robado.txt").exists()
    finally:
        fuera.unlink()


def test_move_destination_escaping_the_workspace_is_rejected(tmp_path: Path) -> None:
    """El extremo que una jaula puesta sólo en el origen dejaría abierto."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "Home.php").write_text("<?php", encoding="utf-8")
    fuera = tmp_path.parent / "fuga_destino"

    res = _files(tmp_path).file_move({"source": "app", "destination": "../fuga_destino"})

    assert res.ok is False
    assert "escapes the workspace" in (res.error or "")
    assert "destination" in (res.error or ""), "no dice qué extremo rechazó"
    assert not fuera.exists(), "se escribió fuera del workspace"
    assert (tmp_path / "app" / "Home.php").exists()


def test_move_rejects_absolute_paths_on_either_end(tmp_path: Path) -> None:
    (tmp_path / "a.php").write_text("<?php", encoding="utf-8")
    absoluta = str(tmp_path.parent / "absoluto.txt")
    files = _files(tmp_path)

    assert files.file_move({"source": absoluta, "destination": "a2.php"}).ok is False
    assert files.file_move({"source": "a.php", "destination": absoluta}).ok is False
    assert not Path(absoluta).exists()
    assert (tmp_path / "a.php").exists()


# --- `.git` es intocable también por aquí (ADR 0163) ------------------------


def test_move_refuses_the_worktree_git_link_as_source(tmp_path: Path) -> None:
    """Mover el puntero es exactamente igual de destructivo que borrarlo.

    Y es la salida obvia para quien acaba de chocar con la guarda de
    `delete_file`: si `move_file` no lo cubriera, la protección del ADR 0163
    tendría una puerta de al lado.
    """
    enlace = _worktree_git_file(tmp_path)

    res = _files(tmp_path).file_move({"source": ".git", "destination": "git_backup"})

    assert res.ok is False
    assert ".git" in (res.error or "")
    assert enlace.exists()
    assert not (tmp_path / "git_backup").exists()


def test_move_refuses_a_destination_inside_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
    (tmp_path / "roto.txt").write_text("basura", encoding="utf-8")

    res = _files(tmp_path).file_move({"source": "roto.txt", "destination": ".git/HEAD"})

    assert res.ok is False
    assert (tmp_path / ".git" / "HEAD").read_text(encoding="utf-8") == "ref: refs/heads/master\n"
    assert (tmp_path / "roto.txt").exists()


def test_move_leaves_gitignore_alone(tmp_path: Path) -> None:
    """La guarda casa el SEGMENTO `.git`, no un prefijo — igual que en delete."""
    (tmp_path / ".gitignore").write_text("vendor/\n", encoding="utf-8")

    res = _files(tmp_path).file_move({"source": ".gitignore", "destination": "config/.gitignore"})

    assert res.ok is True, res.error
    assert (tmp_path / "config" / ".gitignore").is_file()


# --- La raíz del workspace, en los dos extremos -----------------------------


def test_move_refuses_the_workspace_root_as_source(tmp_path: Path) -> None:
    """Mover la raíz vacía el workspace: es «el deliverable», no «un árbol»."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "Home.php").write_text("<?php", encoding="utf-8")

    for ruta in (".", "./", "app/.."):
        res = _files(tmp_path).file_move({"source": ruta, "destination": "viejo"})
        assert res.ok is False, f"se aceptó mover la raíz con source={ruta!r}"
        assert "workspace root" in (res.error or "")
    assert (tmp_path / "app" / "Home.php").exists()


def test_move_onto_the_workspace_root_says_to_move_entry_by_entry(tmp_path: Path) -> None:
    """El caso EXACTO del run medido, y por eso el error tiene que enseñar la vía.

    `composer create-project ... ci4tmp` deja el framework en `ci4tmp/`, y lo que
    el agente quiere es «vuelca esto en la raíz». Eso no es UN movimiento: es
    fusionar dos árboles, con la mitad de las colisiones apuntando al deliverable
    versionado. Se rechaza, pero diciendo qué SÍ funciona — entrada por entrada.
    """
    _ci4_temporal(tmp_path)

    res = _files(tmp_path).file_move({"source": "ci4tmp", "destination": "."})

    assert res.ok is False
    error = (res.error or "").lower()
    assert "workspace root" in error
    assert "one entry at a time" in error, f"no ofrece la salida: {res.error}"
    assert (tmp_path / "ci4tmp" / "spark").exists()


# --- Sobrescribir NO es el default: se pide con `overwrite` -----------------


def test_move_refuses_an_existing_destination_by_default(tmp_path: Path) -> None:
    """`mv` significa dos cosas distintas según exista o no el destino.

    Esa ambigüedad es justo lo que no puede tener una tool que un modelo invoca
    a ciegas: aquí `destination` es SIEMPRE la ruta final, y reemplazar lo que ya
    está es la variante DESTRUCTIVA — se pide a propósito, igual que `recursive`
    en `delete_file`, y no se hereda del caso normal.
    """
    (tmp_path / "nuevo.php").write_text("<?php // nuevo", encoding="utf-8")
    (tmp_path / "viejo.php").write_text("<?php // viejo", encoding="utf-8")

    res = _files(tmp_path).file_move({"source": "nuevo.php", "destination": "viejo.php"})

    assert res.ok is False
    assert "exists" in (res.error or "")
    assert "overwrite=true" in (res.error or ""), "no dice cómo pedirlo a propósito"
    assert (tmp_path / "viejo.php").read_text(encoding="utf-8") == "<?php // viejo"
    assert (tmp_path / "nuevo.php").exists(), "el origen se perdió en un movimiento fallido"


def test_overwrite_is_an_explicit_opt_in(tmp_path: Path) -> None:
    """Y SÓLO esa bandera lo activa: nada de sinónimos que el modelo improvise.

    El esquema del catálogo declara `additionalProperties: false` con `overwrite`
    como única opcional. Si el runtime aceptara además `force` o `replace`,
    tendría una puerta destructiva que el esquema no anuncia y que por tanto
    nadie revisó.
    """
    (tmp_path / "nuevo.php").write_text("<?php // nuevo", encoding="utf-8")
    (tmp_path / "viejo.php").write_text("<?php // viejo", encoding="utf-8")
    files = _files(tmp_path)

    for bandera in ("force", "replace"):
        res = files.file_move({"source": "nuevo.php", "destination": "viejo.php", bandera: True})
        assert res.ok is False, f"la bandera {bandera!r} activó la sobrescritura"
        assert (tmp_path / "viejo.php").read_text(encoding="utf-8") == "<?php // viejo"

    res = files.file_move({"source": "nuevo.php", "destination": "viejo.php", "overwrite": True})
    assert res.ok is True, res.error
    assert (tmp_path / "viejo.php").read_text(encoding="utf-8") == "<?php // nuevo"
    assert not (tmp_path / "nuevo.php").exists()


def test_overwrite_says_in_the_log_what_it_destroyed(tmp_path: Path) -> None:
    """Reemplazar un árbol es un `rm -rf` con otro nombre: tiene que constar.

    Sin el recuento, «se movió framework a vendor» no distingue entre pisar un
    directorio vacío y evaporar 85 ficheros — y es justo el número que hizo
    legible el incidente cuando se leyó el `steps_log` a posteriori.
    """
    _ci4_temporal(tmp_path)
    (tmp_path / "vendor" / "pkg").mkdir(parents=True)
    (tmp_path / "vendor" / "pkg" / "a.php").write_text("<?php", encoding="utf-8")

    res = _files(tmp_path).file_move(
        {"source": "ci4tmp", "destination": "vendor", "overwrite": True}
    )

    assert res.ok is True, res.error
    assert res.output is not None
    assert res.output["replaced"] is True
    assert res.output["replaced_entries"] == 2, f"no dice qué se llevó: {res.output}"
    assert (tmp_path / "vendor" / "spark").is_file()
    assert not (tmp_path / "vendor" / "pkg").exists()


def test_a_plain_move_does_not_claim_it_replaced_anything(tmp_path: Path) -> None:
    (tmp_path / "a.php").write_text("<?php", encoding="utf-8")

    res = _files(tmp_path).file_move({"source": "a.php", "destination": "b.php"})

    assert res.ok is True, res.error
    assert res.output is not None
    assert "replaced" not in res.output


def test_overwrite_still_refuses_a_tracked_first_level_tree(tmp_path: Path) -> None:
    """La decisión sobre el DESTINO versionado, con el caso que la obliga.

    Run 2 del 2026-08-31: `ci4tmp/app` recién generado por composer, y `app/` con
    las 85 entradas commiteadas por la tarea anterior. `move_file ci4tmp/app app
    --overwrite` reemplazaría el deliverable por el esqueleto por defecto: el
    mismo destrozo que `delete_file app --recursive`, con otro nombre y sin
    dejarlo escrito como borrado.

    Por eso la guarda de `AGENT_TRACKED_PATHS` mira los DOS extremos. Si mirase
    sólo el origen, la bandera de sobrescritura sería literalmente el rodeo a la
    guarda que este mismo día se puso para impedir el destrozo.
    """
    _siembra_deliverable(tmp_path)
    _ci4_temporal(tmp_path)

    res = _files_rastreadas(tmp_path, "app", "composer.json").file_move(
        {"source": "ci4tmp/app", "destination": "app", "overwrite": True}
    )

    assert res.ok is False
    error = (res.error or "").lower()
    assert "tracked" in error and "specific file" in error, res.error
    entregable = tmp_path / "app" / "Controllers" / "Home.php"
    assert entregable.read_text(encoding="utf-8") == "<?php // deliverable"
    assert (tmp_path / "ci4tmp" / "app").is_dir(), "el origen se movió a medias"


def test_overwrite_may_replace_a_tracked_file_not_a_tree(tmp_path: Path) -> None:
    """El límite es el ÁRBOL, no cualquier cosa versionada — y no es arbitrario.

    Sobrescribir un fichero versionado ya se puede con `write_file`, que es la
    forma normal de editar código: bloquearlo aquí no protegería nada y sí
    convertiría la guarda en un estorbo, que es como el agente aprende a
    rodearla. Un árbol entero, en cambio, no tiene equivalente por `write_file`.
    """
    (tmp_path / "composer.json").write_text('{"viejo": true}', encoding="utf-8")
    (tmp_path / "ci4tmp").mkdir()
    (tmp_path / "ci4tmp" / "composer.json").write_text('{"nuevo": true}', encoding="utf-8")

    res = _files_rastreadas(tmp_path, "app", "composer.json").file_move(
        {"source": "ci4tmp/composer.json", "destination": "composer.json", "overwrite": True}
    )

    assert res.ok is True, res.error
    assert (tmp_path / "composer.json").read_text(encoding="utf-8") == '{"nuevo": true}'


def test_overwrite_may_replace_an_untracked_build_tree(tmp_path: Path) -> None:
    """El caso legítimo: pisar `vendor/`, que es reconstruible."""
    _ci4_temporal(tmp_path)
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "viejo.php").write_text("<?php", encoding="utf-8")

    res = _files_rastreadas(tmp_path, "app", "composer.json").file_move(
        {"source": "ci4tmp", "destination": "vendor", "overwrite": True}
    )

    assert res.ok is True, f"se bloqueó un artefacto reconstruible: {res.error}"
    assert (tmp_path / "vendor" / "spark").is_file()


# --- La guarda de rutas versionadas aplica A LOS DOS EXTREMOS ---------------
# ORIGEN: mover un árbol de primer nivel versionado es igual de destructivo que
# borrarlo — desaparece de donde estaba, y `git add -A` lo registra como borrado
# más un añadido en otro sitio. Si la guarda sólo mirase a `delete_file`, la
# forma de vaciar `app/` sería `move_file app basura` y habríamos movido el
# agujero, no cerrado.
#
# DESTINO: sólo puede pisarse con `overwrite`, y ahí la guarda vuelve a aplicar
# (arriba, `test_overwrite_still_refuses_a_tracked_first_level_tree`). Las dos
# mitades son la misma frase: un árbol versionado de primer nivel no se retira
# de su sitio por esta tool, ni sacándolo ni tapándolo.


def test_move_refuses_a_tracked_first_level_tree(tmp_path: Path) -> None:
    entregable = _siembra_deliverable(tmp_path)

    res = _files_rastreadas(tmp_path, "app", "composer.json").file_move(
        {"source": "app", "destination": "app_viejo"}
    )

    assert res.ok is False, "se aceptó mover el árbol versionado fuera de su sitio"
    assert entregable.exists()
    assert not (tmp_path / "app_viejo").exists()


def test_the_tracked_move_refusal_explains_and_offers_a_way_out(tmp_path: Path) -> None:
    _siembra_deliverable(tmp_path)

    res = _files_rastreadas(tmp_path, "app").file_move(
        {"source": "app", "destination": "app_viejo"}
    )

    error = (res.error or "").lower()
    assert "app" in error, "no dice qué ruta rechazó"
    assert "tracked" in error or "versionad" in error, "no explica POR QUÉ es distinto"
    assert "committed" in error or "deliverable" in error, "no explica la consecuencia"
    assert "specific file" in error, "no ofrece la salida (mover los ficheros concretos)"


def test_the_tracked_move_guard_covers_the_dotted_spellings(tmp_path: Path) -> None:
    _siembra_deliverable(tmp_path)
    files = _files_rastreadas(tmp_path, "app")

    for ruta in ("./app", "app/", "app/."):
        res = files.file_move({"source": ruta, "destination": "app_viejo"})
        assert res.ok is False, f"se coló por la grafía {ruta!r}"
    assert (tmp_path / "app" / "Controllers" / "Home.php").exists()


def test_moving_an_untracked_tree_out_of_the_way_still_works(tmp_path: Path) -> None:
    """El caso legítimo por el que existe la tool no puede caer de rebote.

    `ci4tmp/` lo acaba de crear composer en este mismo run: no está versionado,
    luego no llega en `AGENT_TRACKED_PATHS` y moverlo es la operación normal.
    """
    _ci4_temporal(tmp_path)

    res = _files_rastreadas(tmp_path, "app", "composer.json").file_move(
        {"source": "ci4tmp", "destination": "framework"}
    )

    assert res.ok is True, f"se bloqueó un árbol reconstruible: {res.error}"
    assert (tmp_path / "framework" / "spark").is_file()


def test_renaming_one_tracked_file_is_still_allowed(tmp_path: Path) -> None:
    """Mover UN fichero versionado es un refactor normal, no una demolición.

    Es el mismo límite que en `delete_file`, donde borrar un fichero versionado
    sigue permitido (es el caso original del ADR 0089): la guarda mira ÁRBOLES de
    primer nivel, porque el daño medido fue un árbol de primer nivel.
    """
    (tmp_path / "composer.json").write_text("{}", encoding="utf-8")
    files = _files_rastreadas(tmp_path, "app", "composer.json")

    res = files.file_move({"source": "composer.json", "destination": "composer.json.bak"})

    assert res.ok is True, res.error
    assert (tmp_path / "composer.json.bak").is_file()


def test_a_subtree_inside_a_tracked_directory_can_be_moved(tmp_path: Path) -> None:
    """Reorganizar `app/Modules/Foo` —creado en este run— es trabajo normal.

    Bloquearlo convertiría la guarda en un estorbo, que es como el agente
    aprende a rodearla.
    """
    modulo = tmp_path / "app" / "Modules" / "Foo"
    modulo.mkdir(parents=True)
    (modulo / "Foo.php").write_text("<?php", encoding="utf-8")

    res = _files_rastreadas(tmp_path, "app").file_move(
        {"source": "app/Modules/Foo", "destination": "app/Modules/Bar"}
    )

    assert res.ok is True, res.error
    assert (tmp_path / "app" / "Modules" / "Bar" / "Foo.php").is_file()
    assert not modulo.exists()


def test_without_tracked_paths_the_move_guard_does_not_apply(tmp_path: Path) -> None:
    """Compatibilidad hacia atrás, el mismo contrato que en `delete_file`."""
    _siembra_deliverable(tmp_path)

    res = WorkspaceFiles(root=str(tmp_path)).file_move(
        {"source": "app", "destination": "app_viejo"}
    )

    assert res.ok is True, res.error
    assert (tmp_path / "app_viejo" / "Controllers" / "Home.php").is_file()


# --- Las formas degeneradas: origen y destino que se solapan ---------------
# La guarda topológica cubría UN solo sentido (el destino colgando del origen),
# y una verificación adversarial del 2026-08-31 midió DOS VECES lo que se
# escapaba por el otro:
#
#   move_file {"source":"ci4tmp/app","destination":"ci4tmp","overwrite":true}
#     antes: ci4tmp/app/Config.php, ci4tmp/spark   después: []          ok=False
#   move_file {"source":"app/Config/Boot","destination":"app/Config","overwrite":true}
#     antes: 43 entradas bajo app/                 después: app/ VACÍA   ok=False
#
# El destino ERA ANCESTRO del origen: `rmtree(destino)` se llevó el origen por
# delante, el `shutil.move` posterior ya no encontró nada que mover y la tool
# devolvió `ok=False`. Peor que el incidente que vino a resolver: allí el
# borrado al menos constaba en el `steps_log` como un borrado. Aquí el agente
# lee «no se hizo nada» sobre 41 ficheros commiteados que ya no están.
#
# La guarda de árbol versionado no lo tapa —`app/Config` es profundidad 2 y la
# guarda es de PRIMER NIVEL a propósito—, así que la topológica es la única que
# puede rechazarlo.


def test_moving_a_directory_into_itself_fails_without_damage(tmp_path: Path) -> None:
    """Caso degenerado que un `mv` a pelo deja a medias.

    **Este test estaba flojo: fijaba el comportamiento de OTRO módulo.** Sólo
    afirmaba `ok is False`, y `shutil.move` levanta `shutil.Error` por su cuenta
    cuando el destino cuelga del origen — así que borrando la guarda ENTERA los
    67 tests seguían verdes. Un test que pasa con la guarda quitada no la
    protege: la acompaña.

    Lo que se afirma ahora es lo que sólo puede venir de la guarda: el rechazo
    en el vocabulario de este módulo —ruta relativa al workspace, en inglés,
    diciendo dónde está el problema— en vez del texto de `shutil` con las rutas
    absolutas del host.
    """
    _ci4_temporal(tmp_path)

    res = _files(tmp_path).file_move({"source": "ci4tmp", "destination": "ci4tmp/dentro"})

    assert res.ok is False
    assert (tmp_path / "ci4tmp" / "spark").is_file()
    error = res.error or ""
    assert "is inside it" in error, f"no lo rechazó la guarda sino el sistema: {error}"
    assert str(tmp_path) not in error and tmp_path.as_posix() not in error


def test_the_degenerate_shapes_are_refused_by_the_guard_not_by_shutil(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Las TRES formas se rechazan ANTES de tocar el disco, y cada una lo dice.

    Con `_ejecutar_movimiento` cableado a explotar, este test sólo puede pasar
    si ninguna de las tres formas llega al movimiento: es la afirmación
    «rechaza antes de tocar nada», que ningún mensaje de `shutil` puede
    satisfacer porque `shutil` sólo se pronuncia una vez ya está trabajando.

    Y cada forma exige SU fragmento, para que quitar media guarda mate este test
    y no otro: sin las tres afirmaciones, un solapamiento de tres sentidos queda
    fijado por un único caso.
    """
    _ci4_temporal(tmp_path)
    formas = (
        # (origen, destino, lo que el error tiene que explicar)
        ("ci4tmp", "ci4tmp/dentro", "is inside it"),  # el destino cuelga del origen
        ("ci4tmp/app", "ci4tmp", "contains it"),  # el origen cuelga del destino
        ("ci4tmp", "ci4tmp", "the same path"),  # la misma ruta
    )

    def _no_deberia_llegar(*_args: object, **_kwargs: object) -> ToolResult:
        raise AssertionError("la forma degenerada llegó hasta el movimiento")

    monkeypatch.setattr(WorkspaceFiles, "_ejecutar_movimiento", _no_deberia_llegar)
    files = _files(tmp_path)

    for origen, destino, explicacion in formas:
        res = files.file_move({"source": origen, "destination": destino, "overwrite": True})
        assert res.ok is False, f"se aceptó {origen!r} -> {destino!r}"
        assert explicacion in (res.error or ""), f"{origen!r} -> {destino!r}: {res.error}"


def test_moving_a_tree_onto_its_own_parent_destroys_nothing(tmp_path: Path) -> None:
    """La primera medición del verificador, tal cual.

    `ci4tmp/app` dentro de `ci4tmp`, y `overwrite` para pisar el padre: el
    `rmtree(destino)` se llevaba el origen y la tool decía `ok=False`.
    """
    _ci4_temporal(tmp_path)

    res = _files(tmp_path).file_move(
        {"source": "ci4tmp/app", "destination": "ci4tmp", "overwrite": True}
    )

    assert res.ok is False
    assert (tmp_path / "ci4tmp" / "spark").is_file(), "el destino se evaporó"
    assert (tmp_path / "ci4tmp" / "app" / "Controllers" / "Home.php").is_file(), (
        "el origen se fue con el destino y la tool dijo que no pasó nada"
    )


def test_moving_onto_an_ancestor_inside_a_tracked_tree_destroys_nothing(tmp_path: Path) -> None:
    """La segunda medición: 41 ficheros commiteados, y la guarda versionada NO aplica.

    `app/Config` es profundidad 2, y la guarda de `AGENT_TRACKED_PATHS` mira
    sólo el PRIMER NIVEL a propósito (reorganizar `app/Modules/Foo` es trabajo
    normal). Así que aquí no hay red de seguridad debajo: o lo para la guarda
    topológica, o `app/` se queda vacía.
    """
    (tmp_path / "app" / "Config" / "Boot").mkdir(parents=True)
    (tmp_path / "app" / "Config" / "Boot" / "development.php").write_text("<?php", "utf-8")
    (tmp_path / "app" / "Config" / "App.php").write_text("<?php // config", encoding="utf-8")
    (tmp_path / "app" / "Controllers").mkdir()
    (tmp_path / "app" / "Controllers" / "Home.php").write_text("<?php // deliverable", "utf-8")

    res = _files_rastreadas(tmp_path, "app", "composer.json").file_move(
        {"source": "app/Config/Boot", "destination": "app/Config", "overwrite": True}
    )

    assert res.ok is False
    assert (tmp_path / "app" / "Config" / "App.php").is_file(), "se llevó el hermano del origen"
    assert (tmp_path / "app" / "Config" / "Boot" / "development.php").is_file()
    assert (tmp_path / "app" / "Controllers" / "Home.php").read_text(
        encoding="utf-8"
    ) == "<?php // deliverable"


def test_source_equal_to_destination_is_refused_even_with_overwrite(tmp_path: Path) -> None:
    """Mover algo sobre sí mismo con `overwrite` sería un `rmtree` del origen.

    No hay ningún movimiento que pedir aquí, así que la única lectura posible de
    la llamada es un error del modelo — y la respuesta correcta es decírselo,
    no ejecutar la mitad destructiva de una operación sin la otra mitad.
    """
    _ci4_temporal(tmp_path)

    for grafia in ("ci4tmp", "./ci4tmp/", "ci4tmp/."):
        res = _files(tmp_path).file_move(
            {"source": grafia, "destination": "ci4tmp", "overwrite": True}
        )
        assert res.ok is False, f"se aceptó por la grafía {grafia!r}"
        assert (tmp_path / "ci4tmp" / "spark").is_file(), f"borró el origen con {grafia!r}"
        assert (tmp_path / "ci4tmp" / "app" / "Controllers" / "Home.php").is_file()


def test_a_failed_move_does_not_leave_the_destination_destroyed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Destruir y luego devolver ok=False» es un defecto de FORMA, no un caso suelto.

    Aunque la guarda topológica cubra ahora los tres solapamientos, el ORDEN
    seguía permitiendo el mismo desenlace por cualquier otro motivo de fallo del
    `shutil.move` (ENOSPC, EACCES, EXDEV, un fichero bloqueado en Windows): el
    destino ya estaba borrado. Se comprueba con un fallo inyectado porque es la
    única forma de provocar ese camino sin depender del sistema de ficheros.
    """
    _ci4_temporal(tmp_path)
    (tmp_path / "vendor" / "pkg").mkdir(parents=True)
    (tmp_path / "vendor" / "pkg" / "a.php").write_text("<?php // instalado", encoding="utf-8")

    def _revienta(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(file_tools.shutil, "move", _revienta)

    res = _files(tmp_path).file_move(
        {"source": "ci4tmp", "destination": "vendor", "overwrite": True}
    )

    assert res.ok is False
    assert (tmp_path / "vendor" / "pkg" / "a.php").read_text(
        encoding="utf-8"
    ) == "<?php // instalado", "destruyó el destino en un movimiento que dijo no haber hecho nada"
    assert (tmp_path / "ci4tmp" / "spark").is_file(), "y el origen tampoco puede quedarse a medias"
    # El prefijo es el de `_PREFIJO_TRANSITORIO`, no `.vendor`: desde que las tres
    # tools que mutan el workspace comparten un solo patrón de residuo, filtrar
    # por el nombre del destino dejaría esta comprobación PASANDO EN VACÍO —
    # ningún hermano empieza ya por `.vendor`, así que no vería el rescate fallido
    # aunque dejara basura.
    sobras = [hijo.name for hijo in tmp_path.iterdir() if hijo.name.startswith(_PREFIJO_RESIDUO)]
    assert not sobras, f"quedó basura del rescate en el workspace: {sobras}"


# --- El camino legítimo completo, extremo a extremo -------------------------


def test_the_measured_dead_end_now_has_a_way_through(tmp_path: Path) -> None:
    """El run del 2026-08-31, rehecho con las herramientas que ahora existen.

    Es la prueba de que este encargo abre camino y no sólo cierra una puerta: sin
    ella, todo lo de arriba puede estar verde y el agente seguir atascado — que
    es exactamente el estado en el que lo dejó la guarda de `delete_file` sola.
    """
    _worktree_git_file(tmp_path)
    (tmp_path / "composer.json").write_text("{}", encoding="utf-8")
    _ci4_temporal(tmp_path)
    files = _files_rastreadas(tmp_path, "composer.json")

    # El agente lista el temporal y mueve entrada por entrada a la raíz.
    listado = files.file_list({"path": "ci4tmp"})
    assert listado.ok is True and listado.output is not None
    for entrada in listado.output["entries"]:
        nombre = entrada["name"]
        res = files.file_move({"source": f"ci4tmp/{nombre}", "destination": nombre})
        assert res.ok is True, f"no se pudo mover {nombre}: {res.error}"

    assert (tmp_path / "app" / "Controllers" / "Home.php").is_file()
    assert (tmp_path / "spark").is_file()
    # Y el worktree sigue entero: sin `.git` el trabajo no sería entregable.
    assert (tmp_path / ".git").is_file()

    # El temporal ya vacío se retira con la tool que ya existía.
    assert files.file_delete({"path": "ci4tmp", "recursive": True}).ok is True


# --- El cableado: env -> constructor -> guarda, por el nombre canónico ------


def test_move_file_is_wired_under_its_canonical_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin esto, la tool puede estar perfecta y no existir para el agente."""
    _ci4_temporal(tmp_path)
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("AGENT_TRACKED_PATHS", raising=False)

    registry = ToolRegistry()
    registrados = register_builtin_families(
        registry,
        api=None,
        sink=OrchestrationSink(),
        flags={f: f == FAMILY_FILE for f in ALL_FAMILIES},
    )

    assert "move_file" in registrados
    res = registry.call("move_file", {"source": "ci4tmp", "destination": "framework"})
    assert res.ok is True, res.error
    assert (tmp_path / "framework" / "spark").is_file()


def test_the_tracked_contract_reaches_move_through_the_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El modo de fallo caro: la guarda montada EN VACÍO no da error, da un destrozo."""
    _siembra_deliverable(tmp_path)
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_TRACKED_PATHS", "app\nsystem\npublic\ncomposer.json")

    registry = ToolRegistry()
    register_builtin_families(
        registry,
        api=None,
        sink=OrchestrationSink(),
        flags={f: f == FAMILY_FILE for f in ALL_FAMILIES},
    )

    # Antes del rechazo hay que probar que la tool EXISTE: si no estuviera
    # cableada, `registry.call` devolvería «unknown tool» —también `ok=False`— y
    # este test daría verde con la guarda muerta. Es el mismo modo de fallo que
    # obligó a escribir el test del contrato de `delete_file`.
    (tmp_path / "basura.txt").write_text("x", encoding="utf-8")
    legitimo = registry.call("move_file", {"source": "basura.txt", "destination": "tmp/basura.txt"})
    assert legitimo.ok is True, f"`move_file` no está cableada: {legitimo.error}"

    res = registry.call("move_file", {"source": "app", "destination": "app_viejo"})

    assert res.ok is False, "la familia `file` montó `move_file` sin las rutas versionadas"
    assert "tracked" in (res.error or ""), f"rechazó por otro motivo: {res.error}"
    assert (tmp_path / "app" / "Controllers" / "Home.php").exists()


def test_move_file_is_gated_as_code_changes() -> None:
    """La mitad del argumento «por aquí y no por `mv` en el allowlist de shell_exec».

    Un `mv` abierto en el allowlist no pasa por el gate de acciones sensibles: la
    política del proyecto («Cliente Externo» pide humano para `code_changes`) no
    vería nunca ese movimiento. Si `move_file` aterrizara wired y SIN categoría
    tendríamos el mismo agujero con mejor nombre — que es literalmente el
    fail-open que cerró prod-03 A8. Se comprueba aquí, junto a la tool, porque es
    parte de lo que su docstring promete.
    """
    from agent_runtime.approval import ApprovalGate
    from shared_domain.approval_categories import APPROVAL_CATEGORIES

    estricto = ApprovalGate({"categories": dict.fromkeys(APPROVAL_CATEGORIES, "human_required")})
    assert estricto.review("move_file") == "code_changes"

    permisivo = ApprovalGate({"categories": dict.fromkeys(APPROVAL_CATEGORIES, "auto")})
    assert permisivo.review("move_file") is None


# ---------------------------------------------------------------------------
# Las banderas destructivas tienen que ser booleanos DE VERDAD
# ---------------------------------------------------------------------------
# `bool(args.get("overwrite", False))` da True para la CADENA "false", y también
# para "no". Verificado el 2026-08-31 sobre esta misma tool: el destino se
# reemplazaba igual. El modelo del incidente es `gpt-oss:120b` vía ollama, que
# emite los booleanos como cadena a menudo.
#
# Y no es un detalle de forma: TODO el argumento de diseño de estas dos tools es
# que «la variante destructiva se pide A PROPÓSITO» —los docstrings de
# `file_move` y `file_delete` lo dicen con esas palabras—. Con la coerción de
# Python, el que dice "no" obtiene "sí". Es además lo que hace alcanzable SIN
# INTENCIÓN el destrozo de la sección anterior: `overwrite` era la puerta.
#
# El mismo defecto estaba en `delete_file.recursive`, así que la coerción es UNA
# y compartida: dos copias de la misma regla envejecen a media velocidad.


def _dos_ficheros(tmp_path: Path) -> None:
    (tmp_path / "nuevo.php").write_text("<?php // nuevo", encoding="utf-8")
    (tmp_path / "viejo.php").write_text("<?php // viejo", encoding="utf-8")


def _un_arbol(tmp_path: Path) -> Path:
    hoja = tmp_path / "vendor" / "pkg" / "a.php"
    hoja.parent.mkdir(parents=True)
    hoja.write_text("<?php", encoding="utf-8")
    return hoja


@pytest.mark.parametrize("valor", [True, "true", "True", "  TRUE  ", "1", 1])
def test_the_unambiguous_yes_spellings_all_overwrite(tmp_path: Path, valor: object) -> None:
    """El booleano de verdad y las grafías inequívocas de JSON, nada más."""
    _dos_ficheros(tmp_path)

    res = _files(tmp_path).file_move(
        {"source": "nuevo.php", "destination": "viejo.php", "overwrite": valor}
    )

    assert res.ok is True, f"{valor!r} no activó la sobrescritura: {res.error}"
    assert (tmp_path / "viejo.php").read_text(encoding="utf-8") == "<?php // nuevo"


@pytest.mark.parametrize("valor", [False, "false", "False", " FALSE ", "0", 0, None])
def test_the_unambiguous_no_spellings_never_overwrite(tmp_path: Path, valor: object) -> None:
    """El caso medido: la CADENA "false" reemplazaba el destino.

    `None` cuenta como ausencia —un `null` de JSON es «no lo he puesto»— y la
    ausencia sigue significando False, que es el default no destructivo.
    """
    _dos_ficheros(tmp_path)

    res = _files(tmp_path).file_move(
        {"source": "nuevo.php", "destination": "viejo.php", "overwrite": valor}
    )

    assert res.ok is False, f"{valor!r} sobrescribió el destino"
    assert (tmp_path / "viejo.php").read_text(encoding="utf-8") == "<?php // viejo"
    assert (tmp_path / "nuevo.php").exists(), "y el origen tampoco se toca"
    assert "overwrite=true" in (res.error or ""), "no dice cómo pedirlo a propósito"


@pytest.mark.parametrize("valor", [True, "true", "True", "1", 1])
def test_the_unambiguous_yes_spellings_all_delete_recursively(
    tmp_path: Path, valor: object
) -> None:
    hoja = _un_arbol(tmp_path)

    res = _files(tmp_path).file_delete({"path": "vendor", "recursive": valor})

    assert res.ok is True, f"{valor!r} no activó el borrado recursivo: {res.error}"
    assert not hoja.parent.parent.exists()


@pytest.mark.parametrize("valor", [False, "false", "False", "0", 0, None])
def test_the_unambiguous_no_spellings_never_delete_a_tree(tmp_path: Path, valor: object) -> None:
    hoja = _un_arbol(tmp_path)

    res = _files(tmp_path).file_delete({"path": "vendor", "recursive": valor})

    assert res.ok is False, f"{valor!r} borró el árbol"
    assert hoja.is_file()
    assert "recursive=true" in (res.error or ""), "no dice cómo pedirlo a propósito"


#: Valores que NO son una grafía inequívoca de un booleano de JSON.
_AMBIGUOS: tuple[object, ...] = ("no", "yes", "sí", "y", "on", "off", "maybe", "", "2", 2, -1, [])


@pytest.mark.parametrize("valor", _AMBIGUOS)
def test_an_ambiguous_overwrite_is_an_error_never_a_silent_yes(
    tmp_path: Path, valor: object
) -> None:
    """Un valor raro NO puede degradar a «sí» — pero tampoco a «no» en silencio.

    Degradar a «sí» es el defecto medido y no se discute. Degradar a «no»
    tampoco es gratis: el agente creería haber pedido la variante destructiva y
    recibiría un «pasa overwrite=true» que, desde su punto de vista, ya había
    pasado — el bucle de reintentos que en la ejecución
    `01a05881-89d7-79fa-be72-bd0e7c1a9fbb` se comió CATORCE llamadas y el 60%
    del presupuesto contra un requisito de forma. El error explícito, diciendo
    qué valor se esperaba, convierte ese bucle en una llamada corregida.

    Y no se aceptan sinónimos ("yes"/"no"/"on"/"off") por la misma razón por la
    que `test_overwrite_is_an_explicit_opt_in` no acepta `force` ni `replace`
    como sinónimos de la CLAVE: un contrato estricto con un vocabulario que
    crece deja de ser un contrato.
    """
    _dos_ficheros(tmp_path)

    res = _files(tmp_path).file_move(
        {"source": "nuevo.php", "destination": "viejo.php", "overwrite": valor}
    )

    assert res.ok is False, f"{valor!r} activó la sobrescritura"
    assert (tmp_path / "viejo.php").read_text(encoding="utf-8") == "<?php // viejo"
    error = res.error or ""
    assert "overwrite" in error, f"no dice qué argumento estaba mal: {error}"
    assert "true" in error and "false" in error, f"no dice qué valor se esperaba: {error}"
    assert repr(valor) in error, f"no dice qué llegó, así que no se puede corregir: {error}"


@pytest.mark.parametrize("valor", _AMBIGUOS)
def test_an_ambiguous_recursive_is_an_error_never_a_silent_yes(
    tmp_path: Path, valor: object
) -> None:
    hoja = _un_arbol(tmp_path)

    res = _files(tmp_path).file_delete({"path": "vendor", "recursive": valor})

    assert res.ok is False, f"{valor!r} borró el árbol"
    assert hoja.is_file()
    error = res.error or ""
    assert "recursive" in error, f"no dice qué argumento estaba mal: {error}"
    assert "true" in error and "false" in error, f"no dice qué valor se esperaba: {error}"


def test_a_malformed_flag_is_reported_even_when_it_would_not_have_mattered(
    tmp_path: Path,
) -> None:
    """La misma llamada no puede funcionar o fallar según el estado del disco.

    Si `overwrite="yes"` sólo diera error cuando el destino existe, la tool
    tendría el defecto que su propio docstring le reprocha a `mv`: significar
    dos cosas distintas según lo que haya en el destino. El argumento está mal
    escrito, y eso es cierto antes de mirar el sistema de ficheros.
    """
    (tmp_path / "nuevo.php").write_text("<?php // nuevo", encoding="utf-8")

    res = _files(tmp_path).file_move(
        {"source": "nuevo.php", "destination": "otro.php", "overwrite": "yes"}
    )

    assert res.ok is False
    assert not (tmp_path / "otro.php").exists()
    assert "overwrite" in (res.error or "")


# ---------------------------------------------------------------------------
# Un fallo del sistema se cuenta en el idioma del módulo, no en el del host
# ---------------------------------------------------------------------------
# El `except OSError` devolvía tal cual «FileExistsError: [WinError 183] No se
# puede crear un archivo que ya existe: 'C:\...\worktrees\...\framework'». Dos
# cosas mal, y las dos importan porque el destinatario es un modelo:
#
#   * la ruta ABSOLUTA del host, cuando el agente sólo ve /workspace y todo el
#     resto del módulo le habla en rutas relativas — no puede accionar una ruta
#     que no existe en su mundo, ni tiene por qué aprenderse la del host;
#   * el mensaje del SO traducido al idioma de la máquina. Un error que cambia
#     de idioma según dónde corra el worker no lo reconoce ni el modelo ni un
#     test. El `errno` sí es estable y está en inglés.


def test_a_failed_move_reports_in_relative_paths_and_a_stable_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci4_temporal(tmp_path)

    def _revienta(*_args: object, **_kwargs: object) -> None:
        raise FileExistsError(
            errno.EEXIST,
            "No se puede crear un archivo que ya existe",
            str(tmp_path / "framework"),
        )

    monkeypatch.setattr(file_tools.shutil, "move", _revienta)

    res = _files(tmp_path).file_move({"source": "ci4tmp", "destination": "framework"})

    assert res.ok is False
    error = res.error or ""
    assert str(tmp_path) not in error and tmp_path.as_posix() not in error, (
        f"filtra la ruta absoluta del host: {error}"
    )
    assert "No se puede crear" not in error, f"filtra el idioma del host: {error}"
    assert "ci4tmp" in error and "framework" in error, f"no dice qué falló: {error}"
    assert "EEXIST" in error, f"no deja un código estable para diagnosticar: {error}"


def test_a_failed_recursive_delete_reports_the_same_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El fallo se inyecta donde el borrado recursivo puede fallar HOY.

    Antes se inyectaba sobre `shutil.rmtree`, y era el sitio correcto mientras el
    árbol se destruía en su sitio. Desde que se aparta primero, un `rmtree` roto
    ya no produce error —el borrado lógico ya ocurrió, ver
    `_delete_tree`— y el único fallo que la tool puede reportar es no haber
    podido apartar. Seguir inyectando en `rmtree` dejaría este test comprobando
    el formato de un error que no se emite nunca: verde y en vacío.
    """
    _un_arbol(tmp_path)

    def _revienta(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EACCES, "Acceso denegado", str(tmp_path / "vendor"))

    monkeypatch.setattr(Path, "rename", _revienta)

    res = _files(tmp_path).file_delete({"path": "vendor", "recursive": True})

    assert res.ok is False
    error = res.error or ""
    assert str(tmp_path) not in error and tmp_path.as_posix() not in error, error
    assert "Acceso denegado" not in error, f"filtra el idioma del host: {error}"
    assert "vendor" in error and "EACCES" in error, error


def test_a_write_under_a_file_reports_the_same_way(tmp_path: Path) -> None:
    """Sin inyectar nada: escribir bajo un fichero es un error real del SO."""
    (tmp_path / "a.php").write_text("<?php", encoding="utf-8")

    res = _files(tmp_path).file_write({"path": "a.php/b.php", "content": "x"})

    assert res.ok is False
    error = res.error or ""
    assert str(tmp_path) not in error and tmp_path.as_posix() not in error, error
    assert "a.php/b.php" in error, f"no dice qué ruta falló, en el mundo del agente: {error}"
    assert "EEXIST" in error or "ENOTDIR" in error, error


def test_a_move_that_worked_is_not_reported_as_failed_if_the_leftover_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El defecto inverso del que arregla el nuevo orden, y por eso se fija aquí.

    Apartar el destino y descartarlo al final abre una posibilidad que antes no
    existía: que el descarte falle (en Windows, un fichero de sólo lectura o
    abierto por otro proceso) DESPUÉS de que el movimiento haya ocurrido. Decir
    entonces `ok=False` sería el mismo defecto con el signo cambiado — el agente
    reharía un trabajo que ya está hecho.

    Y la salida no puede crecer para contarlo: el catálogo declara la de
    `move_file` con `additionalProperties: false`, y
    `tests/unit/test_move_file_catalogo_y_reparto.py` comprueba que el ejecutor
    no devuelve ninguna clave que el catálogo no declare.
    """
    _ci4_temporal(tmp_path)
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "viejo.php").write_text("<?php", encoding="utf-8")

    def _no_se_puede(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EACCES, "Acceso denegado")

    monkeypatch.setattr(file_tools.shutil, "rmtree", _no_se_puede)

    res = _files(tmp_path).file_move(
        {"source": "ci4tmp", "destination": "vendor", "overwrite": True}
    )

    assert res.ok is True, res.error
    assert (tmp_path / "vendor" / "spark").is_file(), "el movimiento no llegó a ocurrir"
    assert res.output is not None
    declaradas = {"source", "destination", "moved", "entries", "replaced", "replaced_entries"}
    assert set(res.output) <= declaradas, f"clave que el catálogo no declara: {set(res.output)}"


# ---------------------------------------------------------------------------
# «Destruir y luego fallar» en las DOS tools hermanas de `move_file`
# ---------------------------------------------------------------------------
# El patrón tiene nombre y acaba de cerrarse en `move_file`: la tool destruye
# algo, la operación falla DESPUÉS, y devuelve `ok=False` — así que el agente lee
# «no ha pasado nada» sobre un workspace que quedó PEOR que antes de la llamada.
# Una verificación adversarial del 2026-08-31 confirmó que las otras dos tools
# que mutan el workspace lo tienen igual, y no como regresión reciente: de
# origen.
#
# `delete_file --recursive` es la grave, y es además la tool que se estrenó el
# día del incidente que motivó todo esto. `shutil.rmtree` va DESENLAZANDO
# entradas y aborta en la primera que no puede; lo ya desenlazado no vuelve.
# Medido en LINUX con la imagen real del worker y uid no root
# (`docker run --rm --user 1000:1000 agentic-platform/workers:ci`): un `vendor/`
# con un subdirectorio sin permiso de escritura perdió 6 DE 8 ENTRADAS y después
# lanzó PermissionError, respondiendo `could not delete "vendor" [EACCES]`.
#
# Y el camino es esperable en producción, no teórico: `stack_exec` (ADR 0093)
# corre el toolchain en OTRO contenedor, que puede dejar el árbol con dueño o
# permisos que el agent-runtime no puede desenlazar. Un `composer install`
# seguido de un `delete_file vendor --recursive` es exactamente esa secuencia.
#
# `write_file` es el mismo patrón más barato: `Path.write_text` abre en modo "w",
# que TRUNCA al abrir, así que un fallo a media escritura deja el fichero sin el
# contenido viejo y sin el nuevo. Medido:
#
#     "CONTENIDO ORIGINAL QUE IMPORTA"  ->  "NUEV"   con ok=False


#: El prefijo literal que un `.gitignore` del worktree tiene que excluir.
#:
#: Se escribe A MANO y no se importa de `file_tools` a propósito: si alguien
#: cambia el patrón en el módulo, la línea del `.gitignore` deja de cubrirlo y
#: este fichero tiene que ser lo que se entere. Importar la constante haría al
#: test cómplice del cambio en vez de testigo.
_PREFIJO_RESIDUO = ".agent-runtime-tmp."


def _arbol_de_cuatro(tmp_path: Path) -> None:
    """Un `vendor/` con cuatro ficheros: suficiente para quedarse a medias."""
    (tmp_path / "vendor" / "pkg").mkdir(parents=True)
    for nombre in ("autoload.php", "a.php", "b.php"):
        (tmp_path / "vendor" / nombre).write_text(f"<?php // {nombre}", encoding="utf-8")
    (tmp_path / "vendor" / "pkg" / "c.php").write_text("<?php // c", encoding="utf-8")


def _rmtree_que_desenlaza_y_aborta(ruta: object, *_args: object, **_kwargs: object) -> None:
    """El `shutil.rmtree` REAL del fallo medido: desenlaza un par y aborta.

    Un doble que se limitara a lanzar SIN borrar nada dejaría este test verde
    también con el defecto puesto —el árbol seguiría entero por accidente—, que
    es justo la trampa que documenta
    `docs/03-guides/verificar-antes-de-implementar.md`: un test que fija el
    defecto en lugar de exponerlo.
    """
    raiz = Path(str(ruta))
    borrados = 0
    for hijo in sorted(raiz.rglob("*")):
        if hijo.is_file():
            hijo.unlink()
            borrados += 1
        if borrados == 2:
            raise PermissionError(errno.EACCES, "Permission denied", str(hijo))
    raise PermissionError(errno.EACCES, "Permission denied", str(raiz))


def test_a_recursive_delete_that_aborts_midway_leaves_no_half_deleted_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lo medido en Linux: 6 de 8 entradas perdidas y la tool diciendo `ok=False`.

    Las dos afirmaciones van juntas y en este orden. La primera es el daño: parte
    de la carpeta desaparecida y el resto todavía ahí, con el agente creyendo que
    no se tocó nada. La segunda es la consecuencia del arreglo: una vez el árbol
    ha salido de su ruta, el borrado que el agente pidió YA ocurrió, y decir que
    falló le haría rehacer algo hecho.
    """
    _arbol_de_cuatro(tmp_path)
    monkeypatch.setattr(file_tools.shutil, "rmtree", _rmtree_que_desenlaza_y_aborta)

    res = _files(tmp_path).file_delete({"path": "vendor", "recursive": True})

    assert not (tmp_path / "vendor").exists(), (
        "el árbol se quedó a medias EN SU SITIO: parte perdida, parte ahí, y la "
        "tool a punto de decir que no había hecho nada"
    )
    assert res.ok is True, (
        f"el borrado lógico ocurrió —`vendor` ya no está— así que `ok=False` es el "
        f"mismo defecto con el signo cambiado: {res.error}"
    )
    sobras = sorted(hijo.name for hijo in tmp_path.iterdir())
    assert sobras == [f"{_PREFIJO_RESIDUO}vendor.0"], (
        "el residuo del descarte fallido no lleva el prefijo que UNA línea de "
        f".gitignore puede excluir: {sobras}"
    )


def test_a_recursive_delete_that_cannot_even_start_destroys_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La otra mitad: si no se puede apartar, `ok=False` dice la verdad.

    Apartar es un renombrado, que pide permiso sobre el directorio PADRE y no
    sobre el contenido — por eso puede con árboles que `rmtree` no consigue
    desmontar. Cuando ni eso se puede, no se ha destruido nada y el agente puede
    fiarse del «no».
    """
    _arbol_de_cuatro(tmp_path)

    def _no_se_puede_renombrar(self: Path, *_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EACCES, "Permission denied", str(self))

    monkeypatch.setattr(Path, "rename", _no_se_puede_renombrar)

    res = _files(tmp_path).file_delete({"path": "vendor", "recursive": True})

    assert res.ok is False
    assert sorted(hijo.name for hijo in (tmp_path / "vendor").iterdir()) == [
        "a.php",
        "autoload.php",
        "b.php",
        "pkg",
    ], "destruyó el árbol pese a responder que no había podido"
    assert (tmp_path / "vendor" / "pkg" / "c.php").is_file()
    error = res.error or ""
    assert "vendor" in error and "EACCES" in error, error


def test_the_tree_leaves_its_place_before_anything_gets_destroyed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La invariante que ninguna guarda puede dar: el ORDEN.

    Enumerar motivos de fallo del `rmtree` no sirve —siempre queda uno sin
    enumerar, que es la lección de `_ejecutar_movimiento`—, así que lo que se
    fija aquí es que cuando la destrucción EMPIEZA el árbol ya no está en la
    ruta que el agente conoce. Con eso, cualquier fallo posterior significa «ya
    no está», nunca «está a medias».
    """
    _arbol_de_cuatro(tmp_path)
    en_su_sitio: list[bool] = []
    rmtree_real = file_tools.shutil.rmtree

    def _espia(ruta: object, *args: object, **kwargs: object) -> None:
        en_su_sitio.append((tmp_path / "vendor").exists())
        rmtree_real(ruta, *args, **kwargs)

    monkeypatch.setattr(file_tools.shutil, "rmtree", _espia)

    assert _files(tmp_path).file_delete({"path": "vendor", "recursive": True}).ok is True
    assert en_su_sitio == [False], (
        "la destrucción empezó con el árbol todavía en su ruta: cualquier fallo a "
        "mitad lo deja roto donde el agente cree que sigue entero"
    )


def test_a_failed_write_does_not_truncate_the_previous_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lo medido: `"CONTENIDO ORIGINAL QUE IMPORTA"` -> `"NUEV"` con `ok=False`.

    Más barato que su hermana `delete_file` y el mismo patrón — con la agravante
    de que aquí lo destruido es código fuente, que nadie echa en falta hasta que
    no compila.
    """
    objetivo = tmp_path / "app" / "Config" / "App.php"
    objetivo.parent.mkdir(parents=True)
    objetivo.write_text("<?php // CONTENIDO ORIGINAL QUE IMPORTA", encoding="utf-8")

    def _se_queda_sin_disco(self: Path, data: str, *_args: object, **_kwargs: object) -> None:
        # Fiel al fallo real: el modo "w" TRUNCA al abrir y la escritura se rompe
        # DESPUÉS. Un doble que lanzara sin escribir dejaría el test verde con el
        # defecto puesto, porque el fichero seguiría intacto por accidente.
        with open(self, "w", encoding="utf-8") as fichero:
            fichero.write(data[:4])
        raise OSError(errno.ENOSPC, "No space left on device", str(self))

    monkeypatch.setattr(Path, "write_text", _se_queda_sin_disco)

    res = _files(tmp_path).file_write({"path": "app/Config/App.php", "content": "<?php // NUEVO"})

    assert res.ok is False
    assert objetivo.read_text(encoding="utf-8") == "<?php // CONTENIDO ORIGINAL QUE IMPORTA", (
        "escribió a medias sobre el fichero real y luego dijo que no había hecho nada"
    )
    assert sorted(hijo.name for hijo in objetivo.parent.iterdir()) == ["App.php"], (
        "el transitorio se quedó huérfano al lado del fichero"
    )
    error = res.error or ""
    assert "app/Config/App.php" in error and "ENOSPC" in error, error


def test_a_write_that_cannot_be_swapped_in_leaves_neither_damage_nor_leftovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El segundo punto de fallo del arreglo: el reemplazo final.

    Se inyecta sobre `os.replace` porque es el paso que el arreglo introduce.
    Con la versión que truncaba, `os.replace` no se llama nunca: la escritura
    ocurre igual y este test lo dice.
    """
    objetivo = tmp_path / "spark"
    objetivo.write_text("#!/usr/bin/env php\n// v1\n", encoding="utf-8")

    def _revienta(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EACCES, "Acceso denegado")

    monkeypatch.setattr(os, "replace", _revienta)

    res = _files(tmp_path).file_write({"path": "spark", "content": "// v2\n"})

    assert res.ok is False
    assert objetivo.read_text(encoding="utf-8") == "#!/usr/bin/env php\n// v1\n"
    assert sorted(hijo.name for hijo in tmp_path.iterdir()) == ["spark"], (
        "un transitorio huérfano lo vería el agente en `list_files` y acabaría en "
        "el `git add -A` del cierre de tarea"
    )
    error = res.error or ""
    assert "spark" in error and "EACCES" in error, error


def test_writing_a_new_file_still_creates_its_missing_directories(tmp_path: Path) -> None:
    """El arreglo no puede romper el caso normal, que es el 99 % de las llamadas.

    `mkdir` está BLOQUEADO por el allowlist de comandos (paso 39 del run medido),
    así que si `write_file` dejara de crear los intermedios el agente se quedaría
    sin NINGUNA forma de crear un fichero en un directorio nuevo.
    """
    contenido = "<?php // nuevo"

    res = _files(tmp_path).file_write({"path": "app/Controllers/Home.php", "content": contenido})

    assert res.ok is True, res.error
    destino = tmp_path / "app" / "Controllers" / "Home.php"
    assert destino.read_text(encoding="utf-8") == contenido
    assert res.output == {"path": "app/Controllers/Home.php", "bytes_written": len(contenido)}
    assert sorted(hijo.name for hijo in destino.parent.iterdir()) == ["Home.php"], (
        "quedó el transitorio al lado del fichero recién creado"
    )


def test_rewriting_a_file_leaves_no_tail_of_the_previous_content(tmp_path: Path) -> None:
    """Reemplazar es reemplazar: ni resto del contenido viejo, ni hermanos."""
    objetivo = tmp_path / "app" / "Config" / "App.php"
    objetivo.parent.mkdir(parents=True)
    objetivo.write_text("<?php // una versión anterior bastante más larga\n", encoding="utf-8")

    res = _files(tmp_path).file_write({"path": "app/Config/App.php", "content": "<?php\n"})

    assert res.ok is True, res.error
    assert objetivo.read_text(encoding="utf-8") == "<?php\n"
    assert sorted(hijo.name for hijo in objetivo.parent.iterdir()) == ["App.php"]


def test_rewriting_a_file_keeps_the_permissions_it_already_had(tmp_path: Path) -> None:
    """El defecto que el ARREGLO podría introducir, que es la peor clase.

    `os.replace` estrena inodo, así que el fichero resultante no hereda los
    permisos del anterior como sí hacía `write_text`: reescribir el `spark` de
    CodeIgniter o un `.sh` de despliegue lo dejaría sin el bit de ejecución, y
    eso no se ve hasta que algo no arranca.

    En Windows la afirmación es trivial —`os.chmod` sólo mueve el bit de sólo
    lectura y `st_mode` no distingue más—, así que quien la hace valer de verdad
    es Linux, que es donde corre el runtime real y donde corre el CI.
    """
    objetivo = tmp_path / "spark"
    objetivo.write_text("#!/usr/bin/env php\n", encoding="utf-8")
    os.chmod(objetivo, 0o750)
    modo_previo = stat.S_IMODE(objetivo.stat().st_mode)

    res = _files(tmp_path).file_write({"path": "spark", "content": "#!/usr/bin/env php\n// v2\n"})

    assert res.ok is True, res.error
    assert stat.S_IMODE(objetivo.stat().st_mode) == modo_previo, (
        "la reescritura se llevó por delante los permisos del fichero"
    )


def test_one_ignore_line_covers_what_both_tools_can_leave_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Los dos residuos comparten patrón, porque el `.gitignore` es UNO.

    Cuando el descarte final falla —en Windows, ficheros de sólo lectura o
    abiertos por otro proceso—, `move_file --overwrite` y
    `delete_file --recursive` dejan cada una un hermano oculto. El cierre de
    tarea hace `git add -A` (`workers.plan_git.commit_task`) y ese
    nombre no está en ningún `.gitignore`, así que se commitearía. Un patrón por
    tool obligaría a dos líneas y a acordarse de las dos: esto fija que basta
    con `.agent-runtime-tmp.*`.
    """
    _ci4_temporal(tmp_path)
    (tmp_path / "vendor" / "pkg").mkdir(parents=True)
    (tmp_path / "vendor" / "pkg" / "a.php").write_text("<?php", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.js").write_text("//", encoding="utf-8")

    def _no_se_puede(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EACCES, "Acceso denegado")

    monkeypatch.setattr(file_tools.shutil, "rmtree", _no_se_puede)
    files = _files(tmp_path)

    movido = files.file_move({"source": "ci4tmp", "destination": "vendor", "overwrite": True})
    borrado = files.file_delete({"path": "build", "recursive": True})

    assert movido.ok is True, movido.error
    assert borrado.ok is True, borrado.error
    residuos = sorted(hijo.name for hijo in tmp_path.iterdir() if hijo.name != "vendor")
    assert residuos == [
        f"{_PREFIJO_RESIDUO}build.0",
        f"{_PREFIJO_RESIDUO}vendor.0",
    ], f"los residuos de las dos tools no caben en un solo patrón: {residuos}"


# ===========================================================================
# `list_files` y su `pattern`: el contrato anunciado NO se cumplía
# ===========================================================================
# Medido en vivo el 2026-09-01, proyecto `Hello World CI4 v3` del tenant
# mediapro. La tool ANUNCIA en su esquema —que es lo único que el modelo ve—
#
#     "List files matching a glob pattern under a path."
#     {"path": {"default": "."}, "pattern": {"default": "**/*"}}
#
# y `file_list` NUNCA leía `pattern`: hacía `resolved.iterdir()`, un listado
# PLANO de un nivel, sin filtrar. El agente buscaba los tests por el árbol:
#
#     list_files {"path":".", "pattern":"tests/**/*.php"}     -> [{"name":"docs"}]
#     list_files {"path":".", "pattern":"*phpunit*"}          -> [{"name":"docs"}]
#     list_files {"path":".", "pattern":"vendor/bin/phpunit"} -> [{"name":"docs"}]
#
# Ocho patrones distintos, la misma respuesta, ninguna señal de que el filtro se
# estaba tirando a la basura. Por eso repetía: no podía concluir nada.
#
# Es la misma familia que el `path` vacío que se cerró el 2026-08-31 —el
# contrato de la tool no coincide con su comportamiento— pero PEOR, porque aquél
# devolvía un error y éste devolvía un resultado plausible.
#
# Lo que el modelo manda DE VERDAD (965 llamadas a `list_files` en el
# `steps_log`, consultadas el 2026-09-01):
#
#     **/*  316 | *  279 | **/*.php  72 | **/*.md  69 | *.php  32 | **  8
#     589 de 965 llevan '/'    -> el patrón casa contra la RUTA, no el nombre
#     578 de 965 llevan '**'   -> la recursividad se pide explícitamente
#      39 de 965 llevan llaves -> `**/composer.{json,lock}`, `{app,tests}/**/*.php`
#      33 de 965 no llevan comodín alguno («¿existe app/Config/Routes.php?»)
#
# De ahí sale la semántica que fijan los tests de abajo.


def _arbol_ci4(raiz: Path) -> None:
    """Un CI4 en miniatura con la forma que tienen los patrones reales."""
    ficheros = (
        "composer.json",
        "composer.lock",
        "spark",
        "phpunit.xml.dist",
        "app/Config/Routes.php",
        "app/Config/App.php",
        "app/Controllers/Home.php",
        "app/Views/welcome.php",
        "tests/HomeTest.php",
        "tests/unit/RouteTest.php",
        "tests/_support/bootstrap.php",
        "vendor/bin/phpunit",
        "vendor/codeigniter4/framework/system/CodeIgniter.php",
        "docs/README.md",
    )
    for relativa in ficheros:
        destino = raiz / relativa
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text("x", encoding="utf-8")


def _nombres(res: ToolResult) -> list[str]:
    assert res.ok is True, res.error
    assert res.output is not None
    return [e["name"] for e in res.output["entries"]]


# --- 1. el defecto medido --------------------------------------------------


def test_the_pattern_is_actually_applied(tmp_path: Path) -> None:
    """El defecto en una línea: el filtro se anunciaba y no se aplicaba."""
    _arbol_ci4(tmp_path)

    res = _files(tmp_path).file_list({"path": ".", "pattern": "tests/**/*.php"})

    assert _nombres(res) == [
        "tests/HomeTest.php",
        "tests/_support/bootstrap.php",
        "tests/unit/RouteTest.php",
    ], "`pattern` sigue sin leerse: esto es el listado plano de la raíz"


def test_the_three_patterns_from_the_incident_now_answer(tmp_path: Path) -> None:
    """Los tres patrones literales del run, que devolvían el mismo listado."""
    _arbol_ci4(tmp_path)
    files = _files(tmp_path)

    assert _nombres(files.file_list({"path": ".", "pattern": "tests/**/*.php"})) == [
        "tests/HomeTest.php",
        "tests/_support/bootstrap.php",
        "tests/unit/RouteTest.php",
    ]
    assert _nombres(files.file_list({"path": ".", "pattern": "**/*phpunit*"})) == [
        "phpunit.xml.dist",
        "vendor/bin/phpunit",
    ]
    assert _nombres(files.file_list({"path": ".", "pattern": "vendor/bin/phpunit"})) == [
        "vendor/bin/phpunit"
    ]


# --- 2. la semántica que se decide, cada pieza por separado ----------------


def test_a_single_star_does_not_cross_directories(tmp_path: Path) -> None:
    """`*` = este nivel. Es el 2.º patrón más usado (279 llamadas) y significa
    «enséñame este directorio»: hacerlo recursivo devolvería el árbol entero."""
    _arbol_ci4(tmp_path)

    assert _nombres(_files(tmp_path).file_list({"path": ".", "pattern": "*"})) == [
        "app",
        "composer.json",
        "composer.lock",
        "docs",
        "phpunit.xml.dist",
        "spark",
        "tests",
        "vendor",
    ]


def test_only_double_star_descends(tmp_path: Path) -> None:
    """`*.php` en la raíz de un CI4 no da nada; `**/*.php` da el árbol."""
    _arbol_ci4(tmp_path)
    files = _files(tmp_path)

    assert _nombres(files.file_list({"path": ".", "pattern": "*.php"})) == []
    assert _nombres(files.file_list({"path": ".", "pattern": "**/*.php"})) == [
        "app/Config/App.php",
        "app/Config/Routes.php",
        "app/Controllers/Home.php",
        "app/Views/welcome.php",
        "tests/HomeTest.php",
        "tests/_support/bootstrap.php",
        "tests/unit/RouteTest.php",
        "vendor/codeigniter4/framework/system/CodeIgniter.php",
    ]


def test_the_pattern_matches_the_relative_path_not_the_name(tmp_path: Path) -> None:
    """589 de 965 patrones reales llevan '/'. Casar contra el NOMBRE los
    dejaría a todos en vacío, que es la forma silenciosa del mismo defecto."""
    _arbol_ci4(tmp_path)
    files = _files(tmp_path)

    assert _nombres(files.file_list({"path": ".", "pattern": "app/Config/Routes.php"})) == [
        "app/Config/Routes.php"
    ]
    # Y la contraria: el nombre suelto NO casa una ruta profunda.
    assert _nombres(files.file_list({"path": ".", "pattern": "Routes.php"})) == []


def test_the_pattern_is_relative_to_the_path_given(tmp_path: Path) -> None:
    """El patrón se ancla en `path`, no en la raíz del workspace."""
    _arbol_ci4(tmp_path)

    res = _files(tmp_path).file_list({"path": "app", "pattern": "**/*.php"})

    assert _nombres(res) == [
        "Config/App.php",
        "Config/Routes.php",
        "Controllers/Home.php",
        "Views/welcome.php",
    ]
    assert res.output is not None
    assert res.output["path"] == "app"


def test_braces_are_expanded(tmp_path: Path) -> None:
    """39 de las 965 llamadas reales usan llaves. `pathlib` no las entiende: sin
    expandirlas, `**/composer.{json,lock}` devolvería vacío sobre un workspace
    que SÍ tiene los dos ficheros — el defecto con otra cara."""
    _arbol_ci4(tmp_path)
    files = _files(tmp_path)

    assert _nombres(files.file_list({"path": ".", "pattern": "**/composer.{json,lock}"})) == [
        "composer.json",
        "composer.lock",
    ]
    assert _nombres(files.file_list({"path": ".", "pattern": "{app,tests}/**/*Test.php"})) == [
        "tests/HomeTest.php",
        "tests/unit/RouteTest.php",
    ]


def test_matching_is_case_sensitive(tmp_path: Path) -> None:
    """El runtime corre sobre Linux y el repo es PSR-4: `Home.php` y `home.php`
    son ficheros distintos. Casar sin distinguir mayúsculas le daría al agente
    una ruta que después `read_file` no encuentra.

    Se afirma sobre el NOMBRE que devuelve el listado, no sobre el sistema de
    ficheros, para que valga igual en el Windows del desarrollador (que es
    case-insensitive) y en el Linux del contenedor."""
    _arbol_ci4(tmp_path)
    files = _files(tmp_path)

    assert _nombres(files.file_list({"path": ".", "pattern": "**/Home.php"})) == [
        "app/Controllers/Home.php"
    ]
    assert _nombres(files.file_list({"path": ".", "pattern": "**/home.php"})) == []


def test_a_star_does_not_cross_a_separator_even_while_descending(tmp_path: Path) -> None:
    """La pieza que hace predecible el resto: `**` desciende, `*` no.

    Con `**/a*`, el `**/` come segmentos enteros y el `a*` casa DENTRO de uno
    solo. Si `*` cruzara la barra, `x/a/b` casaría también y la diferencia entre
    los dos comodines dejaría de significar nada.
    """
    (tmp_path / "x" / "a").mkdir(parents=True)
    (tmp_path / "x" / "ab").write_text("x", encoding="utf-8")
    (tmp_path / "x" / "a" / "b").write_text("x", encoding="utf-8")

    assert _nombres(_files(tmp_path).file_list({"path": ".", "pattern": "**/a*"})) == [
        "x/a",
        "x/ab",
    ]


def test_question_mark_and_character_classes(tmp_path: Path) -> None:
    (tmp_path / "ab.php").write_text("x", encoding="utf-8")
    (tmp_path / "axb.php").write_text("x", encoding="utf-8")
    files = _files(tmp_path)

    assert _nombres(files.file_list({"path": ".", "pattern": "a?b.php"})) == ["axb.php"]
    assert _nombres(files.file_list({"path": ".", "pattern": "a[bx]*.php"})) == [
        "ab.php",
        "axb.php",
    ]
    assert _nombres(files.file_list({"path": ".", "pattern": "a[!x]*.php"})) == ["ab.php"]


# --- 3. el default efectivo -----------------------------------------------


def test_the_default_pattern_is_flat_not_the_whole_tree(tmp_path: Path) -> None:
    """El esquema anunciaba `"default": "**/*"`, y cumplirlo sería peor que el
    defecto: `list_files` sobre la raíz de un CI4 devolvería los ~5.000 ficheros
    de `vendor/` —en el incidente la rama llegó a 10.318— y reventaría el
    contexto del agente en una sola llamada. El default efectivo es `*`."""
    _arbol_ci4(tmp_path)
    files = _files(tmp_path)

    sin_patron = files.file_list({"path": "."})
    plano = files.file_list({"path": ".", "pattern": "*"})

    assert _nombres(sin_patron) == _nombres(plano)
    assert "vendor/bin/phpunit" not in _nombres(sin_patron)
    assert sin_patron.output is not None
    assert sin_patron.output["pattern"] == "*", "el resultado no dice qué filtro se aplicó"


def test_a_blank_pattern_is_the_same_as_omitting_it(tmp_path: Path) -> None:
    """Mismo argumento que con `path`: el modelo manda la clave VACÍA, no la
    omite (medido: `{"path": "", "pattern": "*"}` x12)."""
    _arbol_ci4(tmp_path)
    files = _files(tmp_path)
    esperado = _nombres(files.file_list({"path": "."}))

    for patron in ("", "   ", None):
        assert _nombres(files.file_list({"path": ".", "pattern": patron})) == esperado, (
            f"pattern={patron!r} no se trató como ausente"
        )


def test_the_catalog_default_is_the_one_the_tool_applies(tmp_path: Path) -> None:
    """El cruce que impide que esto vuelva a divergir.

    El esquema del catálogo es LO ÚNICO que el modelo ve. Este test toma el
    `default` que anuncia la fila `list-files` y comprueba, por comportamiento,
    que pasárselo explícitamente da el MISMO resultado que omitirlo. Con el
    `"**/*"` de antes fallaría por los dos lados a la vez: el anunciado no era
    el efectivo, y el efectivo ni siquiera se aplicaba."""
    from api_server.seeds.builtin_tools import BUILTIN_TOOLS

    fila = next(t for t in BUILTIN_TOOLS if t.slug == "list-files")
    propiedades = fila.input_schema["properties"]
    _arbol_ci4(tmp_path)
    files = _files(tmp_path)

    anunciado = files.file_list(
        {"path": propiedades["path"]["default"], "pattern": propiedades["pattern"]["default"]}
    )
    omitido = files.file_list({})

    assert _nombres(anunciado) == _nombres(omitido), (
        "el esquema anuncia un default que la implementación no aplica"
    )


def test_the_catalog_announces_the_keys_the_tool_returns(tmp_path: Path) -> None:
    """La otra mitad del cruce: el esquema de SALIDA tampoco puede mentir.

    Anunciaba `{"files": [...]}` con `files` en `required`, y la tool devuelve
    `entries` desde siempre: 893 de 893 salidas reales del `steps_log` traen
    `entries` y ninguna `files`.

    Se cruzan las dos direcciones, y con las DOS formas del resultado —la normal
    y la que lleva `note`—, porque `_obj` marca `additionalProperties: False`:
    una clave que la tool devuelve y el esquema no declara es una promesa rota
    en el sentido contrario.
    """
    from api_server.seeds.builtin_tools import BUILTIN_TOOLS

    fila = next(t for t in BUILTIN_TOOLS if t.slug == "list-files")
    declaradas = set(fila.output_schema["properties"])
    obligatorias = set(fila.output_schema.get("required", ()))
    _arbol_ci4(tmp_path)
    files = _files(tmp_path)

    normal = files.file_list({"path": ".", "pattern": "**/*.php"}).output
    con_nota = files.file_list({"path": ".", "pattern": "*Test.php"}).output
    assert normal is not None and con_nota is not None
    assert "note" in con_nota, "el caso que ejercita la clave opcional no se dio"

    for salida in (normal, con_nota):
        assert set(salida) <= declaradas, (
            f"la tool devuelve claves que el esquema no declara: {set(salida) - declaradas}"
        )
        assert obligatorias <= set(salida), (
            f"el esquema exige claves que la tool no devuelve: {obligatorias - set(salida)}"
        )
    assert "entries" in obligatorias, "la clave que el agente lee de verdad no es obligatoria"


# --- 4. el tope, y que el truncado SE DIGA --------------------------------


def test_a_listing_over_the_cap_is_truncated_and_says_so(tmp_path: Path) -> None:
    """Un truncado silencioso es EXACTAMENTE el defecto que se está cerrando,
    con otra cara: el agente creería que no hay más ficheros."""
    tope = file_tools._MAX_LIST_ENTRIES
    total = tope + 25
    (tmp_path / "src").mkdir()
    for i in range(total):
        (tmp_path / "src" / f"f{i:05d}.php").write_text("x", encoding="utf-8")

    res = _files(tmp_path).file_list({"path": ".", "pattern": "**/*.php"})

    assert res.ok is True, res.error
    assert res.output is not None
    assert len(res.output["entries"]) == tope
    assert res.output["truncated"] is True
    assert res.output["total_matches"] == total
    nota = res.output["note"]
    assert str(total) in nota, "la nota no dice cuántas entradas había de verdad"
    assert "pattern" in nota and "path" in nota, "la nota no dice cómo acotar la búsqueda"


def test_a_listing_under_the_cap_promises_it_is_complete(tmp_path: Path) -> None:
    """`truncated` va SIEMPRE: su ausencia sería ambigua para el modelo, y la
    ambigüedad es lo que le hizo repetir ocho veces."""
    _arbol_ci4(tmp_path)

    res = _files(tmp_path).file_list({"path": ".", "pattern": "**/*.php"})

    assert res.output is not None
    assert res.output["truncated"] is False
    assert res.output["total_matches"] == len(res.output["entries"])
    assert "note" not in res.output, "se mete ruido en el caso normal"


def test_truncation_keeps_the_first_entries_in_path_order(tmp_path: Path) -> None:
    """Determinista: dos llamadas iguales devuelven lo mismo, y el agente puede
    razonar sobre el prefijo que sí ve."""
    tope = file_tools._MAX_LIST_ENTRIES
    for i in range(tope + 10):
        (tmp_path / f"f{i:05d}.php").write_text("x", encoding="utf-8")

    nombres = _nombres(_files(tmp_path).file_list({"path": ".", "pattern": "*.php"}))

    assert nombres == sorted(nombres)
    assert nombres[0] == "f00000.php"
    assert nombres[-1] == f"f{tope - 1:05d}.php"


# --- 5. el vacío que ENSEÑA en vez de mentir ------------------------------


def test_zero_matches_explains_how_to_widen_the_search(tmp_path: Path) -> None:
    """El agente probó ocho patrones porque ninguna respuesta le decía nada.
    Un cero sin explicación es indistinguible de «no existe»."""
    _arbol_ci4(tmp_path)

    res = _files(tmp_path).file_list({"path": ".", "pattern": "*Test.php"})

    assert res.ok is True, res.error
    assert res.output is not None
    assert res.output["entries"] == []
    assert "**/*Test.php" in res.output["note"], (
        "la nota no propone la forma recursiva del patrón que el modelo mandó"
    )


def test_zero_matches_says_how_many_entries_it_visited(tmp_path: Path) -> None:
    _arbol_ci4(tmp_path)

    res = _files(tmp_path).file_list({"path": ".", "pattern": "**/*.rs"})

    assert res.output is not None
    assert res.output["entries"] == []
    recorridas = sum(1 for _ in tmp_path.rglob("*"))
    nota = res.output["note"]
    assert "**/" not in nota, "propone recursividad a un patrón que ya la lleva"
    assert str(recorridas) in nota, (
        "la nota no dice cuántas entradas se recorrieron: sin ese dato, un cero es "
        "indistinguible de un directorio vacío"
    )


def test_a_literal_prefix_keeps_the_search_out_of_the_rest_of_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`app/**/*.php` no puede casar nada bajo `vendor/`, así que no se baja ahí.

    Medido el 2026-09-01 sobre un árbol de 10.400 entradas con la forma del
    incidente: sin la poda, este patrón —el mismo que la nota de truncado
    recomienda para acotar— recorría el árbol entero y tardaba 176 ms en
    devolver UNA coincidencia. Recomendarle al modelo que acote y que acotar no
    le salga más barato es recomendarle humo.

    Se observa por dónde abre el recorrido, que es la conducta que se afirma;
    el resultado tiene que salir idéntico, y eso se comprueba en el mismo test.
    """
    _arbol_ci4(tmp_path)
    abiertos: list[str] = []
    real = file_tools.os.scandir

    def _espia(ruta: Path) -> object:
        abiertos.append(str(ruta))
        return real(ruta)

    monkeypatch.setattr(file_tools.os, "scandir", _espia)

    res = _files(tmp_path).file_list({"path": ".", "pattern": "app/**/*.php"})

    assert _nombres(res) == [
        "app/Config/App.php",
        "app/Config/Routes.php",
        "app/Controllers/Home.php",
        "app/Views/welcome.php",
    ]
    assert not [ruta for ruta in abiertos if "vendor" in ruta], (
        f"se bajó a donde el prefijo literal del patrón no llega: {abiertos}"
    )


def test_a_non_recursive_pattern_never_descends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El default `*` mira UN nivel, y ni siquiera abre los subdirectorios.

    Su hermana la poda por prefijo ya tenía test; ésta no, y una verificación
    adversarial lo destapó quitando la guarda (`profundidad = None`): los 161
    tests seguían en verde mientras el default pasaba de **7,8 ms a 810 ms** —
    unas 100 veces— sobre el worktree real del incidente (11.956 entradas), y un
    `list_files {}` de 9,1 ms a 1.466 ms.

    El docstring del recorrido afirma que «el arreglo no encarece el caso
    normal». Esto es lo que lo sostiene: sin esta guarda la afirmación sería
    falsa y nadie se enteraría, que es la misma forma del defecto que este
    fichero entero viene a cerrar — algo escrito y nunca contrastado.

    Se afirma sobre la CONDUCTA (qué directorios se abren) y no sobre el tiempo,
    que en una máquina cargada es una fuente de falsos rojos.
    """
    _arbol_ci4(tmp_path)
    abiertos: list[str] = []
    real = file_tools.os.scandir

    def _espia(ruta: Path) -> object:
        abiertos.append(str(ruta))
        return real(ruta)

    monkeypatch.setattr(file_tools.os, "scandir", _espia)

    res = _files(tmp_path).file_list({"path": "."})

    assert res.ok, res.error
    assert len(abiertos) == 1, (
        "un patrón sin `**` recorrió más de un directorio: la guarda de "
        f"profundidad no está frenando el descenso. Abiertos: {abiertos}"
    )
    assert not [ruta for ruta in abiertos if "vendor" in ruta], (
        f"se bajó a `vendor/` con el patrón por defecto: {abiertos}"
    )


def test_an_empty_directory_says_it_is_empty(tmp_path: Path) -> None:
    (tmp_path / "build").mkdir()

    res = _files(tmp_path).file_list({"path": "build", "pattern": "**/*"})

    assert res.output is not None
    assert res.output["entries"] == []
    assert "empty" in res.output["note"]


# --- 6. un patrón que no se puede cumplir se RECHAZA, no se ignora --------


def test_a_non_string_pattern_is_rejected_with_an_actionable_error(tmp_path: Path) -> None:
    """Ignorarlo es literalmente el defecto que se arregla."""
    _arbol_ci4(tmp_path)
    files = _files(tmp_path)

    for basura in (["*.php"], 7, {"glob": "*"}, True):
        res = files.file_list({"path": ".", "pattern": basura})
        assert res.ok is False, f"pattern={basura!r} se aceptó y se ignoró"
        assert "'pattern'" in (res.error or "")
        assert "**/*.php" in (res.error or ""), "el error no enseña una forma válida"


def test_an_unparsable_pattern_is_rejected(tmp_path: Path) -> None:
    """Devolver `[]` sobre un patrón que no se pudo interpretar le haría creer
    al agente que el fichero no está."""
    _arbol_ci4(tmp_path)
    files = _files(tmp_path)

    llaves = files.file_list({"path": ".", "pattern": "composer.{json,lock"})
    assert llaves.ok is False
    assert "composer.{json,lock}" in (llaves.error or ""), (
        "el error no enseña la forma equilibrada del patrón que el modelo mandó"
    )

    corchete = files.file_list({"path": ".", "pattern": "[abc*.php"})
    assert corchete.ok is False
    assert "[" in (corchete.error or "")


def test_an_absolute_or_traversing_pattern_is_rejected(tmp_path: Path) -> None:
    """`/etc/**` no casaría nada nunca, y ese `[]` diría «no existe» en vez de
    «estás preguntando fuera del workspace»."""
    _arbol_ci4(tmp_path)
    files = _files(tmp_path)

    for patron in ("/etc/**", "../**/*.php", "app/../../*.php"):
        res = files.file_list({"path": ".", "pattern": patron})
        assert res.ok is False, f"pattern={patron!r} devolvió un vacío que miente"
        assert "relative" in (res.error or "").lower()


# --- 7. lo que ya estaba y no se puede romper -----------------------------


def test_cli_artifacts_stay_hidden_at_any_depth(tmp_path: Path) -> None:
    """Se ocultaban en el listado plano; con recursividad hay que podar el
    subárbol entero o `.claude/` reaparecería por la puerta de atrás."""
    _arbol_ci4(tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".claude.json").write_text("{}", encoding="utf-8")

    nombres = _nombres(_files(tmp_path).file_list({"path": ".", "pattern": "**/*"}))

    assert not [n for n in nombres if ".claude" in n], (
        f"los artefactos del CLI reaparecen con el patrón recursivo: {nombres}"
    )


def test_the_path_jail_still_holds_with_a_pattern(tmp_path: Path) -> None:
    (tmp_path.parent / "fuera.php").write_text("x", encoding="utf-8")
    (tmp_path / "app").mkdir()

    res = _files(tmp_path).file_list({"path": "../", "pattern": "**/*.php"})

    assert res.ok is False
    assert "escapes the workspace" in (res.error or "")


def test_list_files_wired_applies_the_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Por la misma puerta por la que la llama el agente: sin este test, todo lo
    de arriba puede estar verde y la tool registrada seguir siendo la vieja."""
    _arbol_ci4(tmp_path)
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))

    registry = ToolRegistry()
    register_builtin_families(
        registry,
        api=None,
        sink=OrchestrationSink(),
        flags={f: f == FAMILY_FILE for f in ALL_FAMILIES},
    )

    res = registry.call("list_files", {"path": "", "pattern": "tests/**/*.php"})

    assert res.ok is True, res.error
    assert res.output is not None
    assert [e["name"] for e in res.output["entries"]] == [
        "tests/HomeTest.php",
        "tests/_support/bootstrap.php",
        "tests/unit/RouteTest.php",
    ]
