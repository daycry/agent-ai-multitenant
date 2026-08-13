"""Catalog of the fourteen curated test-runtime templates (Plan 06 task_06_02).

Each entry is a :class:`RuntimeTemplate` declaring the runtime's
contract with the worker. The matching Dockerfile lives at
``docker/agent-runtimes/<id>/Dockerfile`` and is built by
``task_06_03``'s CI workflow into ``agent-runtime-<id>:v1``.

The catalog is the single source of truth: when a project lists a
runtime in ``Project.execution_runtimes`` or a task's
``acceptance_criteria.runtime`` field, the platform resolves the
template through :func:`get` and uses the fields to launch the
test-runtime container.

Adding a new template ⇒
  1. Add the Dockerfile under ``docker/agent-runtimes/<new-id>/``.
  2. Register the :class:`RuntimeTemplate` in :data:`CATALOG`.
  3. Add the build line in CI (task_06_03 workflow).

**Referencia de las imágenes (ADR 0148).** Hasta el 2026-08-01 esto era
``_IMAGE_TAG = "v1"`` y un nombre local: cada host construía su propia
variante de las 14 imágenes donde corre el código NO confiable, y el
sistema no podía responder «¿qué imagen exacta ejecutó el código de este
tenant?». Ahora la referencia la compone el **manifiesto de release**
(:mod:`shared_test_runtimes.images`), que reescribe el pipeline: cuando
hay release publicada es ``<registry>/agent-runtime-<slug>:<version>@<digest>``
y el worker resuelve por digest o aborta; mientras no la haya, el nombre
local de siempre. Aquí no se escribe ningún digest a mano.
"""

from __future__ import annotations

from collections.abc import Mapping

from shared_test_runtimes.images import ReleaseManifest, RuntimeImageManifestError, load_manifest
from shared_test_runtimes.types import Resources, RuntimeTemplate

# Resultado de la última release publicada. Se lee UNA vez al importar: la
# referencia de una imagen no puede cambiar a mitad de la vida del proceso.
MANIFEST: ReleaseManifest = load_manifest()


def _image(slug: str) -> str:
    """Referencia de la imagen de una plantilla, según el manifiesto de release."""
    return MANIFEST.reference(slug)


# --- Python ---------------------------------------------------------

PYTHON_PYTEST = RuntimeTemplate(
    id="python-pytest",
    docker_image=_image("python-pytest"),
    dep_cache_mount="/home/agent/.cache/pip",
    default_pre_install=(
        "pip install --upgrade pip",
        "pip install -r requirements.txt",
    ),
    output_parsers=("junit_xml", "raw_text"),
    cache_env=(("PIP_CACHE_DIR", "/home/agent/.cache/pip"),),
    dependency_dirs=(".venv", "venv"),
)

# --- Node -----------------------------------------------------------

NODE_JEST = RuntimeTemplate(
    id="node-jest",
    docker_image=_image("node-jest"),
    dep_cache_mount="/home/agent/.npm",
    default_pre_install=("npm ci",),
    output_parsers=("jest_json", "junit_xml", "raw_text"),
    cache_env=(("npm_config_cache", "/home/agent/.npm"),),
    dependency_dirs=("node_modules",),
)

NODE_VITEST = RuntimeTemplate(
    id="node-vitest",
    docker_image=_image("node-vitest"),
    dep_cache_mount="/home/agent/.npm",
    default_pre_install=("npm ci",),
    output_parsers=("junit_xml", "raw_text"),
    cache_env=(("npm_config_cache", "/home/agent/.npm"),),
    dependency_dirs=("node_modules",),
)

NODE_PLAYWRIGHT = RuntimeTemplate(
    id="node-playwright",
    docker_image=_image("node-playwright"),
    dep_cache_mount="/home/agent/.npm",
    default_pre_install=("npm ci",),
    # Playwright is browser-heavy — give it more headroom.
    default_resources=Resources(cpu=2.0, memory_mb=2048),
    output_parsers=("playwright_json", "junit_xml", "raw_text"),
    cache_env=(("npm_config_cache", "/home/agent/.npm"),),
    dependency_dirs=("node_modules",),
)

# --- PHP ------------------------------------------------------------

