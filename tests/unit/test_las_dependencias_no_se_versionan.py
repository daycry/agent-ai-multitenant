"""Un directorio de dependencias no entra en la rama del plan — y si ya entró, sale.

## El punto muerto que esto cierra (medido el 2026-09-01)

Plan `01a059db`, proyecto «Hello World CI4 v3», tenant mediapro. Una tarea cuyo
trabajo era comprobar `php -v` corrió además `composer install` para poder
enseñarle una prueba al reviewer. Al cerrar, `commit_task` hizo `git add -A`;
como CodeIgniter **no trae `.gitignore`** (comprobado sobre la instalación
intacta: 14 entradas en la raíz, ninguna es `.gitignore`), se llevó **1.151
ficheros de `vendor/`** a la rama del plan.

A partir de ahí la tarea siguiente se quedó atascada, y lo hizo por la puerta
más incómoda: la guarda del ADR 0164 —«no borro recursivamente algo versionado,
es trabajo ya commiteado de una tarea anterior»— se negó a retirar `vendor/`
tanto por `delete_file` como por `move_file`. La guarda hizo exactamente lo que
se diseñó, y por eso se equivocaba: `vendor/` no es trabajo de nadie. De propina,
`list_files` pasó a costar ~9.100 tokens por iteración y el run murió en
`max_tokens_exceeded` a las 12 iteraciones.

## Por qué la decisión ya estaba tomada

`shared_test_runtimes.catalog` declara los directorios de dependencias POR
RUNTIME (`vendor` en los php, `node_modules` en los node, `.venv`/`venv` en
python) y expone su unión en `dependency_dirs()`. Su único consumidor,
`execution._provision_worktree`, se los pasa a `sync_to_head(preserve=...)` para
que el `clean -fdx` NO se los lleve.

O sea que la plataforma **ya** trata esos directorios como «no forman parte del
entregable». Hasta hoy decía las dos cosas a la vez: «preservo `vendor/` porque
no es tuyo» y «te lo commiteo porque `git add -A`». Estos tests fijan la mitad
que faltaba.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from shared_test_runtimes import catalog
from workers.git_identity import PLATFORM_GIT_EMAIL, PLATFORM_GIT_NAME
from workers.git_repos import GitCommandError
from workers.plan_git import CommitTrailers, commit_task

pytestmark = pytest.mark.unit

#: Una PERSONA: quien clona el repo y commitea fuera de la plataforma.
_PERSONA = ("harness", "harness@example.com")
#: La PLATAFORMA: la identidad con la que `commit_task` firma cada commit de tarea.
_PLATAFORMA = (PLATFORM_GIT_NAME, PLATFORM_GIT_EMAIL)


def _git(*args: str, cwd: Path | None = None, identidad: tuple[str, str] = _PERSONA) -> str:
    """git con identidad fija: el arnés no depende del ~/.gitconfig del host.

    La identidad NO es cosmética desde el 2026-09-01: la plataforma sólo deshace
    los directorios de dependencias que commiteó ELLA MISMA, así que quién firma
    un commit decide si su `vendor/` se respeta o se des-versiona.
    """
    nombre, email = identidad
    proc = subprocess.run(
        [
            "git",
            "-c",
            f"user.email={email}",
            "-c",
            f"user.name={nombre}",
            "-c",
            "safe.bareRepository=all",
            *args,
        ],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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


def _escribe(worktree_path: Path, ruta: str, contenido: str = "<?php\n") -> Path:
    destino = worktree_path / ruta
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(contenido, encoding="utf-8")
    return destino


def _versiona_como_antes(worktree_path: Path, rutas: dict[str, str]) -> None:
    """Reproduce el `git add -A` SIN exclusiones que causó el incidente.

    No usa `commit_task` a propósito: el estado de partida que hay que desatascar
    lo dejaron ramas commiteadas con el código anterior, y un test que lo
    fabricase con el código nuevo no probaría nada. Pero SÍ firma como la
    plataforma, porque eso es lo que era el accidente: un `commit_task` viejo.
    """
    for ruta, contenido in rutas.items():
        _escribe(worktree_path, ruta, contenido)
    _git("add", "-A", cwd=worktree_path, identidad=_PLATAFORMA)
    _git(
        "commit",
        "-qm",
        "la tarea anterior se llevo vendor/ por delante",
        cwd=worktree_path,
        identidad=_PLATAFORMA,
    )


def _versiona_una_persona(worktree_path: Path, rutas: dict[str, str], mensaje: str) -> None:
    """Lo que hace un humano en su clon: versiona a propósito y empuja."""
    for ruta, contenido in rutas.items():
        _escribe(worktree_path, ruta, contenido)
    _git("add", "-A", cwd=worktree_path, identidad=_PERSONA)
    _git("commit", "-qm", mensaje, cwd=worktree_path, identidad=_PERSONA)


def _ficheros_del_commit(worktree_path: Path, sha: str) -> list[str]:
    """Los ficheros del commit, con `-z`.

    El `-z` no es adorno y este helper lo aprendió a base de fallar: sin él, git
    devuelve los nombres no-ASCII **entrecomillados al estilo C**
    (`"vendor/se\303\261al.php"`), que no empiezan por `vendor/`. El test que
    comprueba justamente que un `vendor/` acentuado se des-versiona pasaba en
    vacío por eso — tenía dentro el mismo defecto que venía a cazar. Verificado:
    con `-z` muere al quitar el des-versionado; sin `-z`, no.
    """
    salida = _git("ls-tree", "-r", "-z", "--name-only", sha, cwd=worktree_path)
    return [entrada for entrada in salida.split(chr(0)) if entrada]


def _commit(worktree_path: Path) -> str:
    return commit_task(
        worktree_path,
        message="wip: la tarea entrega su trabajo",
        trailers=CommitTrailers(
            plan_id="01a059db-a2af-72c2-a1d3-e62747987a08",
            task_id="01a05849-0438-769e-86ba-712d44e2c38a",
            execution_id="01a05881-89d7-79fa-be72-bd0e7c1a9fbb",
        ),
    )


# ---------------------------------------------------------------------------
# 1. Que no entren
# ---------------------------------------------------------------------------


def test_el_deliverable_de_verdad_si_se_commitea(worktree: Path) -> None:
    """La cara positiva, primero: sin ella los tests de abajo pasarían en vacío.

    Un filtro que excluyera de más dejaría el commit sin el trabajo de la tarea
    y este fichero seguiría en verde, que es la peor forma de «arreglarlo».
    """
    _escribe(worktree, "app/Controllers/Home.php")

    assert "app/Controllers/Home.php" in _ficheros_del_commit(worktree, _commit(worktree))


def test_el_vendor_recien_instalado_no_entra_en_la_rama(worktree: Path) -> None:
    """El caso medido: `composer install` deja 1.151 ficheros que no son de nadie."""
    _escribe(worktree, "app/Controllers/Home.php")
    _escribe(worktree, "vendor/autoload.php")
    _escribe(worktree, "vendor/codeigniter4/framework/System/Boot.php")

    ficheros = _ficheros_del_commit(worktree, _commit(worktree))

    colados = [f for f in ficheros if f.startswith("vendor/")]
    assert not colados, (
        f"vendor/ entró en la rama del plan: {colados}. A partir de aquí la guarda "
        "del ADR 0164 lo blinda y la tarea siguiente no puede andamiar."
    )
    assert "app/Controllers/Home.php" in ficheros, "y el deliverable tiene que seguir entrando"


def test_el_node_modules_de_un_subproyecto_tampoco(worktree: Path) -> None:
    """A CUALQUIER profundidad: un monorepo tiene `frontend/node_modules/`.

    Es el mismo motivo por el que `dependency_dirs()` devuelve la UNIÓN de los
    runtimes en vez de los del template declarado.
    """
    _escribe(worktree, "frontend/src/App.tsx", "export default () => null;\n")
    _escribe(worktree, "frontend/node_modules/react/index.js", "module.exports = {};\n")
    _escribe(worktree, "backend/vendor/autoload.php")

    ficheros = _ficheros_del_commit(worktree, _commit(worktree))

    colados = [f for f in ficheros if "node_modules" in f or "vendor" in f]
    assert not colados, f"dependencias anidadas coladas en el commit: {colados}"
    assert "frontend/src/App.tsx" in ficheros


def test_lo_que_solo_se_parece_a_una_dependencia_si_entra(worktree: Path) -> None:
    """La exclusión no puede ser más ancha de lo que dice.

    `vendors/` (plural), un fichero llamado `vendor` y una clase `VendorService`
    son código del proyecto. Excluirlos sería perder deliverable en silencio, que
    es peor que el defecto que se está cerrando.
    """
    _escribe(worktree, "app/Services/VendorService.php")
    _escribe(worktree, "vendors/Proveedor.php")
    _escribe(worktree, "bin/vendor", "#!/bin/sh\n")
    _escribe(worktree, "docs/node_modules-explicado.md", "# nota\n")

    ficheros = _ficheros_del_commit(worktree, _commit(worktree))

    for esperado in (
        "app/Services/VendorService.php",
        "vendors/Proveedor.php",
        "bin/vendor",
        "docs/node_modules-explicado.md",
    ):
        assert esperado in ficheros, (
            f"la exclusión se llevó por delante {esperado!r}, que es deliverable"
        )


# ---------------------------------------------------------------------------
# 2. Y que salgan las que ya entraron — la mitad que desatasca
# ---------------------------------------------------------------------------


def test_un_vendor_ya_versionado_sale_del_indice(worktree: Path) -> None:
    """Excluir de FUTUROS commits no arregla la rama donde el artefacto ya entró:
    `sync_to_head` lo sigue trayendo y la guarda lo sigue blindando."""
    _versiona_como_antes(
        worktree,
        {
            "vendor/autoload.php": "<?php\n",
            "vendor/codeigniter4/framework/System/Boot.php": "<?php\n",
            "app/Controllers/Home.php": "<?php\n",
        },
    )
    _escribe(worktree, "app/Controllers/Nuevo.php")

    ficheros = _ficheros_del_commit(worktree, _commit(worktree))

    assert not [f for f in ficheros if f.startswith("vendor/")], (
        "vendor/ sigue versionado en la rama: la tarea siguiente seguirá atascada"
    )
    assert "app/Controllers/Home.php" in ficheros, (
        "el des-versionado se llevó por delante el entregable de la tarea anterior"
    )
    assert "app/Controllers/Nuevo.php" in ficheros


def test_el_desversionado_jamas_borra_del_disco(worktree: Path) -> None:
    """El requisito que no se puede fallar.

    El agente y el toolchain NECESITAN ese `vendor/` para trabajar — por eso
    `sync_to_head` lo preserva. `git rm --cached` des-versiona sin tocar el
    árbol de trabajo; cualquier variante que borre del disco rompe el proyecto.
    """
    _versiona_como_antes(
        worktree,
        {"vendor/autoload.php": "<?php // el autoload de composer\n"},
    )
    _escribe(worktree, "app/Controllers/Home.php")

    _commit(worktree)

    assert (worktree / "vendor" / "autoload.php").is_file(), (
        "se ha borrado vendor/ del disco: el toolchain de la tarea siguiente no arranca"
    )
    assert (worktree / "vendor" / "autoload.php").read_text(encoding="utf-8") == (
        "<?php // el autoload de composer\n"
    ), "el contenido de la dependencia no puede cambiar al des-versionarla"


def test_desversionar_es_un_cambio_y_produce_commit(worktree: Path) -> None:
    """Aunque la tarea no tocase nada más.

    Si esto se tratara como «árbol limpio», el des-versionado nunca llegaría al
    bare, `sync_to_head` volvería a traer `vendor/` y el punto muerto seguiría
    exactamente igual. Un des-versionado SÍ es un cambio.
    """
    _versiona_como_antes(worktree, {"vendor/autoload.php": "<?php\n"})

    sha = _commit(worktree)

    assert not [f for f in _ficheros_del_commit(worktree, sha) if f.startswith("vendor/")]
    assert "vendor/autoload.php" in _ficheros_del_commit(worktree, f"{sha}^"), (
        "el arnés no reprodujo el estado de partida: vendor/ tenía que estar versionado"
    )


def test_sin_dependencias_versionadas_el_arbol_limpio_sigue_estando_limpio(
    worktree: Path,
) -> None:
    """Regresión: si no hay nada que des-versionar, `commit_task` se comporta
    EXACTAMENTE como antes — el llamador sigue leyendo «la tarea no produjo
    cambio» y se salta el push."""
    with pytest.raises(GitCommandError, match="clean"):
        _commit(worktree)


def test_queda_registrado_cuantos_ficheros_y_de_donde(
    worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un cambio silencioso en lo que se commitea es un defecto, no una mejora.

    Se afirma sobre un logger falso y no con `caplog`: la app hace
    `logging.disable` y el resultado depende del orden de los tests (gotcha
    transversal del repo).
    """
    from workers import plan_git

    eventos: list[tuple[str, dict[str, Any]]] = []

    class _LogFalso:
        def __getattr__(self, nivel: str) -> Any:
            def _registrar(evento: str, **kw: Any) -> None:
                eventos.append((evento, kw))

            return _registrar

    monkeypatch.setattr(plan_git, "_log", _LogFalso())

    _versiona_como_antes(
        worktree,
        {
            "vendor/autoload.php": "<?php\n",
            "vendor/pkg/Lib.php": "<?php\n",
            "frontend/node_modules/react/index.js": "module.exports = {};\n",
        },
    )
    _commit(worktree)

    registros = [kw for evento, kw in eventos if evento == "commit_task.dependencies_unversioned"]
    assert registros, (
        "el des-versionado no dejó rastro: nadie podría explicar por qué el commit "
        f"borra ficheros que nadie tocó. Eventos vistos: {[e for e, _ in eventos]}"
    )
    registro = registros[0]
    assert registro["files"] == 3
    assert registro["by_directory"] == {"vendor": 2, "frontend/node_modules": 1}


