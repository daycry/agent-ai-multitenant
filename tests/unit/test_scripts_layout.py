"""`scripts/` separa el tooling de plataforma de los demos de fase.

prod-15 `task_gov_higiene_10` (hallazgo quality-11). La raíz de `scripts/` mezclaba
cinco utilidades que corren en CI y en los hooks (`mypy_gate`, `check_commit_trailers`,
`audit_rbac`, `check_no_secret_artifacts`, `check_pip_audit_report`) con **22
ficheros** de demos de tests humanos. Quien abría el directorio no podía distinguir
lo que sostiene el repo de lo que sirve para enseñar una fase.

## Por qué esta guarda existe y no basta con haberlo movido

El movimiento fue barato; lo caro es que se deshaga a trozos. Un demo nuevo se
escribe en la raíz por costumbre (era su sitio hasta hoy), y a partir de ahí el
directorio vuelve a mezclarse sin que nada avise.

Y hay un modo de fallo peor, que es el que motiva el tercer test: **una guía de
test humano que cita una ruta muerta no rompe nada — rompe a un humano**, en
mitad de una validación, y el síntoma («no such file») no dice si el script se
movió, se borró o nunca existió.

## La trampa que este movimiento tenía escondida

Cinco demos resolvían el estado compartido como
`Path(__file__).resolve().parent.parent / "scripts" / ".demo_state_XX.json"`
mientras el setup que lo ESCRIBE usaba `Path(__file__).parent`. Con todos en
`scripts/` las dos formas daban el mismo fichero; bajando un nivel dejan de
darlo, y el resultado no habría sido un error sino un demo que arranca y dice
«no hay estado, corre el setup» **habiéndolo corrido**. Ahora los dos lados usan
`Path(__file__).parent`, que no depende de dónde viva el directorio; el cuarto
test lo fija.
"""

from __future__ import annotations

import ast
import py_compile
import re
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"
_DEMOS = _SCRIPTS / "demos"
_GUIDES = _REPO_ROOT / "docs" / "03-guides"

#: El tooling de PLATAFORMA: lo que corre en CI, en los hooks de pre-commit o en
#: una auditoría. Es lo único que puede vivir en la raíz de `scripts/`.
PLATFORM_TOOLING: frozenset[str] = frozenset(
    {
        "audit_rbac.py",
        "check_commit_trailers.py",
        "check_e2e_install_report.py",
        "check_no_secret_artifacts.py",
        "check_pip_audit_report.py",
        "mypy_gate.py",
    }
)


def test_the_demos_directory_actually_holds_the_demos() -> None:
    """No-vacuidad: si `scripts/demos/` estuviera vacío, todo lo de abajo pasaría
    por la razón equivocada."""
    demos = sorted(p.name for p in _DEMOS.glob("*.py"))

    assert len(demos) >= 20, f"scripts/demos/ sólo tiene {len(demos)} ficheros: {demos}"
    assert "_demo_common.py" in demos, (
        "`_demo_common.py` se quedó fuera de scripts/demos/. Tiene que viajar CON "
        "los demos: once de ellos hacen `from _demo_common import …`, que sólo "
        "resuelve porque el directorio del script entra en `sys.path`."
    )


def test_the_root_of_scripts_only_holds_platform_tooling() -> None:
    """La raíz de `scripts/` es para lo que sostiene el repo, no para los demos."""
    at_root = {p.name for p in _SCRIPTS.glob("*.py")}
    strays = sorted(at_root - PLATFORM_TOOLING)

    assert not strays, (
        f"ficheros en la raíz de scripts/ que no son tooling de plataforma: {strays}.\n"
        "Los demos de fase viven en `scripts/demos/`. Si de verdad es tooling "
        "(corre en CI, en un hook o en una auditoría), añádelo a PLATFORM_TOOLING "
        "y di en el PR quién lo invoca."
    )


def test_every_demo_still_compiles_where_it_now_lives() -> None:
    """Un rename mecánico no puede dejar un fichero que ni siquiera parsea."""
    with tempfile.TemporaryDirectory() as tmp:
        for path in sorted(_DEMOS.glob("*.py")):
            cfile = str(Path(tmp) / f"{path.stem}.pyc")
            try:
                py_compile.compile(str(path), cfile=cfile, doraise=True)
            except py_compile.PyCompileError as exc:  # pragma: no cover - el mensaje ES el test
                pytest.fail(f"{path.name} no compila tras el movimiento: {exc}")


