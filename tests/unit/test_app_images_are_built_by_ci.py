"""Toda app que hereda de la imagen base se construye y se publica.

Hay dos familias de `apps/*/Dockerfile` en este repo y **se construyen de forma
incompatible**:

* **Derivadas** (`ARG BASE_IMAGE` + `FROM ${BASE_IMAGE}`): reutilizan la imagen
  pesada de la api-server y hacen `COPY apps/<app>/…`, o sea que su contexto de
  build es la **raíz del repo**. Hoy: workers, orchestrator,
  notification-dispatcher y watchdog.
* **Autocontenidas** (admin-panel, installer, installer/backend): `COPY` relativo
  a su propio directorio, que es su contexto.

Construir una derivada con su propio directorio como contexto **no da una imagen
mala: da un fallo de build**, porque `COPY apps/watchdog/pyproject.toml` no
resuelve dentro de `apps/watchdog/`.

**El defecto que motiva este fichero (2026-08-12).** `apps/watchdog/Dockerfile`
entró el 2026-08-02 con el servicio de compose de `task_prod08_watchdog_14`. Los
dos sitios que reparten apps entre las dos familias llevaban la lista **escrita a
mano**:

* `ci.yml:build-images` → `for app in workers orchestrator notification-dispatcher`
  y el mismo trío en el `case` que excluye del bucle de autocontenidas;
* `release-images.yml` → `matrix.app: [workers, orchestrator, notification-dispatcher]`.

Nadie las actualizó. Consecuencia doble: el `watchdog` caía al bucle de
autocontenidas y **rompía el job `build-images`**, y no se publicaba en ningún
sitio, así que el `${IMAGE_WATCHDOG}` del compose canónico no tenía imagen que
levantar. No se vio porque CI lleva caído desde el 2026-07-30 por facturación.

Por eso la guarda **deriva la lista del árbol** y no la enumera: una lista escrita
a mano fue exactamente el modo de fallo, y una guarda que repita la lista a mano
lo repetiría.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APPS = _REPO_ROOT / "apps"
_CI = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_RELEASE = _REPO_ROOT / ".github" / "workflows" / "release-images.yml"

#: Suelo del descubrimiento: si deja de ver apps derivadas, la guarda pasaría en
#: vacío diciendo que todas están cubiertas.
_MINIMUM_DERIVED = 3


def _derived_apps() -> list[str]:
    """Apps de `apps/*/` cuyo Dockerfile hereda de la imagen base."""
    found = []
    for dockerfile in sorted(_APPS.glob("*/Dockerfile")):
        text = dockerfile.read_text(encoding="utf-8")
        if "ARG BASE_IMAGE" in text:
            found.append(dockerfile.parent.name)
    return found


def _workflow(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _build_each_app_script() -> str:
    job = _workflow(_CI)["jobs"]["build-images"]
    for step in job["steps"]:
        if step.get("name") == "Build each app":
            return str(step.get("run", ""))
    raise AssertionError("ci.yml:build-images perdió el paso «Build each app»")


_DERIVED = _derived_apps()


def test_the_guard_still_sees_the_derived_apps() -> None:
    assert len(_DERIVED) >= _MINIMUM_DERIVED, (
        f"solo se han descubierto apps derivadas {_DERIVED}. O el patrón "
        "`ARG BASE_IMAGE` dejó de usarse, o el descubrimiento está roto: en "
        "cualquiera de los dos casos el resto del fichero pasaría en vacío."
    )
    assert _CI.is_file() and _RELEASE.is_file()


def test_ci_derives_the_backend_app_list_from_the_dockerfiles() -> None:
    """El arreglo es que la lista se DEDUZCA, no que se le añada un nombre más.

    Si mañana vuelve a escribirse a mano, la próxima app derivada volverá a caer
    en el bucle de autocontenidas y a romper el job — que es lo que pasó con el
    watchdog durante diez días sin que nadie lo viera.
    """
    script = _build_each_app_script()
    assert "ARG BASE_IMAGE" in script, (
        "ci.yml:build-images ya no deriva del árbol qué apps heredan de la "
        "imagen base. Con la lista escrita a mano, añadir una app derivada "
        "rompe el job en silencio hasta que alguien mira los logs de CI."
    )


@pytest.mark.parametrize("app", _DERIVED)
def test_every_derived_app_builds_with_the_repo_root_as_context(app: str) -> None:
    """Su contexto es la raíz y lleva `--build-arg BASE_IMAGE`, o no compila."""
    script = _build_each_app_script()
    assert "--build-arg BASE_IMAGE" in script, (
        f"{app} hereda de la base y el script de build no pasa --build-arg "
        "BASE_IMAGE en ninguna parte"
    )
    # Nadie puede construirla con `docker build … "$app_dir"`, la forma de las
    # autocontenidas: `COPY apps/<app>/…` no resolvería.
    hardcoded_self_contained = re.findall(
        rf'docker build [^\n]*-t "?agentic-platform/{re.escape(app)}[^\n]*"\$app_dir"', script
    )
    assert not hardcoded_self_contained, (
        f"{app} se construye con su propio directorio como contexto: "
        f"`COPY apps/{app}/…` no resuelve ahí y el build falla"
    )


@pytest.mark.parametrize("app", _DERIVED)
def test_every_derived_app_is_published_by_the_release_workflow(app: str) -> None:
    """Sin publicarla, el `image:` del compose no tiene nada que levantar.

    El `watchdog` del compose canónico apunta a
    `${IMAGE_WATCHDOG:-agentic-platform/watchdog:latest}`; si el workflow de
    release no la sube, ese servicio no arranca en ningún host que no la haya
    construido a mano.
    """
    release = _workflow(_RELEASE)
    published: set[str] = set()
    for job in release["jobs"].values():
        matrix = ((job.get("strategy") or {}).get("matrix") or {}).get("app") or []
        published.update(str(entry) for entry in matrix)
        # Los jobs sin matriz nombran su Dockerfile en el `file:` del build-push.
        for step in job.get("steps") or []:
            file_ref = ((step.get("with") or {}).get("file")) or ""
            match = re.fullmatch(r"apps/([^/]+)/Dockerfile", str(file_ref))
            if match:
                published.add(match.group(1))
    assert app in published, (
        f"release-images.yml no publica `{app}`, que hereda de la imagen base. "
        f"Las apps publicadas son {sorted(published)}."
    )