def test_cada_directorio_del_catalogo_queda_cubierto(worktree: Path) -> None:
    """La lista NO se escribe aquí: sale de `dependency_dirs()`.

    Se comprueba por comportamiento —se versiona un fichero dentro de cada
    directorio declarado y ninguno sobrevive al commit— para que añadir un
    runtime nuevo al catálogo quede cubierto sin tocar este test.
    """
    nombres = catalog.dependency_dirs()
    assert nombres, "el catálogo no declara ningún directorio de dependencias"

    _versiona_como_antes(
        worktree,
        {f"{nombre}/instalado.txt": "artefacto reconstruible\n" for nombre in nombres},
    )
    _escribe(worktree, "app/Controllers/Home.php")

    ficheros = _ficheros_del_commit(worktree, _commit(worktree))

    supervivientes = [f for f in ficheros if f.split("/")[0] in nombres]
    assert not supervivientes, (
        f"estos directorios del catálogo siguen versionados: {supervivientes}"
    )


def test_una_tarea_que_solo_deja_dependencias_se_lee_como_sin_cambio(worktree: Path) -> None:
    """No como avería, que es lo que empezó a pasar al excluirlas.

    Medido el 2026-09-01: con `vendor/` fuera del `git add -A`, una tarea que
    corrió `composer install` y no entregó nada más deja el directorio SIN
    versionar en el árbol, y entonces git no dice «nothing to commit» sino
    «nothing added to commit but untracked files present». Esa cadena no casa ni
    con el traductor de `commit_task` ni con el `if "clean"` del llamador
    (`execution._commit_and_push_worktree`), así que el run moría con un error de
    git donde antes —cuando el `add -A` se llevaba los 1.151 ficheros— había un
    commit. El caso es de los comunes: cualquier tarea de un proyecto PHP o node
    que no toque ficheros.
    """
    _escribe(worktree, "vendor/autoload.php")
    _escribe(worktree, "vendor/codeigniter4/framework/System/Boot.php")

    with pytest.raises(GitCommandError, match="clean"):
        _commit(worktree)

    assert (worktree / "vendor" / "autoload.php").is_file(), (
        "y el toolchain de la tarea siguiente sigue teniendo sus dependencias"
    )


