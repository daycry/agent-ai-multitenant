"""La plataforma declara UN número, y esta guarda lo deriva del árbol.

[ADR 0160](../../docs/05-architecture-decisions/0160-versionado-de-la-plataforma.md),
firmado por el operador el 2026-08-27, elige la **opción A**: versión única del
monorepo, SDK aparte. Las quince distribuciones de plataforma comparten número
—un tag `vX.Y.Z` significa «este stack, entero, tal como se publicó ese día»— y
`packages/sdk-python` / `packages/sdk-typescript` conservan el suyo porque los
consume gente de fuera y su compatibilidad la gobierna el ADR 0037, no el número
del stack.

**Por qué existe este fichero y no basta con haber hecho el bump.** El bump es un
estado, no una propiedad: se deshace solo. Basta con que alguien añada un paquete
nuevo —que nace en `0.0.0`, porque es lo que escribe cualquier plantilla— para
que el monorepo vuelva a tener dos números sin que nada avise. El día del corte
había **catorce** distribuciones en `0.0.0`, una en `1.0.0` por inercia y dos SDK
en `0.1.0` por decisión; nadie lo vio hasta que se contó a mano para escribir el
ADR.

**Y por qué la lista se DERIVA y no se enumera.** Es el modo de fallo que este
repo ya pagó: `apps/watchdog/` entró el 2026-08-02 y los dos sitios que repartían
apps entre familias de build llevaban la lista escrita a mano, así que la app
nueva rompió el job y no se publicó su imagen durante diez días
(`tests/unit/test_app_images_are_built_by_ci.py`, que es el patrón que este
fichero sigue). Una guarda que repitiera la lista a mano repetiría el defecto:
cubriría exactamente las distribuciones que ya estaban bien el día que se
escribió.

**Las dos poblaciones que hay que mirar**, porque la segunda no la ve ningún
barrido de manifiestos:

1. Los manifiestos (`pyproject.toml` / `package.json`) — 19 en el árbol, 17 de
   plataforma, 15 en el número común.
2. Los **cinco sitios con la versión escrita a mano en el código**. Cuatro se
   mueven con la plataforma; el quinto es el `__version__` público del SDK y no
   se toca. El peor de los cuatro es el del instalador: se sirve por HTTP en su
   `/healthz`, así que un `0.0.0` ahí es un dato que alguien usará para
   diagnosticar una instalación etiquetada `v1.0.0` y le dará la respuesta
   contraria. Estos cinco también se descubren barriendo el árbol —`__version__`
   y el `version=` literal de una app FastAPI—, no listándolos.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Directorios que NO son parte del árbol fuente: dependencias instaladas,
#: cachés y salidas de build. Podar aquí no es una lista de distribuciones
#: escrita a mano —eso es justo lo que esta guarda evita—, sino la frontera
#: entre «lo que este repo escribe» y «lo que otras herramientas dejan». Sin
#: podar, un `node_modules/` traería cientos de `package.json` de terceros.
_PRUNED_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)

_MANIFEST_NAMES = frozenset({"pyproject.toml", "package.json"})

# ---------------------------------------------------------------------------
# Las exclusiones, con su motivo AL LADO.
#
# Una exclusión sin motivo escrito es un agujero: nadie sabe si sigue siendo
# cierta, así que sobrevive a refactors que la invalidan o se borra en el
# primero que la toca. El ADR 0160 §«Lo que hay hoy, medido» las nombra por eso.
# ---------------------------------------------------------------------------
_NOT_PLATFORM_DISTRIBUTIONS: dict[str, str] = {
    "docs/manuals/package.json": (
        "Toolchain de construcción de los manuales en PDF. No se publica ni se "
        "despliega nada de ahí: su número no describe ningún artefacto del stack."
    ),
    "apps/admin-panel/vendor/miniverse-core/package.json": (
        "Dependencia VENDORIZADA de terceros. Su número lo pone su autor. "
        "Pisárselo con el de la plataforma es la forma limpia de romper una "
        "actualización futura de ese paquete, y marcarlo como «desincronizado» "
        "sería llamar defecto a algo que nunca estuvo sincronizado."
    ),
}

# ---------------------------------------------------------------------------
# Los dos SDK: son de plataforma, pero NO entran en el número común.
#
# Decisión 2 del ADR 0160. Los consume gente de fuera y su compatibilidad la
# gobierna el ADR 0037 (versionado del path de la API). Atarlos al stack
# obligaría a publicar un SDK nuevo cada vez que cambie el `watchdog`, que no le
# dice nada a quien lo consume.
# ---------------------------------------------------------------------------
_SDK_DISTRIBUTIONS: dict[str, str] = {
    "packages/sdk-python/pyproject.toml": "SDK público (ADR 0037), ciclo propio.",
    "packages/sdk-typescript/package.json": "SDK público (ADR 0037), ciclo propio.",
}

#: Suelo del descubrimiento. Si el barrido deja de ver distribuciones, todo lo
#: de abajo pasaría EN VACÍO diciendo que el monorepo es coherente
#: (`docs/03-guides/verificar-antes-de-implementar.md` §4). Eran 15 el día del
#: ADR; el suelo no sube solo, pero un barrido roto cae muy por debajo.
_MINIMUM_PLATFORM_DISTRIBUTIONS = 15

#: Ídem para los hardcodeos del código: eran 5 el 2026-08-27.
_MINIMUM_HARDCODED_SITES = 5

_SEMVER = re.compile(r"^\d+\.\d+\.\d+")

#: `__version__ = "1.2.3"` en cualquier módulo del árbol.
_DUNDER_VERSION = re.compile(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", re.M)

#: `FastAPI(..., version="1.2.3", ...)` — sólo literales. Un `version=__version__`
#: (el del instalador) no casa a propósito: ese sitio ya se cubre por su
#: `__version__`, y contarlo dos veces sería contar una cadena de propagación
#: como si fuera una fuente.
_FASTAPI_VERSION = re.compile(
    r"FastAPI\((?:[^()]|\([^()]*\))*?\bversion\s*=\s*[\"']([^\"']+)[\"']", re.S
)


def _source_files() -> list[Path]:
    """Todos los ficheros del árbol fuente, podando lo que no escribimos."""
    found: list[Path] = []
    stack = [_REPO_ROOT]
    while stack:
        for entry in sorted(stack.pop().iterdir()):
            if entry.is_dir():
                if entry.name not in _PRUNED_DIRS:
                    stack.append(entry)
            else:
                found.append(entry)
    return found


_SOURCE_FILES = _source_files()


def _rel(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def _manifests() -> dict[str, Path]:
    """Los manifiestos con número de versión, DERIVADOS del árbol."""
    return {_rel(p): p for p in _SOURCE_FILES if p.name in _MANIFEST_NAMES}


def _declared_version(manifest: Path) -> str:
    if manifest.name == "pyproject.toml":
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        project = data.get("project")
        return str(project.get("version", "")) if isinstance(project, dict) else ""
    return str(json.loads(manifest.read_text(encoding="utf-8")).get("version", ""))


_MANIFESTS = _manifests()

#: Las que se mueven juntas: todo lo descubierto menos las dos que no son
#: distribuciones de plataforma y menos los dos SDK.
_PLATFORM = {
    rel: path
    for rel, path in _MANIFESTS.items()
    if rel not in _NOT_PLATFORM_DISTRIBUTIONS and rel not in _SDK_DISTRIBUTIONS
}


def _owning_manifest(source: Path) -> str | None:
    """El manifiesto de la distribución a la que pertenece un fichero.

    Se resuelve subiendo por el árbol hasta el primer directorio que tenga
    manifiesto — así el mapeo también sale del árbol y no de una tabla.
    """
    for parent in source.parents:
        for name in ("pyproject.toml", "package.json"):
            rel = _rel(parent / name)
            if rel in _MANIFESTS:
                return rel
        if parent == _REPO_ROOT:
            break
    return None


def _hardcoded_version_sites() -> list[tuple[str, str, str | None]]:
    """Versiones escritas a mano en el código: `(fichero, valor, manifiesto)`.

    Los tests de este repo y sus fixtures quedan fuera: un `version="1.0.0"` en
    un dato de prueba del marketplace no dice nada de la versión del stack.
    """
    sites: list[tuple[str, str, str | None]] = []
    for path in _SOURCE_FILES:
        if path.suffix != ".py":
            continue
        rel = _rel(path)
        if rel.startswith("tests/") or "/tests/" in rel or path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _DUNDER_VERSION.finditer(text):
            sites.append((rel, match.group(1), _owning_manifest(path)))
        for match in _FASTAPI_VERSION.finditer(text):
            sites.append((rel, match.group(1), _owning_manifest(path)))
    return sorted(sites)


_HARDCODED = _hardcoded_version_sites()


# ---------------------------------------------------------------------------
# (0) La guarda tiene que poder fallar: que vea algo antes de afirmar nada.
# ---------------------------------------------------------------------------
def test_the_guard_still_sees_the_versioned_distributions() -> None:
    assert len(_PLATFORM) >= _MINIMUM_PLATFORM_DISTRIBUTIONS, (
        f"solo se han descubierto {len(_PLATFORM)} distribuciones de plataforma "
        f"({sorted(_PLATFORM)}). O el barrido está roto, o la poda se comió parte "
        "del árbol: en cualquiera de los dos casos el resto del fichero pasaría "
        "en vacío diciendo que el monorepo es coherente."
    )


def test_the_guard_still_sees_the_hardcoded_versions() -> None:
    assert len(_HARDCODED) >= _MINIMUM_HARDCODED_SITES, (
        f"solo se han descubierto {len(_HARDCODED)} versiones escritas a mano en "
        f"el código ({[s[0] for s in _HARDCODED]}). Eran 5 el 2026-08-27; si el "
        "barrido deja de verlas, un servicio puede volver a servir un `0.0.0` "
        "por HTTP sin que nada se ponga en rojo."
    )


# ---------------------------------------------------------------------------
# (1) Las exclusiones siguen apuntando a algo.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "excluded", sorted(_NOT_PLATFORM_DISTRIBUTIONS) + sorted(_SDK_DISTRIBUTIONS)
)
def test_every_documented_exclusion_still_matches_a_real_manifest(excluded: str) -> None:
    """Una exclusión que ya no señala nada deja de excluir sin que se note.

    Si el fichero se mueve, la exclusión sigue escrita pero no cubre nada, y su
    distribución entra en el número común por la puerta de atrás. Si desaparece,
    la exclusión es texto muerto que el siguiente lector tomará por vigente.
    """
    reason = {**_NOT_PLATFORM_DISTRIBUTIONS, **_SDK_DISTRIBUTIONS}[excluded]
    assert excluded in _MANIFESTS, (
        f"la exclusión `{excluded}` ya no corresponde a ningún manifiesto del "
        f"árbol. Motivo con el que se escribió: {reason} Si el fichero se movió, "
        "actualiza la ruta; si desapareció, borra la entrada — pero no la dejes."
    )


# ---------------------------------------------------------------------------
# (2) El número común.
# ---------------------------------------------------------------------------
def test_every_platform_distribution_declares_the_same_version() -> None:
    declared = {rel: _declared_version(path) for rel, path in sorted(_PLATFORM.items())}
    by_version: dict[str, list[str]] = {}
    for rel, version in declared.items():
        by_version.setdefault(version, []).append(rel)
    reparto = "\n".join(
        f"  {version or '(sin versión)'}: {sorted(paths)}"
        for version, paths in sorted(by_version.items())
    )
    assert len(by_version) == 1, (
        "las distribuciones de plataforma declaran versiones distintas, y el "
        "ADR 0160 (opción A) dice que se mueven juntas: un tag `vX.Y.Z` es «este "
        f"stack, entero». Reparto actual:\n{reparto}\n"
        "Si lo que has añadido es una distribución nueva, dale el número común. "
        "Si NO es de plataforma, añádela arriba con su motivo escrito."
    )
    (common,) = by_version
    assert _SEMVER.match(common), (
        f"la versión común del monorepo es `{common}`, que no tiene forma de "
        "semver `X.Y.Z`. El default del instalador genera `${PLATFORM_IMAGE_TAG:-vX.Y.Z}` "
        "y `release-images.yml` dispara con `tags: ['v*']`: un número que no sea "
        "semver no produce un tag que esa maquinaria pueda publicar."
    )


def _platform_version() -> str:
    versions = {_declared_version(path) for path in _PLATFORM.values()}
    assert len(versions) == 1, "sin número común no hay nada que comparar"
    return versions.pop()


# ---------------------------------------------------------------------------
# (3) Los SDK, fuera del número común A PROPÓSITO.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sdk", sorted(_SDK_DISTRIBUTIONS))
def test_the_two_sdks_stay_out_of_the_common_number(sdk: str) -> None:
    """El fallo real que esto ataja es un barrido: `sed -i s/0.0.0/1.0.0/`.

    Quien haga el próximo bump verá 19 manifiestos y la tentación de tocarlos
    todos de una pasada. Los dos SDK NO se tocan (decisión 2 del ADR 0160): los
    consume gente de fuera, y publicarles versión nueva porque cambió el
    `watchdog` no le dice nada a quien los usa.

    Si algún día un SDK llega por su propio ciclo al mismo número que la
    plataforma, esto se pone en rojo por coincidencia. El arreglo entonces es
    una línea aquí explicando esa coincidencia — nunca fundir las dos series,
    que es lo que la decisión 2 prohíbe.
    """
    assert sdk not in _PLATFORM, (
        f"`{sdk}` ha entrado en el conjunto que comparte número. El ADR 0160 "
        "decisión 2 lo deja fuera: su compatibilidad la gobierna el ADR 0037."
    )
    sdk_version = _declared_version(_MANIFESTS[sdk])
    assert sdk_version != _platform_version(), (
        f"`{sdk}` declara `{sdk_version}`, que es exactamente el número común de "
        "la plataforma. O se le ha aplicado el bump del stack por barrido —lo "
        "que el ADR 0160 decisión 2 prohíbe— o ha coincidido por su propio "
        "ciclo, y entonces esta guarda necesita una nota que lo diga."
    )


# ---------------------------------------------------------------------------
# (4) Los hardcodeos del código, que ningún barrido de manifiestos ve.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("source", "version", "manifest"),
    [site for site in _HARDCODED if site[2] in _PLATFORM],
    ids=[site[0] for site in _HARDCODED if site[2] in _PLATFORM],
)
def test_hardcoded_platform_versions_match_the_common_number(
    source: str, version: str, manifest: str | None
) -> None:
    """El caso que duele: `/healthz` del instalador respondiendo `0.0.0`.

    Ese endpoint se sirve por HTTP y es lo que alguien mira para diagnosticar.
    En un stack etiquetado `v1.0.0`, un `"version": "0.0.0"` no es un detalle
    cosmético: es un dato que da la respuesta contraria a la pregunta que se le
    hace. Lo mismo, más callado, con el `version=` de las apps FastAPI, que
    acaba publicado en su `/openapi.json`.
    """
    assert version == _platform_version(), (
        f"`{source}` escribe la versión a mano y dice `{version}`, mientras que "
        f"`{manifest}` y el resto de la plataforma dicen `{_platform_version()}`. "
        "Un barrido de `pyproject.toml`/`package.json` no encuentra este sitio: "
        "por eso queda mintiendo, y por eso lo comprueba esta guarda."
    )


@pytest.mark.parametrize(
    ("source", "version", "manifest"),
    [site for site in _HARDCODED if site[2] in _SDK_DISTRIBUTIONS],
    ids=[site[0] for site in _HARDCODED if site[2] in _SDK_DISTRIBUTIONS],
)
def test_hardcoded_sdk_versions_follow_their_own_manifest(
    source: str, version: str, manifest: str | None
) -> None:
    """El `__version__` público del SDK sigue a SU pyproject, no al stack.

    Es el quinto hardcodeo del ADR 0160 y el único que no se mueve con la
    plataforma. Que coincida con su propio manifiesto sí importa: es el número
    que un consumidor lee con `sdk.__version__` para reportar un bug.
    """
    assert manifest is not None
    assert version == _declared_version(_MANIFESTS[manifest]), (
        f"`{source}` dice `{version}` y su manifiesto `{manifest}` dice "
        f"`{_declared_version(_MANIFESTS[manifest])}`. El SDK tiene ciclo propio "
        "(ADR 0037), pero dentro de ese ciclo sus dos números son el mismo."
    )
