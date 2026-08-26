"""Cada combinación de `-f` documentada produce un proyecto que compose acepta.

Un overlay puede **override**ar un servicio que declara otro fichero (añadirle un
volumen, una red, un `depends_on`). Si el fichero que lo DECLARA no está en el
`-f`, compose no ignora el override: aborta el proyecto entero con

    service "X" has neither an image nor a build context specified

…y no solo el `up`: también `ps`, `logs` y `config`. Es un fallo de arranque
total provocado por un servicio que el operador ni siquiera quería levantar.

**El caso real (2026-08-12).** `docker-compose.monitoring.yml` overridea
`workers` para montarle el drop-dir del textfile-collector (sin ese bind, las
métricas de aplicación no llegan a Prometheus). Pero `workers` lo declara la capa
de APLICACIONES —`docker-compose.manuals.yml` en dev, el compose que genera el
instalador en producción—, no la de infraestructura. Resultado: **todas** estas
invocaciones documentadas abortaban:

    docker compose -f docker/docker-compose.monitoring.yml up -d alertmanager
    docker compose -f docker/docker-compose.yml -f docker/docker-compose.monitoring.yml up -d
    scripts/dev/up.ps1 -Monitoring          # y up.sh --monitoring

El stack vivo del operador no lo notaba porque su invocación real incluye
`docker-compose.manuals.yml` (verificado en la etiqueta
`com.docker.compose.project.config_files` de sus contenedores). O sea: la trampa
estaba reservada para quien siguiera la documentación.

Esta guarda hace en Python lo mínimo que compose comprueba —cada servicio del
proyecto fusionado tiene `image` o `build`— sobre las combinaciones que los
runbooks y los scripts prometen. No necesita el daemon, así que corre en CI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.unit._compose_yaml import ComposeLoader

pytestmark = pytest.mark.unit

_DOCKER = Path(__file__).resolve().parents[2] / "docker"

#: Combinaciones que alguien puede teclear porque están escritas en algún sitio.
#: El nombre es el que sale en el mensaje de error, así que dice DÓNDE mirar.
_DOCUMENTED_STACKS: dict[str, tuple[str, ...]] = {
    "infra sola": ("docker-compose.yml",),
    "dev (scripts/dev/up.*)": ("docker-compose.yml", "docker-compose.dev.yml"),
    "dev + apps": (
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "docker-compose.manuals.yml",
    ),
    # Los scripts de desarrollo NO levantan las apps en Docker: corren
    # api-server y admin-panel nativamente desde el venv (API_PID_FILE /
    # VENV_PY). Por eso su `--monitoring` apila el overlay SIN la capa de
    # aplicaciones, y por eso el overlay tiene que ser componible por su cuenta.
    "dev + monitorización (scripts/dev/up.sh --monitoring)": (
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "docker-compose.monitoring.yml",
        "docker-compose.monitoring.dev.yml",
    ),
    "dev + monitorización (scripts/dev/up.ps1 -Monitoring)": (
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "docker-compose.monitoring.yml",
        "docker-compose.monitoring.dev.yml",
        "docker-compose.windows.yml",
    ),
    # 04-reference/backup-restore.md §«Monitorización del host + alertas» y
    # 06-runbooks/02-troubleshooting.md §«Presión de recursos» mandan teclear
    # exactamente esto para ver los dashboards de host.
    "infra + monitorización (backup-restore / troubleshooting)": (
        "docker-compose.yml",
        "docker-compose.monitoring.yml",
    ),
    "dev + apps + monitorización": (
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "docker-compose.manuals.yml",
        "docker-compose.monitoring.yml",
        "docker-compose.monitoring.apps.yml",
        "docker-compose.monitoring.dev.yml",
    ),
    "dev + apps + monitorización (Windows)": (
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "docker-compose.manuals.yml",
        "docker-compose.monitoring.yml",
        "docker-compose.monitoring.apps.yml",
        "docker-compose.monitoring.dev.yml",
        "docker-compose.windows.yml",
    ),
    # Producción-shaped: la capa de aplicaciones sin los overrides de dev. Es la
    # única forma en que el override de `workers` del textfile-collector llega a
    # aplicarse, y la razón de que viva en un fichero aparte.
    "apps + monitorización (sin overrides de dev)": (
        "docker-compose.yml",
        "docker-compose.manuals.yml",
        "docker-compose.monitoring.yml",
        "docker-compose.monitoring.apps.yml",
    ),
    "apps sin monitorización": (
        "docker-compose.yml",
        "docker-compose.manuals.yml",
    ),
    "CI": ("docker-compose.yml", "docker-compose.ci.yml"),
}


def _services(file_name: str) -> dict[str, dict[str, Any]]:
    text = (_DOCKER / file_name).read_text(encoding="utf-8")
    raw = yaml.load(text, Loader=ComposeLoader) or {}
    services = raw.get("services") or {}
    return {
        name: (body or {})
        for name, body in services.items()
        if body is None or isinstance(body, dict)
    }


def test_the_guard_still_sees_the_compose_files() -> None:
    for stack in _DOCUMENTED_STACKS.values():
        for file_name in stack:
            assert (_DOCKER / file_name).is_file(), f"falta docker/{file_name}"
    assert len(_services("docker-compose.yml")) >= 8, "el compose canónico dejó de parsearse"


@pytest.mark.parametrize(("label", "files"), sorted(_DOCUMENTED_STACKS.items()))
def test_every_service_of_a_documented_stack_has_an_image_or_a_build(
    label: str, files: tuple[str, ...]
) -> None:
    """Lo mismo que valida `docker compose config`, sin necesitar el daemon."""
    runnable: set[str] = set()
    mentioned: set[str] = set()
    for file_name in files:
        for name, body in _services(file_name).items():
            mentioned.add(name)
            if body.get("image") or body.get("build"):
                runnable.add(name)

    orphans = sorted(mentioned - runnable)
    assert not orphans, (
        f"la combinación «{label}» ({' + '.join(files)}) overridea servicios que "
        f"ningún fichero suyo declara: {orphans}. `docker compose` aborta el "
        "proyecto ENTERO —`up`, `ps`, `logs` y `config`— aunque esos servicios no "
        "se fueran a levantar. O el overlay que los overridea no pertenece a esta "
        "combinación, o falta el fichero que los declara."
    )