#: Un fichero PHP mínimo. Constante para no pelearse con el escape
#: del salto de línea dentro del literal.
_PHP = "<?php" + chr(10)


def test_un_vendor_con_nombres_acentuados_tambien_sale_del_indice(worktree: Path) -> None:
    """El `-z` de `git ls-files`, que sin test sobrevivía a la mutación.

    Sin `-z`, git devuelve los nombres no-ASCII **entrecomillados al estilo C**
    (``"se\303\261al.php"``), la lista de directorios presentes sale vacía, y
    con ella el filtro de pathspecs: **el des-versionado no ocurre**, `vendor/`
    sigue en el commit y la rama sigue atascada exactamente igual que antes.

    Es el mismo defecto que ya costó una vuelta entera hoy en `git_repos._run_git`
    —decodificar la salida de git con el locale del host dejaba la guarda del ADR
    0164 ciega justo con los nombres que abundan en un repo en castellano— y
    reaparece aquí por la otra puerta. Un `vendor/` de paquetes con acentos no es
    exótico: basta una dependencia con un fichero de traducción.
    """
    _versiona_como_antes(
        worktree,
        {
            "vendor/señal.php": _PHP,
            "vendor/año.php": _PHP,
            "vendor/ñandú.php": _PHP,
            "app/Home.php": _PHP,
        },
    )
    _escribe(worktree, "app/Nuevo.php")

    # La premisa del test, afirmada y no supuesta. Sin esto el test PASA EN VACÍO
    # —comprobado: no muere ni quitando el des-versionado entero— porque si el
    # arnés no consigue versionar los nombres acentuados, «no hay vendor/ en el
    # commit» es trivialmente cierto y el test no mide nada.
    versionados = _git("ls-files", "-z", cwd=worktree).split(chr(0))
    acentuados = [f for f in versionados if f.startswith("vendor/") and not f.isascii()]
    assert len(acentuados) == 3, (
        f"el arnés no dejó versionados los tres nombres acentuados ({acentuados}): "
        "este test no puede afirmar nada sobre ellos"
    )

    ficheros = _ficheros_del_commit(worktree, _commit(worktree))

    colados = [f for f in ficheros if f.startswith("vendor/")]
    assert not colados, (
        f"un `vendor/` de nombres acentuados NO se des-versionó: {colados}. "
        "La rama sigue atascada y nada avisa."
    )
    assert "app/Home.php" in ficheros and "app/Nuevo.php" in ficheros


