"""El worker resuelve la imagen de runtime por digest, o aborta (ADR 0148).

Condición 2 del ADR, literal: *«fallback explícito, no silencioso. Si el `pull`
por digest falla, el worker debe abortar la tarea con un error legible, nunca
caer a una imagen local con el mismo tag: eso reintroduciría el problema
disfrazado de resiliencia.»*

El modo de fallo que estos tests cierran no es un crash — es lo contrario: un
`containers.run` que **funciona** con la imagen que hubiera en el daemon local
bajo ese nombre, y un run verde que nadie puede auditar. Por eso la afirmación
central no es «lanza el error», sino **«NO llamó a `containers.run`»**.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit

_DIGEST = "sha256:" + "ab" * 32
_PINNED = f"ghcr.io/agentic-platform/agent-runtime-php-phpunit:v1@{_DIGEST}"
_CANONICAL = f"ghcr.io/agentic-platform/agent-runtime-php-phpunit@{_DIGEST}"


class _Images:
    """Doble del `client.images` de docker-py."""

    def __init__(self, *, local: tuple[str, ...] = (), pull_error: Exception | None = None) -> None:
        self.local = set(local)
        self.pull_error = pull_error
        self.pulled: list[str] = []
        self.got: list[str] = []

    def get(self, reference: str) -> object:
        self.got.append(reference)
        if reference not in self.local:
            raise KeyError(f"no local image {reference}")
        return object()

    def pull(self, reference: str) -> object:
        self.pulled.append(reference)
        if self.pull_error is not None:
            raise self.pull_error
        self.local.add(reference)
        return object()


class _Containers:
    def __init__(self) -> None:
        self.ran: list[str] = []

    def run(self, image: str, **kwargs: Any) -> object:
        self.ran.append(image)
        return object()


class _Client:
    def __init__(self, images: _Images) -> None:
        self.images = images
        self.containers = _Containers()


def _runner(client: _Client) -> Any:
    from workers.config import Settings
    from workers.test_runtime import TestRuntimeRunner

    return TestRuntimeRunner(Settings(), client=client)


def _spec(image: str) -> Any:
    from shared_test_runtimes.types import RuntimeTemplate
    from workers.test_runtime import RuntimePlan, TestRuntimeSpec

    template = RuntimeTemplate(id="php-phpunit", docker_image=image)
    return TestRuntimeSpec(plan=RuntimePlan(template=template, checks=()), worktree_host_path="/wt")


# ---------------------------------------------------------------------------
# helper puro
# ---------------------------------------------------------------------------


def test_ensure_pulls_the_canonical_digest_reference() -> None:
    from workers.test_runtime import ensure_runtime_image

    client = _Client(_Images())
    assert ensure_runtime_image(client, _PINNED) == _CANONICAL
    assert client.images.pulled == [_CANONICAL]


def test_ensure_skips_the_pull_when_the_digest_is_already_local() -> None:
    """Un digest ya presente ES la imagen correcta: es direccionable por contenido.

    Sin este atajo cada lanzamiento pagaría una ida al registry, y un host con
    el registry temporalmente caído no podría correr ni lo que ya descargó.
    """
    from workers.test_runtime import ensure_runtime_image

    client = _Client(_Images(local=(_CANONICAL,)))
    assert ensure_runtime_image(client, _PINNED) == _CANONICAL
    assert client.images.pulled == []


def test_ensure_leaves_an_unpinned_reference_alone() -> None:
    """La imagen propia de un proyecto (ADR 0129) se construye en el host.

    No vive en ningún registry y no tiene digest publicado: exigirle pull la
    rompería. La garantía se aplica donde hay procedencia que verificar.
    """
    from workers.test_runtime import ensure_runtime_image

    client = _Client(_Images())
    assert ensure_runtime_image(client, "proyecto-x-runtime:latest") == "proyecto-x-runtime:latest"
    assert client.images.pulled == []


def test_ensure_raises_a_legible_error_when_the_pull_fails() -> None:
    from workers.test_runtime import RuntimeImageUnavailableError, ensure_runtime_image

    client = _Client(_Images(pull_error=RuntimeError("manifest unknown")))
    with pytest.raises(RuntimeImageUnavailableError) as excinfo:
        ensure_runtime_image(client, _PINNED)

    message = str(excinfo.value)
    assert _DIGEST in message, "el error debe decir QUÉ digest no pudo resolver"
    assert "manifest unknown" in message, "el error debe arrastrar la causa del daemon"
    assert "0148" in message, "el error debe apuntar al ADR que explica por qué no hay fallback"


# ---------------------------------------------------------------------------
# el camino que importa: el lanzamiento
# ---------------------------------------------------------------------------


def test_launch_runs_the_digest_reference_it_pulled() -> None:
    from workers.test_runtime import assert_no_docker_socket  # noqa: F401  (envelope real)

    client = _Client(_Images())
    runner = _runner(client)
    runner._start_main(_spec(_PINNED), "bridge-test")

    assert client.images.pulled == [_CANONICAL]
    assert client.containers.ran == [_CANONICAL], (
        "el contenedor debe lanzarse con la MISMA referencia por digest que se "
        "descargó; lanzar el tag dejaría que el daemon eligiera otra cosa"
    )


def test_launch_aborts_without_running_anything_when_the_pull_fails() -> None:
    """La aserción de fondo del ADR 0148: preferir el fallo al run inauditable."""
    from workers.test_runtime import RuntimeImageUnavailableError

    client = _Client(_Images(pull_error=RuntimeError("unauthorized")))
    runner = _runner(client)

    with pytest.raises(RuntimeImageUnavailableError):
        runner._start_main(_spec(_PINNED), "bridge-test")

    assert client.containers.ran == [], (
        "el worker cayó a la imagen local: exactamente el fallback silencioso que "
        "la condición 2 del ADR 0148 prohíbe"
    )


def test_launch_still_works_for_the_unpinned_catalog_of_today() -> None:
    """Mientras no haya release publicada el catálogo trae el nombre local.

    Si esta guarda se pone roja, el cambio de referencia dejó al operador sin
    poder ejecutar tests hasta que exista una release: un rojo de producción
    disfrazado de mejora de seguridad.
    """
    client = _Client(_Images())
    runner = _runner(client)
    runner._start_main(_spec("agent-runtime-php-phpunit:v1"), "bridge-test")

    assert client.containers.ran == ["agent-runtime-php-phpunit:v1"]
    assert client.images.pulled == []
