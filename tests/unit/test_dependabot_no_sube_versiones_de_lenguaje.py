"""Ningún runtime-template puede cambiar de versión de lenguaje sin decisión humana.

## El defecto que fija este fichero

El grupo ``docker-bases`` de ``.github/dependabot.yml`` declaraba
``update-types: [minor, patch]``, y aun así el PR #130 proponía:

| Imagen                         | Cambio                        |
| ------------------------------ | ----------------------------- |
| ``python:3.12-slim``           | → ``3.14-slim`` (5 Dockerfiles) |
| ``php:8.3-cli``                | → ``8.5-cli``                 |
| ``maven:3.9-eclipse-temurin-21`` | → ``maven:3-...-26``        |
| ``golang:1.26``, ``rust:1.97`` | → ``1.27``, ``1.98``          |

**No es que el filtro fallara.** Para dependabot, el tag ``python:3.12`` tiene
major=3 y minor=12, así que subir a ``3.14`` es un update *minor* de manual. Para
el lenguaje son dos releases de features. La guarda existía, estaba configurada,
y no protegía lo que todo el mundo creía, porque la noción de «minor» del tag no
es la del lenguaje. **Ese desajuste es el defecto, no el bump.**

Lo concreto que se rompía: ``CLAUDE.md`` fija Python 3.12; el tag de Maven pasaba
de ``3.9`` a ``3``, que AFLOJA un pin —prohibido expresamente en este repo— y de
Java 21 LTS a 26; y ``php:8.3 → 8.5`` contradecía el comentario escrito justo
encima del ``FROM`` («La versión de PHP no cambia — sube el snapshot de Debian»).

## Lo que se comprueba, y por qué así

La lista de ``ignore`` se contrasta contra **el árbol**, no contra otra lista
escrita a mano: se leen los ``FROM`` reales de ``docker/agent-runtimes/*`` y se
exige que cada imagen de lenguaje esté cubierta. Una lista contra otra lista sólo
mueve el problema de sitio; el día que alguien añada un runtime de Elixir, esto
falla y le dice qué falta.

Lo que el ``ignore`` NO bloquea —y es justo para lo que existe el grupo— son los
**refrescos de digest** del mismo tag: dependabot los trata aparte de los updates
semver, así que las CVE de la base siguen llegando.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_RAIZ = Path(__file__).resolve().parents[2]
_DEPENDABOT = _RAIZ / ".github" / "dependabot.yml"
_RUNTIMES = _RAIZ / "docker" / "agent-runtimes"

_BLOQUEADOS = frozenset({"version-update:semver-minor", "version-update:semver-major"})

#: Imágenes de `agent-runtimes` que NO son un runtime de lenguaje, con su motivo.
#: Cada exclusión se escribe aquí, y no se deduce del nombre, porque «parece una
#: herramienta» no es una propiedad comprobable y sí lo es «alguien lo justificó».
_NO_SON_LENGUAJE: dict[str, str] = {
    "alpine": (
        "Distribución base, no un runtime de lenguaje: no ejecuta código del "
        "usuario en un lenguaje versionado. Su minor SÍ debe entrar, que es "
        "como llegan los parches de la base."
    ),
    "composer": (
        "Gestor de dependencias de PHP, usado en una etapa de build para copiar "
        "el binario. La versión de PHP la fija la imagen `php:`, no ésta, y su "
        "tag ya está pineado al major (`2`)."
    ),
    "mcr.microsoft.com/playwright": (
        "Imagen de navegadores, no de lenguaje. Su versión la marca Playwright, "
        "y va pineada a la release exacta (`v1.62.1-noble`), no a un rango."
    ),
    "mcr.microsoft.com/playwright/python": (
        "Ídem: lo que versiona el tag es Playwright, no el Python que lleva "
        "dentro, así que subir su minor no cambia el lenguaje del usuario."
    ),
}


def _imagenes_de_los_runtimes() -> set[str]:
    """Nombres de imagen de todos los ``FROM`` bajo ``docker/agent-runtimes/*``."""
    fuera: set[str] = set()
    for dockerfile in sorted(_RUNTIMES.glob("*/Dockerfile")):
        for linea in dockerfile.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^FROM\s+(\S+)", linea)
            if not m:
                continue
            ref = m.group(1).split("@")[0]
            # `host/ns/img:tag` -> se corta el tag sólo si el ÚLTIMO segmento lo trae.
            ultimo = ref.rsplit("/", 1)[-1]
            fuera.add(ref.rsplit(":", 1)[0] if ":" in ultimo else ref)
    return fuera