# ---------------------------------------------------------------------------
# 3. La plataforma deshace SUS accidentes, no las decisiones de las personas
# ---------------------------------------------------------------------------
# Auditoría del 2026-09-01. La primera versión de esto des-versionaba TODO
# directorio con nombre de dependencia, en TODOS los proyectos, y lo justificaba
# con «lo declara el runtime template del propio proyecto». Era falso: la lista
# es la UNIÓN de los 14 templates. Reproducido con un proyecto Go que versiona
# `vendor/` a propósito (`go mod vendor`, el flujo canónico, y `go-test` no
# declara `vendor`): el primer commit de la plataforma lo sacaba del índice,
# escribía un `.gitignore` con `vendor/` y dejaba de protegerlo. Lo mismo le pasa
# a `public/vendor/` de Laravel, `assets/vendor/` de Symfony o `vendor/cache` de
# Ruby, que sus propios frameworks mandan commitear.
#
# El criterio que sí separa las dos poblaciones no es el NOMBRE sino la AUTORÍA:
# un `vendor/` que entró por un `commit_task` sin `.gitignore` lo firmó la
# plataforma, y un `vendor/` que una persona commiteó es una decisión. La
# plataforma deshace lo primero y respeta lo segundo.


def test_un_vendor_versionado_por_una_persona_no_se_desversiona(worktree: Path) -> None:
    """El caso Go, reproducido: `go mod vendor` commiteado a propósito."""
    _versiona_una_persona(
        worktree,
        {
            "go.mod": "module example.com/app\n",
            "vendor/modules.txt": "# github.com/x/y v1.0.0\n",
            "vendor/github.com/x/y/y.go": "package y\n",
        },
        "vendoriza las dependencias (go mod vendor)",
    )
    _escribe(worktree, "main.go", "package main\n")

    sha = _commit(worktree)

    ficheros = _ficheros_del_commit(worktree, sha)
    assert "vendor/modules.txt" in ficheros and "vendor/github.com/x/y/y.go" in ficheros, (
        "la plataforma des-versionó un vendor/ que una PERSONA commiteó a propósito: "
        "el PR del plan borra las dependencias del proyecto"
    )
    assert "main.go" in ficheros