PHP_PHPUNIT = RuntimeTemplate(
    id="php-phpunit",
    docker_image=_image("php-phpunit"),
    dep_cache_mount="/home/agent/.composer/cache",
    default_pre_install=("composer install --no-interaction --no-progress",),
    output_parsers=("junit_xml", "raw_text"),
    cache_env=(("COMPOSER_CACHE_DIR", "/home/agent/.composer/cache"),),
    dependency_dirs=("vendor",),
)

PHP_PEST = RuntimeTemplate(
    id="php-pest",
    docker_image=_image("php-pest"),
    dep_cache_mount="/home/agent/.composer/cache",
    default_pre_install=("composer install --no-interaction --no-progress",),
    output_parsers=("junit_xml", "raw_text"),
    cache_env=(("COMPOSER_CACHE_DIR", "/home/agent/.composer/cache"),),
    dependency_dirs=("vendor",),
)

# --- Go -------------------------------------------------------------

GO_TEST = RuntimeTemplate(
    id="go-test",
    docker_image=_image("go-test"),
    dep_cache_mount="/home/agent/go/pkg/mod",
    default_pre_install=("go mod download",),
    output_parsers=("go_test_json", "raw_text"),
    # The golang image defaults GOPATH=/go, so the mount at /home/agent/go/pkg/mod is
    # ignored unless we point the module cache at it explicitly.
    cache_env=(
        ("GOPATH", "/home/agent/go"),
        ("GOMODCACHE", "/home/agent/go/pkg/mod"),
    ),
)

# --- Java -----------------------------------------------------------

JAVA_MAVEN = RuntimeTemplate(
    id="java-maven",
    docker_image=_image("java-maven"),
    dep_cache_mount="/home/agent/.m2/repository",
    default_pre_install=("mvn -B dependency:go-offline",),
    default_resources=Resources(cpu=2.0, memory_mb=2048),
    output_parsers=("surefire_xml", "junit_xml", "raw_text"),
    cache_env=(("MAVEN_OPTS", "-Dmaven.repo.local=/home/agent/.m2/repository"),),
)

JAVA_GRADLE = RuntimeTemplate(
    id="java-gradle",
    docker_image=_image("java-gradle"),
    dep_cache_mount="/home/agent/.gradle/caches",
    default_pre_install=("gradle --no-daemon dependencies",),
    default_resources=Resources(cpu=2.0, memory_mb=2048),
    output_parsers=("junit_xml", "raw_text"),
    # caches/ live under $GRADLE_USER_HOME → /home/agent/.gradle/caches.
    cache_env=(("GRADLE_USER_HOME", "/home/agent/.gradle"),),
)

# --- Ruby -----------------------------------------------------------

RUBY_RSPEC = RuntimeTemplate(
    id="ruby-rspec",
    docker_image=_image("ruby-rspec"),
    dep_cache_mount="/home/agent/.bundle",
    default_pre_install=("bundle install --jobs=4",),
    output_parsers=("junit_xml", "raw_text"),
    cache_env=(("BUNDLE_PATH", "/home/agent/.bundle"),),
    dependency_dirs=("vendor",),
)

# --- Rust -----------------------------------------------------------

RUST_CARGO = RuntimeTemplate(
    id="rust-cargo",
    docker_image=_image("rust-cargo"),
    dep_cache_mount="/home/agent/.cargo/registry",
    default_pre_install=("cargo fetch",),
    default_resources=Resources(cpu=2.0, memory_mb=2048),
    output_parsers=("rust_test_json", "raw_text"),
    # registry/ lives under $CARGO_HOME → /home/agent/.cargo/registry.
    cache_env=(("CARGO_HOME", "/home/agent/.cargo"),),
)

# --- .NET -----------------------------------------------------------

DOTNET_TEST = RuntimeTemplate(
    id="dotnet-test",
    docker_image=_image("dotnet-test"),
    dep_cache_mount="/home/agent/.nuget/packages",
    default_pre_install=("dotnet restore",),
    default_resources=Resources(cpu=2.0, memory_mb=2048),
    output_parsers=("trx", "junit_xml", "raw_text"),
    cache_env=(("NUGET_PACKAGES", "/home/agent/.nuget/packages"),),
)

# --- Generic --------------------------------------------------------

