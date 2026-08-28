"""La séptima imagen también se pinea, y también tiene quien la refresque.

## El hueco, medido el 2026-08-28

`tests/unit/test_supply_chain_config.py` exige `@sha256:` en todo `FROM` externo
—y lo hace bien— pero descubre los Dockerfiles con
``DOCKER_DIR.rglob("Dockerfile*")``: **sólo bajo `docker/`**. La imagen del
instalador vive en `apps/installer/backend/`, así que su
``FROM python:3.12-slim`` a secas estaba fuera del alcance de la guarda y el test
pasaba en verde.

No es una imagen cualquiera: es la ÚNICA que un operador se descarga en el camino
sin clon (ADR 0161), y el artefacto que la trae —
`docker/bootstrap/docker-compose.generate.yml` — se sella por digest justamente
para que «lo que se baja sea lo que se publicó». Con la base por tag mutable, dos
builds de la MISMA revisión de git producen imágenes distintas según el día, así
que cuando Trivy se ponga rojo en el job `installer` nadie podrá decir si la CVE
entró por la base o por una dependencia, ni bisecarlo. Ya pasó una vez con esta
familia de bases: CVE-2026-53615 el 2026-08-18, que es lo que motivó la capa de
`apt-get upgrade` del propio Dockerfile.

## Por qué el pin y el refresco se comprueban JUNTOS

La cabecera de `.github/dependabot.yml` lo dice con todas las letras: «un digest
pineado SIN mecanismo de refresco es PEOR que un tag flotante» — congela sus CVE
para siempre. La entrada `docker` de Dependabot excluía `apps/` por comentario
explícito, así que pinear sin tocarla habría cambiado un defecto por otro peor.
Las dos mitades se exigen aquí a la vez, y por eso están en el mismo fichero: una
sin la otra es una regresión disfrazada de mejora.

`apps/admin-panel/Dockerfile` tenía exactamente la misma forma —digest pineado,
cero vía de refresco— y entra en la misma entrada de Dependabot, aunque su
Dockerfile no se vigile desde aquí: esta guarda cubre lo que el instalador
publica.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALLER_DIR = _REPO_ROOT / "apps" / "installer"
_DEPENDABOT = _REPO_ROOT / ".github" / "dependabot.yml"

_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


#: `installer_backend/stack_assets/` lleva COPIAS byte a byte de `docker/`
#: (egress-proxy, registry-proxy) para que el compose generado tenga sus
#: auxiliares en un host sin checkout. Su fuente de verdad está en `docker/`, que
#: ya cubren la guarda hermana de `test_supply_chain_config.py` y la entrada
#: `docker` de Dependabot, y `test_installer_ships_stack_assets.py` vigila que la
#: copia no derive. Exigirles aquí su propia entrada de Dependabot crearía una
#: SEGUNDA fuente de verdad para el mismo digest: Dependabot bumpearía la copia,
#: la guarda de deriva se pondría roja, y el arreglo sería deshacer el bump.
_MIRRORED = "stack_assets"


def _installer_dockerfiles() -> list[Path]:
    return sorted(
        path
        for path in _INSTALLER_DIR.rglob("Dockerfile*")
        if _MIRRORED not in path.relative_to(_INSTALLER_DIR).parts
    )


def _from_refs(dockerfile: Path) -> list[tuple[int, str]]:
    """``(lineno, image_ref)`` de cada ``FROM`` EXTERNO del Dockerfile.

    Se excluyen los que referencian una etapa previa del mismo fichero
    (multi-stage) y los que resuelven un ``ARG`` (``FROM ${BASE_IMAGE}``): ni unos
    ni otros son descargas de un registry, así que no llevan digest. Misma
    lectura que la guarda hermana de `docker/`, a propósito: dos formas distintas
    de leer un `FROM` acabarían discrepando sobre qué hay que pinear.
    """
    stages: set[str] = set()
    out: list[tuple[int, str]] = []
    for lineno, raw in enumerate(dockerfile.read_text(encoding="utf-8").splitlines(), start=1):
        match = re.match(
            r"^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?\s*$", raw.strip(), re.I
        )
        if not match:
            continue
        ref, alias = match.group(1), match.group(2)
        if alias:
            stages.add(alias.lower())
        if ref.lower() in stages or ref.startswith("$"):
            continue
        out.append((lineno, ref))
    return out


def _docker_dependabot_directories() -> list[str]:
    data: dict[str, Any] = yaml.safe_load(_DEPENDABOT.read_text(encoding="utf-8"))
    dirs: list[str] = []
    for update in data.get("updates") or []:
        if update.get("package-ecosystem") != "docker":
            continue
        dirs.extend(update.get("directories") or [])
        if isinstance(update.get("directory"), str):
            dirs.append(update["directory"])
    return dirs


def _rel(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


# ---------------------------------------------------------------------------
# Mitad 1: el pin
# ---------------------------------------------------------------------------
def test_installer_image_bases_are_pinned_by_digest() -> None:
    dockerfiles = _installer_dockerfiles()
    assert dockerfiles, (
        f"no hay ningún Dockerfile bajo {_rel(_INSTALLER_DIR)}: si el instalador "
        "dejó de empaquetarse, retira esta guarda a propósito en vez de dejarla "
        "pasando vacía"
    )
    seen = 0
    offenders: list[str] = []
    for dockerfile in dockerfiles:
        for lineno, ref in _from_refs(dockerfile):
            seen += 1
            if not _DIGEST.search(ref):
                offenders.append(f"{_rel(dockerfile)}:{lineno}: FROM {ref}")
    assert seen >= 4, (
        f"la guarda dejó de encontrar los FROM del instalador (vio {seen}): son "
        "uno del backend y tres de las etapas del wizard"
    )
    assert not offenders, (
        "FROM sin digest en la imagen que el operador se descarga (ADR 0161):\n"
        + "\n".join(offenders)
        + "\nDos builds de la misma revisión darían imágenes distintas, y un "
        "Trivy rojo no se podría bisecar entre la base y las dependencias."
    )


def test_installer_pinned_bases_keep_their_tag_readable() -> None:
    """``FROM python:3.12-slim@sha256:…``: el tag va DENTRO de la referencia.

    Un ``FROM python@sha256:…`` sin tag es inauditable —nadie sabe qué versión
    corre— y Dependabot no puede proponer la siguiente.
    """
    seen = 0
    offenders: list[str] = []
    for dockerfile in _installer_dockerfiles():
        for lineno, ref in _from_refs(dockerfile):
            if not _DIGEST.search(ref):
                continue
            seen += 1
            name = ref.split("@", 1)[0]
            if ":" not in name.rsplit("/", 1)[-1]:
                offenders.append(f"{_rel(dockerfile)}:{lineno}: FROM {ref} (sin tag legible)")
    assert seen >= 4, f"la guarda dejó de encontrar FROM con digest (vio {seen})"
    assert not offenders, "FROM con digest pero sin tag:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# Mitad 2: la vía de refresco (ADR 0148, condición 1)
# ---------------------------------------------------------------------------
def test_every_pinned_installer_dockerfile_has_a_dependabot_vehicle() -> None:
    """Sin refresco, el digest congela sus CVE — peor que el tag que sustituye.

    Lo dice la propia cabecera de `dependabot.yml`, y es la condición 1 del ADR
    0148. Aquí se comprueba que el directorio de cada Dockerfile pineado está
    cubierto por el ecosistema `docker` de Dependabot.
    """
    cubiertos = _docker_dependabot_directories()
    assert cubiertos, "la entrada `docker` de dependabot.yml no declara directorios"

    huerfanos: list[str] = []
    for dockerfile in _installer_dockerfiles():
        directorio = "/" + _rel(dockerfile.parent)
        if not any(fnmatch(directorio, patron) for patron in cubiertos):
            huerfanos.append(directorio)
    assert not huerfanos, (
        "estos directorios llevan un Dockerfile pineado por digest y NINGUNA "
        f"entrada `docker` de dependabot.yml los cubre: {sorted(set(huerfanos))}.\n"
        "Un digest sin vía de refresco es PEOR que un tag flotante (ADR 0148, "
        "condición 1): congela sus CVE para siempre y nadie abre el PR que las "
        "cierra."
    )
