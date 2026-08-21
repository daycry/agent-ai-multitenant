"""Las imágenes auxiliares del worker van fijadas por digest (prod-11 task_digest_pin_11).

Las 22 bases externas bajo ``docker/`` llevan ``@sha256:`` desde el 2026-07-31.
Estas DOS no: ``postgres:16-alpine`` y ``redis:7-alpine``, los sidecars que el
worker levanta junto al test-runtime para que un proyecto tenga una base de
datos contra la que correr sus tests. Vivían en constantes de módulo
(``DEFAULT_POSTGRES`` / ``DEFAULT_REDIS``) y por eso se quedaron fuera de la
guarda que recorre Dockerfiles.

Por qué importa que sean justo estas dos, y no es un tecnicismo de inventario:
son los únicos contenedores del sistema que **comparten la red per-tarea con
código no confiable**. El agente escribe los tests que se ejecutan a su lado.
Un tag rodante ahí significa que nadie puede decir qué binario corrió en un run
concreto — y esa es exactamente la pregunta que se hace después de un incidente.

Tres invariantes:

  1. las referencias declaran digest;
  2. conservan el tag legible (``postgres:16-alpine@sha256:…``), sin el cual
     nadie sabe qué versión es ni Dependabot puede proponer la siguiente;
  3. el LANZAMIENTO honra el digest — que es lo que separa un pin real de un
     comentario decorativo. Si el pull por digest falla, la tarea ABORTA en vez
     de correr lo que hubiera en el daemon local bajo ese nombre (ADR 0148,
     condición 2). Sin la 3, las otras dos son papel: ``containers.run`` con un
     tag suelto habría seguido descargando cualquier cosa.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

pytestmark = pytest.mark.unit

#: ``repo:tag@sha256:<64 hex>`` — tag legible OBLIGATORIO delante del digest.
_PINNED_WITH_TAG = re.compile(r"^[^@\s]+:[^@\s:]+@sha256:[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# 1 y 2 — la declaración
# ---------------------------------------------------------------------------
def test_default_aux_images_are_pinned_by_digest_with_a_readable_tag() -> None:
    """Descubrimiento sobre el catálogo real, con suelo: si deja de encontrar
    servicios auxiliares, esta guarda FALLA en vez de aprobar en vacío (§4 de
    `verificar-antes-de-implementar.md`)."""
    from workers.test_runtime import default_aux_services

    services = default_aux_services()
    assert len(services) >= 2, (
        f"la guarda dejó de encontrar los servicios auxiliares (vio {len(services)}): "
        "o se renombró `default_aux_services`, o alguien los quitó"
    )

    problems: list[str] = []
    for aux in services:
        if "@sha256:" not in aux.image:
            problems.append(
                f"{aux.name}: `{aux.image}` va por TAG. Un tag rodante en un "
                "sidecar que comparte red con código no confiable hace que "
                "nadie pueda decir qué binario corrió en un run concreto."
            )
        elif not _PINNED_WITH_TAG.match(aux.image):
            problems.append(
                f"{aux.name}: `{aux.image}` tiene digest pero no un tag legible "
                "delante. `postgres@sha256:…` es inauditable de un vistazo y deja "
                "a Dependabot sin saber qué versión proponer después."
            )
    assert not problems, "imágenes auxiliares sin fijar:\n" + "\n".join(problems)


def _pin_problem(label: str, image: str) -> str | None:
    """El mismo criterio, reutilizable: `None` si la referencia está bien fijada."""
    if "@sha256:" not in image:
        return (
            f"{label}: `{image}` va por TAG. Un tag en un contenedor que comparte "
            "red per-tarea con código no confiable hace que nadie pueda decir qué "
            "binario corrió en un run concreto."
        )
    if not _PINNED_WITH_TAG.match(image):
        return (
            f"{label}: `{image}` tiene digest pero no un tag legible delante, "
            "así que es inauditable de un vistazo."
        )
    return None


def test_the_project_service_catalog_is_pinned_too() -> None:
    """La TERCERA superficie, la que el enunciado de la casilla no nombraba.

    `default_aux_services()` son los dos sidecars por defecto; el catálogo del
    **ADR 0129** (`runtime_services.SERVICE_CATALOG`) es lo que un proyecto puede
    declarar en su config —mysql, mariadb, postgres, redis, beanstalkd— y acaba
    en los mismos `AuxServiceSpec`, en el mismo bridge per-tarea, al lado del
    mismo código no confiable.

    Pinear sólo los dos por defecto y llamarlo «imágenes auxiliares del worker
    fijadas» habría sido cobertura aparente: la peor de las tres opciones,
    porque se lee como cobertura. Y el peor caso vivía justo aquí —
    `schickling/beanstalkd:latest`, un tag rodante de una imagen de tercero.
    """
    from workers.runtime_services import SERVICE_CATALOG

    assert len(SERVICE_CATALOG) >= 5, (
        f"la guarda dejó de encontrar el catálogo de servicios (vio "
        f"{len(SERVICE_CATALOG)}): ¿se renombró `SERVICE_CATALOG`?"
    )

    problems = [
        problem
        for name, spec in SERVICE_CATALOG.items()
        if (problem := _pin_problem(f"SERVICE_CATALOG[{name!r}]", spec.default_image)) is not None
    ]
    assert not problems, "servicios del ADR 0129 sin fijar:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# 3 — el lanzamiento, que es donde un pin se gana el nombre
# ---------------------------------------------------------------------------
class _Images:
    """Doble del ``client.images`` de docker-py (mismo patrón que
    tests/unit/test_runtime_image_pull.py)."""

    def __init__(self, *, pull_error: Exception | None = None) -> None:
        self.local: set[str] = set()
        self.pull_error = pull_error
        self.pulled: list[str] = []

    def get(self, reference: str) -> object:
        if reference not in self.local:
            raise KeyError(f"no local image {reference}")
        return object()

    def pull(self, reference: str) -> object:
        self.pulled.append(reference)
        if self.pull_error is not None:
            raise self.pull_error
        self.local.add(reference)
        return object()


class _ExecResult:
    exit_code = 0


class _Container:
    def exec_run(self, cmd: Any) -> _ExecResult:
        return _ExecResult()


class _Containers:
    def __init__(self) -> None:
        self.ran: list[str] = []

    def run(self, image: str, **kwargs: Any) -> _Container:
        self.ran.append(image)
        return _Container()


class _Client:
    def __init__(self, images: _Images) -> None:
        self.images = images
        self.containers = _Containers()


def _runner(client: _Client) -> Any:
    from workers.config import Settings
    from workers.test_runtime import TestRuntimeRunner

    return TestRuntimeRunner(Settings(), client=client)


def _spec_with_defaults() -> Any:
    from shared_test_runtimes.types import RuntimeTemplate
    from workers.test_runtime import RuntimePlan, TestRuntimeSpec, default_aux_services

    template = RuntimeTemplate(id="python-pytest", docker_image="agent-runtime-python-pytest:v1")
    return TestRuntimeSpec(
        plan=RuntimePlan(template=template, checks=()),
        worktree_host_path="/wt",
        aux_services=default_aux_services(),
    )


def _canonical(reference: str) -> str:
    repo = reference.split(":", 1)[0]
    digest = reference.split("@", 1)[1]
    return f"{repo}@{digest}"


def test_aux_services_launch_the_digest_reference_they_pulled() -> None:
    """Se lanza ``repo@sha256:…``, no el tag del que salió.

    Pasar el tag a ``containers.run`` después de haber descargado el digest deja
    al daemon elegir otra vez — y puede elegir distinto. El pin sólo vale si la
    referencia que se ejecuta es la direccionable por contenido.
    """
    client = _Client(_Images())
    runner = _runner(client)
    spec = _spec_with_defaults()

    runner._start_aux_services(spec, "net-de-la-tarea")

    expected = [_canonical(aux.image) for aux in spec.aux_services]
    assert client.containers.ran == expected, (
        f"los sidecars se lanzaron como {client.containers.ran}, no por su digest "
        f"canónico {expected}"
    )
    assert client.images.pulled == expected, (
        "no se descargó ninguna imagen por digest: el pin de las constantes no "
        f"llega al lanzamiento (pulled={client.images.pulled})"
    )


def test_an_unresolvable_aux_image_aborts_instead_of_running_whatever_is_local() -> None:
    """ADR 0148, condición 2, aplicada también a los sidecars.

    La afirmación que importa NO es «lanza el error», sino **«no llamó a
    `containers.run`»**: el fallo caro aquí no es un crash, es un postgres
    cualquiera levantándose bajo el nombre correcto al lado de código no
    confiable, y un run verde que nadie puede auditar.
    """
    from workers.test_runtime import RuntimeImageUnavailableError

    client = _Client(_Images(pull_error=RuntimeError("manifest unknown")))
    runner = _runner(client)

    with pytest.raises(RuntimeImageUnavailableError):
        runner._start_aux_services(_spec_with_defaults(), "net-de-la-tarea")

    assert client.containers.ran == [], (
        "se levantó un sidecar pese a no poder resolver su digest: es la caída "
        "silenciosa a la imagen local con el mismo tag que el ADR 0148 prohíbe"
    )