# Bare-bones shell runner: bash + git + curl + jq. No language toolchain.
# Used for tasks whose "tests" are arbitrary shell scripts (e.g. running
# a Makefile that wraps multiple stacks).
GENERIC_SHELL = RuntimeTemplate(
    id="generic-shell",
    docker_image=_image("generic-shell"),
    # No package manager → no dep-cache to mount.
    dep_cache_mount=None,
    default_pre_install=(),
    output_parsers=("tap", "raw_text"),
)

# HTTP probe runner: curl + jq + httpie. Intended to talk to a running
# service (the task's compose, or a staging URL when the project opts
# into ``network_policy="open"``).
GENERIC_HTTP = RuntimeTemplate(
    id="generic-http",
    docker_image=_image("generic-http"),
    dep_cache_mount=None,
    default_pre_install=(),
    output_parsers=("raw_text",),
    # Default to ``restricted`` so the runner can hit the task's
    # compose services but not the public internet. A project can
    # override to ``open`` per task when probing a staging API.
    network_policy="restricted",
)


# The catalog itself. Insertion order = display order in the UI of
# task_06_12 (Project.execution_runtimes editor).
CATALOG: Mapping[str, RuntimeTemplate] = {
    t.id: t
    for t in (
        PYTHON_PYTEST,
        NODE_JEST,
        NODE_VITEST,
        NODE_PLAYWRIGHT,
        PHP_PHPUNIT,
        PHP_PEST,
        GO_TEST,
        JAVA_MAVEN,
        JAVA_GRADLE,
        RUBY_RSPEC,
        RUST_CARGO,
        DOTNET_TEST,
        GENERIC_SHELL,
        GENERIC_HTTP,
    )
}


def assert_manifest_covers_catalog(manifest: ReleaseManifest) -> None:
    """Un pin a medias es peor que ninguno: aborta si falta alguna plantilla.

    Si el pipeline publica trece de catorce y la que falta se queda con tag
    mutable, la pregunta que el ADR 0148 vino a contestar sigue sin respuesta
    justo en la plantilla que nadie está mirando — y con la apariencia de que sí
    la tiene. Fallar al importar es ruidoso a propósito.
    """
    if not manifest.is_pinned:
        return
    missing = sorted(tid for tid in CATALOG if manifest.digest_for(tid) is None)
    if missing:
        raise RuntimeImageManifestError(
            "el manifiesto de release trae digests pero deja plantillas sin fijar: "
            + ", ".join(missing)
        )


assert_manifest_covers_catalog(MANIFEST)


def get(template_id: str) -> RuntimeTemplate:
    """Resolve a runtime template by id.

    Raises:
        KeyError: when ``template_id`` is not in the catalog. The
            caller is expected to surface this as a 422 to the user
            (the project / task config referenced an unknown runtime).
    """
    try:
        return CATALOG[template_id]
    except KeyError as exc:
        known = ", ".join(sorted(CATALOG))
        raise KeyError(f"unknown runtime template {template_id!r}; known: {known}") from exc


def list_ids() -> tuple[str, ...]:
    """Return the catalog ids in declared order."""
    return tuple(CATALOG)


def dependency_dirs() -> tuple[str, ...]:
    """Every stack's dependency directories, deduplicated and sorted.

    The UNION and not one template's list, because a worktree legitimately holds
    several stacks at once — a monorepo with a PHP backend and a node frontend is
    the common case, and preserving only the declared default template's dirs
    would still wipe the other's on every sync (task_wf_24, C-06). The names are
    unambiguous enough (``vendor``, ``node_modules``, ``.venv``) that the union
    costs nothing.
    """
    return tuple(sorted({d for t in CATALOG.values() for d in t.dependency_dirs}))


__all__ = [
    "CATALOG",
    "DOTNET_TEST",
    "GENERIC_HTTP",
    "GENERIC_SHELL",
    "GO_TEST",
    "JAVA_GRADLE",
    "JAVA_MAVEN",
    "MANIFEST",
    "NODE_JEST",
    "NODE_PLAYWRIGHT",
    "NODE_VITEST",
    "PHP_PEST",
    "PHP_PHPUNIT",
    "PYTHON_PYTEST",
    "RUBY_RSPEC",
    "RUST_CARGO",
    "assert_manifest_covers_catalog",
    "dependency_dirs",
    "get",
    "list_ids",
]
