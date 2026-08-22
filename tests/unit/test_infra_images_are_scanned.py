"""Las imágenes de infraestructura tienen UN nombre y alguien las mira.

Hermana de `test_app_images_are_built_by_ci.py`, para la otra familia de
Dockerfiles del repo: los de `docker/*/` que no son runtime templates. Hoy son
los dos tinyproxy (`egress-proxy`, `registry-proxy`) y el sidecar
`whatsapp-neonize`.

**Los dos defectos que motivan este fichero (2026-08-22).**

*Uno: tres actores construían la misma imagen con tres nombres distintos.* El
compose canónico la declaraba con `build:` y sin `image:`, así que Docker la
bautizaba con el prefijo del proyecto (`agentic-platform-egress-proxy`);
`ci.yml` la construía como `agentic-egress-proxy:v1`; y el compose que genera el
instalador repetía la forma del canónico, con el prefijo del proyecto de cada
instalación. Tres nombres para un Dockerfile hacen imposible responder a la
única pregunta que importa cuando algo va mal —«¿la imagen que corre es la que
CI construyó?»— y de hecho en la máquina del operador convivían dos.

*Dos: nadie las escaneaba.* `ci.yml` las construía en un bucle y las tiraba. No
salen en `release-images.yml` (no se publican) ni en la matriz de
`build-runtime-templates.yml` (no son templates), y el comentario que reparte la
cobertura de Trivy entre los tres workflows afirmaba, cinco líneas por debajo
del bucle que las construye, que así no quedaba «ninguna imagen del repo sin
escanear». Era falso, y eso es peor que no haber escrito nada: quien lo leyera
daba la cobertura por cerrada. El `egress-proxy` es la ÚNICA salida a internet
de los agent-runtimes (ADR 0019) — precisamente el contenedor donde corre código
no confiable, Principio Rector 2.

Como en la guarda hermana, la lista se **deriva del árbol**: enumerarla a mano
fue el modo de fallo del `watchdog`, y repetirla a mano aquí lo repetiría.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.unit._compose_yaml import ComposeLoader

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCKER = _REPO_ROOT / "docker"
_CI = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_GENERATOR = (
    _REPO_ROOT
    / "apps"
    / "installer"
    / "backend"
    / "src"
    / "installer_backend"
    / "compose_generator.py"
)

#: Prefijo único de las imágenes que construye este repo, el mismo que ya usan
#: las apps (`agentic-platform/watchdog`, `agentic-platform/api-server`…).
_PREFIX = "agentic-platform/"

#: Suelo del descubrimiento: por debajo, la guarda pasaría en vacío.
_MINIMUM_INFRA = 3


def _infra_images() -> list[str]:
    """Dockerfiles de `docker/*/` que no son runtime templates."""
    return [
        dockerfile.parent.name
        for dockerfile in sorted(_DOCKER.glob("*/Dockerfile"))
        if dockerfile.parent.name != "agent-runtimes"
    ]


def _ci() -> dict[str, Any]:
    return yaml.safe_load(_CI.read_text(encoding="utf-8"))


def _infra_build_script() -> str:
    for step in _ci()["jobs"]["build-images"]["steps"]:
        if str(step.get("name", "")).startswith("Build infra images"):
            return str(step.get("run", ""))
    raise AssertionError("ci.yml:build-images perdió el paso «Build infra images»")


def _scanned_image_refs() -> set[str]:
    """Imágenes que algún paso de Trivy mira en ci.yml."""
    refs: set[str] = set()
    for job in _ci()["jobs"].values():
        for step in job.get("steps") or []:
            if "trivy-action" not in str(step.get("uses", "")):
                continue
            ref = str((step.get("with") or {}).get("image-ref", ""))
            if ref:
                refs.add(ref)
    return refs


def _services_built_from(image: str) -> list[tuple[Path, str, dict[str, Any]]]:
    """Servicios de cualquier compose cuyo `build` apunta a `docker/<image>/`."""
    found: list[tuple[Path, str, dict[str, Any]]] = []
    for path in sorted(_DOCKER.glob("docker-compose*.yml")):
        parsed = yaml.load(path.read_text(encoding="utf-8"), Loader=ComposeLoader) or {}
        for name, service in (parsed.get("services") or {}).items():
            if not isinstance(service, dict):
                continue
            build = service.get("build")
            context = build if isinstance(build, str) else (build or {}).get("context", "")
            if str(context).strip("./") == image:
                found.append((path, str(name), service))
    return found


def _default_de(valor: str) -> str:
    """`${IMAGE_X:-agentic-platform/x:v1}` → `agentic-platform/x:v1`.

    El compose permite sobreescribir la imagen por variable de entorno; lo que
    se contrasta con CI es el valor por defecto, que es el que rige en un host
    que no la define.
    """
    match = re.fullmatch(r"\$\{[A-Za-z0-9_]+:-(?P<default>[^}]+)\}", valor.strip())
    return match.group("default") if match else valor.strip()


_INFRA = _infra_images()


def test_the_guard_still_sees_the_infra_images() -> None:
    assert len(_INFRA) >= _MINIMUM_INFRA, (
        f"solo se han descubierto imágenes de infraestructura {_INFRA}. O se "
        "han movido fuera de `docker/*/`, o el descubrimiento está roto: en "
        "cualquiera de los dos casos el resto del fichero pasaría en vacío."
    )
    assert _CI.is_file() and _GENERATOR.is_file()


def test_ci_derives_the_infra_image_list_from_the_tree() -> None:
    """La lista se deduce del árbol; no se le añade un nombre más a mano."""
    assert "docker/*/Dockerfile" in _infra_build_script(), (
        "ci.yml:build-images ya no deriva del árbol qué imágenes de "
        "infraestructura construir. Con la lista escrita a mano, la próxima "
        "que se añada no se construye ni se escanea y nadie se entera."
    )


def _tag_que_construye_ci() -> str:
    """El tag con el que `ci.yml` etiqueta las imágenes de `docker/*/`.

    El bucle usa la variable `$name`, así que el nombre completo no está escrito
    en ninguna línea: lo que se compara con el compose es este tag más el nombre
    de cada directorio.
    """
    script = _infra_build_script()
    encontrados = re.findall(rf'-t "{re.escape(_PREFIX)}\$name:([A-Za-z0-9._-]+)"', script)
    assert encontrados, (
        f"el paso «Build infra images» de ci.yml ya no etiqueta con "
        f"`{_PREFIX}$name:<tag>`, así que no hay forma de comparar lo que "
        "construye con lo que el compose levanta."
    )
    assert len(set(encontrados)) == 1, (
        f"ci.yml etiqueta las imágenes de infraestructura con más de un tag "
        f"({sorted(set(encontrados))}); no hay un nombre único que comparar."
    )
    return encontrados[0]


@pytest.mark.parametrize("image", _INFRA)
def test_ci_builds_every_infra_image_under_the_canonical_name(image: str) -> None:
    """Un Dockerfile, un nombre — el mismo que corre en el compose."""
    assert _PREFIX in _infra_build_script(), (
        f"ci.yml construye `{image}` con un nombre que no lleva el prefijo "
        f"`{_PREFIX}` que usan el resto de imágenes del repo. Con dos nombres "
        "para un Dockerfile, nadie puede decir si la imagen que corre es la "
        "que CI construyó."
    )
    assert _tag_que_construye_ci()


@pytest.mark.parametrize("image", _INFRA)
def test_every_infra_image_is_scanned_by_trivy(image: str) -> None:
    """Construirla y tirarla no es cobertura: es un escaneo que falta.

    No caen en `release-images.yml` (no se publican) ni en la matriz de
    `build-runtime-templates.yml` (no son templates). Si no las mira `ci.yml`,
    no las mira nadie.
    """
    scanned = _scanned_image_refs()
    assert any(image in ref for ref in scanned), (
        f"ninguna llamada a trivy-action en ci.yml escanea `{image}`. Las "
        f"imágenes escaneadas son {sorted(scanned)}."
    )


@pytest.mark.parametrize("image", _INFRA)
def test_compose_pins_the_infra_image_name(image: str) -> None:
    """Sin `image:`, Docker la bautiza con el prefijo del proyecto.

    Y entonces el nombre depende de `COMPOSE_PROJECT_NAME`, o sea que cambia
    entre el stack canónico y cada instalación del wizard.
    """
    services = _services_built_from(image)
    if not services:
        pytest.skip(f"`{image}` no lo construye ningún compose de docker/")
    for path, name, service in services:
        declared = str(service.get("image", ""))
        assert declared, (
            f"{path.name}:{name} construye `docker/{image}/` sin declarar "
            "`image:`, así que el nombre resultante depende del nombre del "
            "proyecto de compose y difiere del que construye CI."
        )
        esperado = f"{_PREFIX}{image}:{_tag_que_construye_ci()}"
        assert _default_de(declared) == esperado, (
            f"{path.name}:{name} levanta `{_default_de(declared)}` y CI "
            f"construye `{esperado}`. Coincidir en el prefijo no basta: si el "
            "tag difiere siguen siendo dos imágenes, y vuelve a no poder "
            "responderse cuál de las dos es la que está corriendo."
        )


@pytest.mark.parametrize("image", _INFRA)
def test_the_installer_generates_the_same_image_name(image: str) -> None:
    """El tercer actor: el compose que escribe el wizard de instalación."""
    source = _GENERATOR.read_text(encoding="utf-8")
    if f'"./{image}"' not in source:
        pytest.skip(f"el instalador no genera un servicio para `{image}`")
    assert f'"{_PREFIX}{image}' in source, (
        f"compose_generator.py genera el servicio `{image}` con `build:` y sin "
        f"un `image: {_PREFIX}{image}…`, así que cada instalación acaba con un "
        "nombre de imagen distinto para el mismo Dockerfile."
    )
