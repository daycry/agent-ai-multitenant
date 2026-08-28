"""Guarda estática: la documentación del instalador no puede prometer lo que el
instalador no hace.

Hermana de :mod:`tests.docs.test_installer_runbook_no_simulated_as_real`, que ya
vigila **un** documento —el runbook `06-runbooks/01-installation-from-scratch.md`—
y lo dejó impecable. El problema es que la mentira se había mudado: el runbook
dice la verdad desde prod-01, y mientras tanto el README del propio instalador
afirmaba lo contrario (`apps/installer/README.md`: «The installer actually
provisions a real stack (Docker, `pg_*`, Vault)»), la referencia de producto
describía el paso 9 del wizard revelando credenciales reales, y la guía de tests
humanos mandaba a un operador a una VM virgen a lanzar `./scripts/install.sh` sin
`--config` «modo wizard».

**Por qué esto es el defecto caro y no una errata.** Un runbook lo abre quien ya
está instalando; un README y una referencia los lee quien está **diseñando** —el
camino sin clon, el empaquetado, el presupuesto. Quien diseñó sobre
`apps/installer/README.md:51` diseñó sobre una premisa falsa, y el error no
aparece hasta que alguien intenta instalar de verdad, meses después. Es
exactamente el modo de fallo de `docs/03-guides/verificar-antes-de-implementar.md`:
ningún error, sólo trabajo perdido y confianza injustificada.

## Qué se afirma aquí, y contra qué está anclado

Nada de lo que sigue está escrito a mano dos veces. Las dos afirmaciones sobre
las que descansa todo se **leen del código**, para que la guarda no envejezca
buscando cadenas que ya no existen:

* **El wizard HTTP sigue siendo una simulación** — se comprueba instanciando el
  seam por defecto (`installer_backend.main.get_step_executor`) y mirando si es
  un `FakeStepExecutor`. El día que alguien cablee el ejecutor real (follow-up
  prod-09), esta guarda cae **la primera**, y su mensaje dice qué documentos hay
  que corregir en el mismo commit. Una guarda que se retira sola es la única que
  no se convierte en peaje.
* **`install.sh` sin `--config` no abre ningún wizard** — se comprueba pidiéndole
  al `argparse` real de `installer_backend.cli` que parsee `["install"]`. Sale
  con `USAGE` (código 1) antes de tocar nada. Cualquier documento que enseñe esa
  línea como forma de arrancar el wizard está enseñando un comando que no
  funciona.

## Las dos mitades

**Negativa** — ningún documento puede afirmar que el wizard aprovisiona, ni que
wizard y CLI corren la misma orquestación. Son las frases concretas que estaban
escritas, con su porqué al lado, porque una lista de prohibiciones sin motivo se
borra en cuanto estorba.

**Positiva** — cada documento que describe el wizard tiene que **marcarlo** como
simulación, y los que llevan a un operador al camino real tienen que decir que la
avería de las rutas relativas existe. Sin la mitad positiva bastaría borrar toda
mención del wizard para pasar en verde, que es la vacuidad del §4 de
`verificar-antes-de-implementar.md`: sin frases prohibidas, sin avisos, y el
corpus mintiendo por omisión.

La avería de las rutas relativas está medida en el ADR 0161
(`docs/05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md`):
el compose generado se escribe en `data_root` (`cli.py`), así que sus `./algo`
resuelven contra `/data/agent-platform/`, donde no hay checkout — **ni con clon
ni sin él**. Se exige que conste porque su modo de fallo no avisa donde está la
causa: Docker inventa el destino ausente de un bind como directorio vacío, y lo
que se ve es un Postgres `healthy` sin `pgvector`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

_INSTALLER_README = _REPO_ROOT / "apps" / "installer" / "README.md"
_REFERENCE = _REPO_ROOT / "docs" / "04-reference" / "installation.md"
_HUMAN_TESTS = _REPO_ROOT / "docs" / "03-guides" / "human-tests" / "15-instalador-produccion.md"
_GETTING_STARTED = _REPO_ROOT / "docs" / "02-getting-started" / "01-installation.md"
_RUNBOOK_SCRATCH = _REPO_ROOT / "docs" / "06-runbooks" / "01-installation-from-scratch.md"
_RUNBOOK_PROD = _REPO_ROOT / "docs" / "06-runbooks" / "08-instalacion-produccion.md"

#: Documentos que describen el wizard HTTP a alguien que podría ejecutarlo.
_DOCS_THAT_DESCRIBE_THE_WIZARD = (
    _INSTALLER_README,
    _REFERENCE,
    _HUMAN_TESTS,
    _RUNBOOK_SCRATCH,
)

#: Documentos que llevan a un operador o a un diseñador al camino REAL (el CLI).
#: Todos ellos deben decir que la avería de las rutas relativas existe: es la que
#: impide hoy terminar una instalación en una máquina limpia, y no la arregla
#: clonar el repositorio.
_DOCS_THAT_LEAD_TO_A_REAL_INSTALL = (
    _INSTALLER_README,
    _REFERENCE,
    _RUNBOOK_SCRATCH,
    _RUNBOOK_PROD,
)

_SIMULATION = re.compile(r"simulaci[óo]n|simulation|simulad[oa]", re.IGNORECASE)
#: El corpus es bilingüe (los README son canónicos en inglés,
#: `docs/03-guides/bilingual-docs.md`), así que la avería se nombra en los dos
#: idiomas o la guarda sólo miraría media biblioteca.
_RELATIVE_PATH_BREAKAGE = re.compile(r"rutas?\s+relativas?|relative\s+paths?", re.IGNORECASE)
_ADR_0161 = re.compile(r"\b0161\b")

#: Frases que ningún documento puede sostener mientras el wizard sea un stub,
#: con el motivo por el que cada una es falsa.
_FORBIDDEN_CLAIMS: tuple[tuple[str, str], ...] = (
    (
        r"provisions?\s+a\s+real\s+stack",
        "el wizard NO aprovisiona: su StepExecutor por defecto es FakeStepExecutor "
        "(main.py). Era la frase de apps/installer/README.md:51 — «the installer "
        "actually provisions a real stack». Se prohíbe también sin el «actually», "
        "porque la corrección no puede consistir en quitar un adverbio; y quien "
        "cuente la historia de esta errata tiene que parafrasearla, no reproducirla",
    ),
    (
        r"wizard[^.]{0,120}corren\s+la\s+\*{0,2}misma\*{0,2}\s+orquestaci[óo]n",
        "wizard y CLI NO corren la misma orquestación: el CLI cablea los bindings "
        "reales y el wizard se queda en los seams de simulación",
    ),
    (
        r"install\.sh`?\s*#?\s*modo\s+wizard\s*\(sirve",
        "`./scripts/install.sh` sin --config no sirve ninguna UI: sale con USAGE (1). "
        "Era la línea 49 de la guía de tests humanos",
    ),
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _doc_id(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


# --- anclas en el código: sin ellas, todo lo de abajo pasaría vacío ---------


def test_the_wizard_still_defaults_to_a_simulated_executor() -> None:
    """El hecho del que dependen todas las afirmaciones documentales de abajo.

    Si esto falla es una **buena** noticia: significa que el wizard ya aprovisiona
    de verdad. Pero entonces los documentos que esta guarda obliga a marcar como
    SIMULACIÓN pasan a mentir en la dirección contraria, y hay que corregirlos en
    el mismo commit que cablea el ejecutor real.
    """
    from installer_backend.install import FakeStepExecutor
    from installer_backend.main import get_step_executor

    executor = get_step_executor()
    assert isinstance(executor, FakeStepExecutor), (
        "el wizard HTTP ya NO usa FakeStepExecutor "
        f"(ahora es {type(executor).__name__}): revisa "
        f"{', '.join(_doc_id(p) for p in _DOCS_THAT_DESCRIBE_THE_WIZARD)} — "
        "los avisos de SIMULACIÓN que esta guarda exige se han quedado obsoletos "
        "y ahora son ellos los que mienten"
    )


def test_a_bare_install_sh_cannot_open_a_wizard() -> None:
    """`--config` es obligatorio: la invocación desnuda sale con USAGE (1)."""
    from installer_backend.cli import build_parser

    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["install"])

    assert excinfo.value.code != 0, (
        "`install` sin --config ya no es un error de uso: si el CLI aprendió a "
        "arrancar el wizard por su cuenta, la guarda de abajo sobra"
    )


# --- mitad negativa: ninguna promesa que el código no cumpla ---------------


@pytest.mark.parametrize("doc", _DOCS_THAT_DESCRIBE_THE_WIZARD, ids=_doc_id)
def test_no_doc_claims_the_wizard_provisions_a_real_stack(doc: Path) -> None:
    text = _read(doc)
    offenders = [
        (pattern, why)
        for pattern, why in _FORBIDDEN_CLAIMS
        if re.search(pattern, text, re.IGNORECASE)
    ]
    assert not offenders, f"{_doc_id(doc)} afirma algo que el instalador no hace:\n" + "\n".join(
        f"  · /{pattern}/ — {why}" for pattern, why in offenders
    )


def test_no_doc_shows_a_bare_install_sh_as_a_working_invocation() -> None:
    """Una línea de comando `./scripts/install.sh` sin `--config` no arranca nada.

    Sólo se miran **líneas de comando** (las que empiezan por la invocación), no
    las menciones en prosa: nombrar el script para decir cuál es el camino real es
    correcto; enseñarlo como algo que se teclea, no.
    """
    bare = re.compile(r"^\s*(?:\$\s*)?\./scripts/install\.sh\s*(?:#.*)?$")
    offenders: list[str] = []
    for doc in (*_DOCS_THAT_DESCRIBE_THE_WIZARD, _RUNBOOK_PROD, _GETTING_STARTED):
        for number, line in enumerate(_read(doc).splitlines(), start=1):
            if bare.match(line):
                offenders.append(f"{_doc_id(doc)}:{number}: {line.strip()}")
    assert not offenders, (
        "estos documentos enseñan `./scripts/install.sh` sin `--config` como si "
        "arrancase algo; sale con USAGE (1) sin tocar el host:\n  " + "\n  ".join(offenders)
    )


# --- mitad positiva: lo simulado está marcado y la avería está escrita -----


@pytest.mark.parametrize("doc", _DOCS_THAT_DESCRIBE_THE_WIZARD, ids=_doc_id)
def test_every_doc_that_describes_the_wizard_marks_it_as_a_simulation(doc: Path) -> None:
    text = _read(doc)
    if not re.search(r"wizard", text, re.IGNORECASE):
        pytest.fail(
            f"{_doc_id(doc)} ya no menciona el wizard: si se ha retirado a "
            "propósito, sácalo de _DOCS_THAT_DESCRIBE_THE_WIZARD; si no, esta "
            "guarda acaba de pasar vacía"
        )
    assert _SIMULATION.search(text), (
        f"{_doc_id(doc)} describe el wizard HTTP sin marcarlo como SIMULACIÓN. "
        "Su StepExecutor por defecto es FakeStepExecutor: no aprovisiona nada y "
        "las credenciales que revela no son reales"
    )


def test_the_installer_readme_says_the_revealed_credentials_are_not_real() -> None:
    """La consecuencia práctica, no sólo la etiqueta.

    «Es una simulación» se lee como «no está terminado». Lo que hay que poder leer
    es qué pasa si te la crees: que apuntas unas credenciales y unas unseal keys
    que no abren nada.
    """
    text = _read(_INSTALLER_README)
    assert re.search(r"no\s+son\s+reales|not\s+real|falsas|no\s+sirven", text, re.IGNORECASE), (
        "apps/installer/README.md no dice que las credenciales que revela el "
        "wizard no sirven — el aviso de simulación sin su consecuencia se lee "
        "como «aún no está pulido»"
    )


def test_the_installer_readme_names_the_cli_as_the_real_path() -> None:
    text = _read(_INSTALLER_README)
    assert "scripts/install.sh" in text, (
        "apps/installer/README.md desmiente el wizard pero no dice cuál es el "
        "camino que sí existe: dejar al lector sin salida es media corrección"
    )


@pytest.mark.parametrize("doc", _DOCS_THAT_LEAD_TO_A_REAL_INSTALL, ids=_doc_id)
def test_the_relative_path_breakage_is_written_where_it_is_read(doc: Path) -> None:
    text = _read(doc)
    assert _RELATIVE_PATH_BREAKAGE.search(text) and _ADR_0161.search(text), (
        f"{_doc_id(doc)} lleva a alguien al camino real sin decirle que hoy no "
        "termina: el compose generado se escribe en `data_root`, y sus rutas "
        "relativas resuelven contra /data/agent-platform/, donde no hay checkout "
        "(ADR 0161). Clonar el repositorio NO lo arregla, que es justo lo que "
        "nadie deduce solo"
    )


def test_the_readme_does_not_describe_a_landed_repair_as_pending() -> None:
    """La avería tiene que CONSTAR; describirla como abierta cuando ya se cerró
    es la misma mentira en la dirección pesimista.

    `apps/installer/README.md` decía «Even the CLI cannot finish on a clean
    machine today… **A repair is in progress**… No date is promised here». La
    reparación aterrizó: los auxiliares viajan dentro del paquete
    (`installer_backend.stack_assets`), el README raíz lo dice con todas las
    letras y el ADR 0161 responde su pregunta 4 con «Ya hecho». Dos README del
    mismo repo contradiciéndose sobre el estado de la única avería que impedía
    instalar, y quien lea el pesimista presupuesta una reparación ya pagada.

    El ancla es el CÓDIGO, no una fecha: mientras el paquete embarque los
    auxiliares, el README no puede decir que la reparación está en curso. El día
    que alguien los saque, esta guarda se apaga sola y la frase vuelve a ser
    legítima.
    """
    from installer_backend import stack_assets

    assert stack_assets.ALL_ASSETS, (
        "`installer_backend.stack_assets` ya no embarca auxiliares: si la "
        "reparación se ha deshecho, esta guarda sobra — pero compruébalo"
    )

    text = _read(_INSTALLER_README)
    pendiente = re.compile(
        r"repair\s+is\s+in\s+progress|reparaci[óo]n\s+en\s+curso|"
        r"no\s+date\s+is\s+promised|sin\s+fecha\s+prometida",
        re.IGNORECASE,
    )
    match = pendiente.search(text)
    assert match is None, (
        f"{_doc_id(_INSTALLER_README)} describe como pendiente una reparación que "
        f"ya aterrizó ({match.group(0)!r}): el paquete embarca "
        f"{len(stack_assets.ALL_ASSETS)} auxiliares y el compose generado los "
        "monta desde ahí. Cuéntalo en pasado, conservando la mención a la avería "
        "(la guarda de arriba la sigue exigiendo: constar no es seguir abierta)"
    )


def test_getting_started_points_at_the_real_install_path() -> None:
    """La guía de arranque no puede despachar producción con «ya llegará».

    Decía que la producción llega «con el instalador de Fase 15» sin nombrar cuál
    de los dos caminos del instalador existe. Quien lo leyera iba al wizard, que
    es el que no aprovisiona.
    """
    text = _read(_GETTING_STARTED)
    assert "scripts/install.sh" in text, (
        "docs/02-getting-started/01-installation.md no nombra el camino REAL de "
        "instalación (el CLI)"
    )
    assert "01-installation-from-scratch.md" in text, (
        "docs/02-getting-started/01-installation.md no enlaza el runbook del "
        "instalador, que es donde está el estado real de cada camino"
    )