def test_no_demo_resolves_a_sibling_path_through_the_repo_root() -> None:
    """La trampa del movimiento, fijada.

    `Path(__file__).parent / ".demo_state_X.json"` es robusto a dónde viva el
    directorio; `_REPO_ROOT / "scripts" / ".demo_state_X.json"` no lo es, y su
    fallo no es un error sino un demo que dice «no hay estado» habiendo corrido
    el setup. Que nadie lo reintroduzca.
    """
    offenders: list[str] = []
    for path in sorted(_DEMOS.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'_REPO_ROOT\s*/\s*"scripts"', line):
                offenders.append(f"{path.name}:{lineno}")

    assert not offenders, (
        f"demos que resuelven un fichero HERMANO pasando por la raíz del repo: "
        f"{offenders}. Usa `Path(__file__).resolve().parent / …` — el estado se "
        "escribe junto al script, y así deja de importar dónde viva el directorio."
    )


def test_the_demos_that_need_the_repo_root_still_find_it() -> None:
    """El otro lado de la misma moneda: los que SÍ necesitan la raíz del repo
    (para `tests/integration/_toy_mcp_server.py`) bajaron un nivel, así que
    `parent.parent` ya no vale. Se comprueba resolviendo de verdad."""
    #: Formas conocidas de subir a la raíz, con cuántos niveles sube cada una.
    #: Se resuelven de verdad contra la ruta del fichero — nada de `eval`.
    ladders = {
        "Path(__file__).resolve().parent.parent": 2,
        "Path(__file__).resolve().parents[1]": 2,
        "Path(__file__).resolve().parents[2]": 3,
        "Path(__file__).resolve().parents[3]": 4,
    }
    seen = 0
    for path in sorted(_DEMOS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if "_REPO_ROOT" not in [t.id for t in node.targets if isinstance(t, ast.Name)]:
                continue
            seen += 1
            source = ast.unparse(node.value)
            levels = ladders.get(source)
            assert levels is not None, (
                f"{path.name}: forma desconocida de resolver la raíz ({source!r}). "
                "Añádela a `ladders` con cuántos niveles sube, para que la guarda "
                "siga pudiendo comprobar a dónde llega."
            )
            resolved = path.resolve().parents[levels - 1]
            assert resolved == _REPO_ROOT, (
                f"{path.name}: `_REPO_ROOT` resuelve a {resolved}, no a la raíz del "
                f"repo ({_REPO_ROOT}). Tras el movimiento hacen falta `parents[2]`, "
                "no `parent.parent`."
            )
    assert seen >= 3, (
        f"la guarda sólo encontró {seen} demos con `_REPO_ROOT`: ¿cambió la forma "
        "de resolver la raíz? Si ya no la usa ninguno, retira este test."
    )


def test_no_guide_points_a_human_at_the_old_path() -> None:
    """Una guía que manda ejecutar una ruta muerta rompe a un humano en mitad de
    una validación, y el «no such file» no dice si el script se movió, se borró o
    nunca existió.

    Cubre `docs/03-guides/` —lo operativo—, no los ADR/changelog/auditorías, que
    narran lo que pasó entonces y donde reescribir la ruta sería falsear el
    relato.
    """
    stale = re.compile(r"scripts[/\\](?:demo_human_|setup_demo_|_demo_common|\.demo_state)")
    offenders: list[str] = []
    scanned = 0
    for path in sorted(_GUIDES.rglob("*.md")):
        scanned += 1
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if stale.search(line):
                offenders.append(f"{path.relative_to(_REPO_ROOT).as_posix()}:{lineno}")

    assert scanned >= 30, f"la guarda sólo leyó {scanned} guías: ¿cambió docs/03-guides/?"
    assert not offenders, (
        f"guías que citan la ruta vieja de un demo: {offenders}. "
        "Los demos viven en `scripts/demos/` desde prod-15 task_gov_higiene_10."
    )


def test_the_ignored_state_files_are_ignored_where_they_are_written() -> None:
    """Los demos escriben su estado JUNTO a sí mismos. Si `.gitignore` sigue
    apuntando a `scripts/`, el primer `setup_demo_*` deja seis ficheros de estado
    —con ids de tenant y de proyecto— listos para colarse en un commit.

    Y el reverso, que pasó de verdad al mover esto: cambiar los patrones DESIGNORÓ
    los seis ficheros de estado que ya había en `scripts/`, que aparecieron como
    untracked. Se movieron con los scripts. Si vuelves de una rama anterior con
    estado viejo en `scripts/`, muévelo tú también en vez de re-añadir el patrón:
    el código ya no lo lee de ahí.
    """
    ignored = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    patterns = [
        line.strip()
        for line in ignored.splitlines()
        if not line.lstrip().startswith("#") and (".demo_state" in line or ".demo_06" in line)
    ]

    assert len(patterns) >= 6, f"esperaba al menos 6 patrones de estado de demo, vi {patterns}"
    wrong = [p for p in patterns if not p.startswith("scripts/demos/")]
    assert not wrong, (
        f"patrones de `.gitignore` que siguen apuntando al sitio viejo: {wrong}. "
        "El estado se escribe con `Path(__file__).parent`, o sea en scripts/demos/."
    )
