"""El fichero de exclusiones de markdownlint no puede tapar documentación propia.

`.markdownlintignore` nació para un problema real y acotado: `docs/manuals/`
instala dependencias de npm bajo `docs/manuals/node_modules/`, y el glob del gate
(`docs/**/*.md`) entraba en los README de playwright y de tslib. En CI no se veía
—checkout limpio y ese job no instala nada—, pero en local sepultaba los errores
propios entre decenas de ajenos, y un lint que no se puede leer no se usa.

El riesgo del remedio es evidente: un fichero de exclusiones es la vía más barata
de poner un gate en verde sin arreglar nada. Esta guarda lo acota a código de
terceros, y comprueba además que los documentos que un desconocido lee primero
—README, CHANGELOG y el corpus de `docs/`— siguen dentro del alcance.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
_IGNORE = _RAIZ / ".markdownlintignore"

#: Lo único que se admite excluir: dependencias de terceros, ya en .gitignore.
_PERMITIDO = {"node_modules/", "node_modules"}

#: Documentos cuyo lint es el motivo de que el gate exista.
_TIENEN_QUE_SEGUIR_CUBIERTOS = (
    "README.md",
    "README.es.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "docs/04-reference/cadena-suministro.md",
    "docs/05-architecture-decisions/0148-distribucion-imagenes-runtime-por-digest.md",
    "docs/roadmap/prod-01-despliegue-ejecutable.md",
)


def _patrones() -> list[str]:
    if not _IGNORE.exists():
        return []
    return [
        linea.strip()
        for linea in _IGNORE.read_text(encoding="utf-8").splitlines()
        if linea.strip() and not linea.lstrip().startswith("#")
    ]


def test_solo_se_excluye_codigo_de_terceros() -> None:
    intrusos = [p for p in _patrones() if p not in _PERMITIDO]
    assert not intrusos, (
        "`.markdownlintignore` sólo puede excluir dependencias de terceros; estos "
        f"patrones excluyen algo más: {intrusos}"
    )


def test_los_documentos_de_entrada_siguen_dentro_del_gate() -> None:
    patrones = _patrones()
    tapados = [
        doc
        for doc in _TIENEN_QUE_SEGUIR_CUBIERTOS
        if any(fnmatch(doc, p) or fnmatch(doc, p.rstrip("/") + "/*") for p in patrones)
    ]
    assert not tapados, f"el ignore de markdownlint tapa documentación propia: {tapados}"


def test_los_documentos_de_entrada_existen() -> None:
    """Non-vacuidad: si se renombran, la guarda de arriba pasaría sobre fantasmas."""
    faltan = [d for d in _TIENEN_QUE_SEGUIR_CUBIERTOS if not (_RAIZ / d).is_file()]
    assert not faltan, f"esta guarda apunta a documentos que ya no existen: {faltan}"