def test_los_cambios_del_agente_dentro_de_un_vendor_respetado_si_entran(worktree: Path) -> None:
    """Respetar a medias sería peor: si `vendor/` es del proyecto, sus cambios
    también lo son, y una exclusión ciega del `git add -A` los perdería en
    silencio."""
    _versiona_una_persona(
        worktree,
        {"vendor/modules.txt": "# github.com/x/y v1.0.0\n"},
        "vendoriza",
    )
    _escribe(worktree, "vendor/modules.txt", "# github.com/x/y v1.1.0\n")

    sha = _commit(worktree)

    contenido = _git("show", f"{sha}:vendor/modules.txt", cwd=worktree)
    assert "v1.1.0" in contenido, (
        "el cambio del agente dentro del vendor/ respetado no llegó al commit"
    )


def test_el_gitignore_base_no_ignora_un_directorio_respetado(worktree: Path) -> None:
    """Un `.gitignore` con `vendor/` en un proyecto que lo versiona a propósito
    es una contradicción escrita en el repo: git seguiría rastreando lo ya
    trackeado, pero cualquier fichero NUEVO bajo `vendor/` desaparecería del
    `git add` de quien clone."""
    _versiona_una_persona(worktree, {"vendor/modules.txt": "# x\n"}, "vendoriza")
    _escribe(worktree, "main.go", "package main\n")

    _commit(worktree)

    gitignore = worktree / ".gitignore"
    assert gitignore.is_file(), "el proyecto no traía .gitignore: la plataforma deja el base"
    lineas = [ln.strip() for ln in gitignore.read_text(encoding="utf-8").splitlines()]
    assert "vendor/" not in lineas, (
        "el .gitignore base ignora un directorio que el proyecto versiona"
    )
    assert "node_modules/" in lineas, "y los demás nombres del catálogo siguen en la lista"