def _bloque_docker() -> dict[str, object]:
    datos = yaml.safe_load(_DEPENDABOT.read_text(encoding="utf-8"))
    for entrada in datos["updates"]:
        if entrada.get("package-ecosystem") == "docker" and "directories" in entrada:
            return dict(entrada)
    raise AssertionError("no se encontró el bloque `docker` con `directories` en dependabot.yml")


def _ignorados() -> dict[str, set[str]]:
    reglas = _bloque_docker().get("ignore", [])
    assert isinstance(reglas, list)
    return {r["dependency-name"]: set(r.get("update-types", [])) for r in reglas}


def test_la_lectura_del_arbol_encuentra_runtimes() -> None:
    """Si el glob dejara de casar, todo lo de abajo pasaría sobre un conjunto vacío."""
    imagenes = _imagenes_de_los_runtimes()
    assert len(imagenes) >= 10, (
        f"sólo se encontraron {sorted(imagenes)} bajo docker/agent-runtimes/*: "
        "probablemente cambió la disposición y esta comprobación dejó de mirar"
    )


def test_todo_runtime_de_lenguaje_esta_protegido() -> None:
    """La lista de ``ignore`` se contrasta contra el árbol, no contra otra lista."""
    ignorados = _ignorados()
    lenguajes = _imagenes_de_los_runtimes() - set(_NO_SON_LENGUAJE)

    sin_proteger = sorted(img for img in lenguajes if img not in ignorados)
    assert not sin_proteger, (
        f"imágenes de runtime cuya versión de LENGUAJE puede subir sola: "
        f"{sin_proteger}. O se añaden al `ignore` del bloque docker de "
        "dependabot.yml, o —si no son un runtime de lenguaje— se declaran en "
        "`_NO_SON_LENGUAJE` con el motivo escrito."
    )


@pytest.mark.parametrize("imagen", sorted(_imagenes_de_los_runtimes() - set(_NO_SON_LENGUAJE)))
def test_la_proteccion_cubre_minor_y_major(imagen: str) -> None:
    """Bloquear sólo ``major`` no serviría: el caso medido fue un ``minor``.

    ``python:3.12 → 3.14`` es *minor* para dependabot. Una regla que sólo cubriera
    ``semver-major`` dejaría pasar exactamente el bump que motivó todo esto.
    """
    cubiertos = _ignorados().get(imagen, set())
    faltan = sorted(_BLOQUEADOS - cubiertos)
    assert not faltan, (
        f"la regla de {imagen!r} no bloquea {faltan}. El caso que motivó esta "
        "guarda (`python:3.12 -> 3.14`) es un update MINOR para dependabot"
    )


def test_el_refresco_de_digest_sigue_permitido() -> None:
    """La otra mitad: bloquear de más congelaría las CVE de la base.

    Un ``ignore`` sin ``update-types`` bloquea la dependencia ENTERA, refrescos de
    digest incluidos — y la cabecera de ``dependabot.yml`` llama a eso «PEOR que
    un tag flotante». Cada regla tiene que decir QUÉ bloquea.
    """
    for nombre, tipos in _ignorados().items():
        assert tipos, (
            f"la regla de {nombre!r} no declara `update-types`, así que bloquea "
            "la dependencia entera y congela también los refrescos de digest, "
            "que son lo único que trae los parches de seguridad de la base"
        )
        assert not (tipos - _BLOQUEADOS), (
            f"la regla de {nombre!r} bloquea {sorted(tipos - _BLOQUEADOS)}, "
            "además de los updates de versión. Si la intención es congelar más, "
            "hace falta escribir por qué en el propio fichero"
        )


@pytest.mark.parametrize("imagen", sorted(_NO_SON_LENGUAJE))
def test_lo_excluido_sigue_existiendo_y_lleva_motivo(imagen: str) -> None:
    """Una exclusión huérfana es una venda sobre algo que ya no está.

    Si la imagen desapareció del árbol, la entrada sobra y confunde; si el motivo
    es de una línea, no se puede auditar dentro de seis meses.
    """
    assert imagen in _imagenes_de_los_runtimes(), (
        f"{imagen!r} figura como «no es un lenguaje» pero ya no aparece en "
        "ningún runtime-template: la entrada quedó huérfana y hay que borrarla"
    )
    assert len(_NO_SON_LENGUAJE[imagen]) >= 80, (
        f"el motivo de {imagen!r} es demasiado corto para auditarlo después"
    )
