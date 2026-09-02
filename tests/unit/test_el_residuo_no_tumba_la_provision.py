"""Un residuo del runtime que no se pudo descartar no puede tumbar las provisiones siguientes.

## El defecto (auditoría 2026-09-01)

Las tools destructivas de la familia `file` apartan antes de destruir (ADR
0164): renombran el árbol a `.agent-runtime-tmp.<nombre>.<n>` y lo descartan
después. Cuando el descarte no puede —un subdirectorio sin permiso de
escritura que dejó otro contenedor, un fichero de sólo lectura— el residuo se
queda, y ahí estaba el problema: ANTES ese contenido imborrable vivía dentro de
`vendor/`, preservado por el `-e vendor` del `git clean`, y nadie lo tocaba.
Ahora vive bajo un nombre que NO se preserva, y `git clean -fdx` intenta
borrarlo en cada `sync_to_head`, no puede, y sale con rc=1 (verificado):
`GitCommandError`, provisión fallida, `workspace_unavailable` en CADA reintento
de la tarea. El patrón que existía para no perder datos convertía un residuo en
un worktree inservible.

## La corrección

`sync_to_head` barre los residuos ANTES del `git clean`, con un `rmtree` que da
permiso de escritura y reintenta (`rmtree_forzado`): resuelve exactamente los
dos casos medidos. Lo que ni así se puede borrar se registra y se PRESERVA en el
`clean`, para que la provisión siga adelante: un residuo huérfano es un fallo
menor; una tarea que no puede volver a arrancar no lo es.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager, rmtree_forzado

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.unit

#: El literal del runtime, escrito a mano a propósito (ver
#: `test_el_residuo_del_runtime_no_llega_al_commit.py`).
PREFIJO = ".agent-runtime-tmp."


def _setup(tmp_path: Path) -> tuple[Path, WorktreeManager]:
    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend")
    seed_bare_repo(bare)
    return bare, WorktreeManager(layout, "backend")


def _residuo_imborrable(raiz: Path, nombre: str) -> Path:
    """Un residuo con lo que un `rmtree` a secas no puede: fichero de sólo lectura
    (lo que bloquea en Windows) dentro de un directorio sin permiso de escritura
    (lo que bloquea en POSIX)."""
    residuo = raiz / f"{PREFIJO}{nombre}.0"
    (residuo / "pkg").mkdir(parents=True)
    fichero = residuo / "pkg" / "a.php"
    fichero.write_text("<?php\n", encoding="utf-8")
    os.chmod(fichero, stat.S_IREAD)
    if os.name != "nt":
        os.chmod(residuo / "pkg", stat.S_IRUSR | stat.S_IXUSR)
    return residuo


def test_rmtree_forzado_borra_lo_que_rmtree_no_puede(tmp_path: Path) -> None:
    residuo = _residuo_imborrable(tmp_path, "vendor")

    rmtree_forzado(residuo)

    assert not residuo.exists()


def test_la_provision_barre_el_residuo_y_sincroniza(tmp_path: Path) -> None:
    """El caso entero: residuo imborrable en el worktree, y la tarea vuelve a arrancar."""
    _bare, mgr = _setup(tmp_path)
    wt = mgr.add("t1", branch="plan/aaaa-x")
    residuo = _residuo_imborrable(wt, "vendor")

    mgr.sync_to_head("t1", branch="plan/aaaa-x", preserve=("vendor",))

    assert not residuo.exists(), "el residuo sigue ahí: el siguiente `git clean` volverá a fallar"


def test_un_residuo_anidado_tambien_se_barre(tmp_path: Path) -> None:
    """El residuo aparece AL LADO de su objetivo, a cualquier profundidad."""
    _bare, mgr = _setup(tmp_path)
    wt = mgr.add("t2", branch="plan/bbbb-y")
    (wt / "app" / "Config").mkdir(parents=True)
    residuo = _residuo_imborrable(wt / "app" / "Config", "cache")

    mgr.sync_to_head("t2", branch="plan/bbbb-y", preserve=("vendor",))

    assert not residuo.exists()


def test_sync_to_head_barre_antes_de_limpiar() -> None:
    """El ORDEN sobre el AST: si el barrido fuera después del `clean`, nunca se
    llegaría (el `clean` es justo lo que revienta). En Windows git borra por sí
    solo los ficheros de sólo lectura y el test de comportamiento pasaría sin
    el barrido; el que lo fija de verdad es Linux, donde corre el worker."""
    import ast
    import inspect
    import textwrap

    from workers import git_repos

    # `dedent` y no `cleandoc`: es un MÉTODO, y cleandoc desindenta la primera
    # línea de forma distinta al resto, dejando el cuerpo sin indentación.
    fuente = textwrap.dedent(inspect.getsource(git_repos.WorktreeManager.sync_to_head))
    arbol = ast.parse(fuente)
    llamadas: list[str] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Call):
            if isinstance(nodo.func, ast.Name):
                llamadas.append(nodo.func.id)
            elif isinstance(nodo.func, ast.Attribute):
                llamadas.append(nodo.func.attr)
    assert "barrer_residuos_del_runtime" in llamadas, "sync_to_head no barre los residuos"
    assert "clean_args" in llamadas, "¿cambió de forma el clean?"
    assert llamadas.index("barrer_residuos_del_runtime") < llamadas.index("clean_args"), (
        "el barrido va DESPUÉS del clean, que es lo que revienta con el residuo"
    )


def test_el_barrido_no_toca_nada_que_no_sea_residuo(tmp_path: Path) -> None:
    """Sólo el prefijo del runtime. Un `vendor/` preservado sigue intacto."""
    _bare, mgr = _setup(tmp_path)
    wt = mgr.add("t3", branch="plan/cccc-z")
    (wt / "vendor").mkdir()
    (wt / "vendor" / "autoload.php").write_text("<?php\n", encoding="utf-8")
    (wt / "agent-runtime-tmp.parecido").mkdir()  # sin el punto inicial: no es residuo

    mgr.sync_to_head("t3", branch="plan/cccc-z", preserve=("vendor",))

    assert (wt / "vendor" / "autoload.php").is_file(), "`preserve` dejó de respetarse"
    # El parecido no es residuo: lo barre `git clean` como cualquier basura, y da
    # igual quién lo haga; lo que este test fija es que el barrido de residuos no
    # se lleva `vendor/`.