def test_se_deshace_el_accidente_y_se_respeta_a_la_persona_en_el_mismo_arbol(
    worktree: Path,
) -> None:
    """Las dos poblaciones conviven en un monorepo, y la decisión es POR
    DIRECTORIO, no por nombre ni por proyecto."""
    _versiona_una_persona(
        worktree,
        {"frontend/node_modules/leftpad/index.js": "module.exports = 1;\n"},
        "node_modules vendorizado a propósito en el frontend",
    )
    _versiona_como_antes(worktree, {"vendor/autoload.php": "<?php\n"})
    _escribe(worktree, "app/Home.php")

    ficheros = _ficheros_del_commit(worktree, _commit(worktree))

    assert not [f for f in ficheros if f.startswith("vendor/")], (
        "el accidente de la plataforma (vendor/) sigue versionado"
    )
    assert "frontend/node_modules/leftpad/index.js" in ficheros, (
        "el node_modules que una persona versionó a propósito se ha des-versionado"
    )


def test_un_accidente_que_una_persona_luego_toco_se_respeta(worktree: Path) -> None:
    """Basta que UNA persona haya tocado el directorio para que deje de ser un
    accidente: a partir de ahí hay trabajo humano dentro, y la plataforma no
    tiene autoridad para retirarlo."""
    _versiona_como_antes(worktree, {"vendor/autoload.php": "<?php\n"})
    _versiona_una_persona(
        worktree, {"vendor/autoload.php": "<?php // parche local\n"}, "parchea vendor"
    )
    _escribe(worktree, "app/Home.php")

    ficheros = _ficheros_del_commit(worktree, _commit(worktree))

    assert "vendor/autoload.php" in ficheros, (
        "se des-versionó un vendor/ con un parche commiteado por una persona"
    )


def test_un_nombre_de_dependencia_invalido_se_rechaza(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La validación de nombres, con el mismo trato que su precedente.

    `dependency_dirs.nombres` valida lo que llega del catálogo antes de meterlo
    en un pathspec. El precedente que cita —`git_repos.clean_args`— SÍ tiene su
    `pytest.raises(ValueError)`; ésta no lo tenía, y la mutación que quitaba la
    validación no mataba ningún test.

    Es defensa en profundidad sobre un catálogo literal, así que el riesgo hoy es
    bajo. Pero un catálogo es exactamente la clase de dato que mañana se lee de
    otro sitio, y entonces la validación es lo único que separa un nombre de
    directorio de un pathspec inyectado.
    """
    from shared_test_runtimes import catalog as runtime_catalog
    from workers.dependency_dirs import nombres

    for veneno in ("../fuera", "vendor/sub", ":(glob)**", "", "  "):
        monkeypatch.setattr(runtime_catalog, "dependency_dirs", lambda v=veneno: (v,))
        with pytest.raises(ValueError):
            nombres()
