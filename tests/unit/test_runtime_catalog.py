"""Unit tests for the runtime template catalog (Plan 06 task_06_02).

These tests are filesystem-aware but daemon-free: they verify that the
in-memory catalog (``shared_test_runtimes.catalog.CATALOG``) and the
on-disk Dockerfiles agree on the set of supported runtimes. The
heavyweight ``docker build`` of every template lives in task_06_03's
CI workflow — running fourteen builds in unit-test time would take
half an hour per push.

Concretely:

  * Every catalog entry has a matching ``docker/agent-runtimes/<id>/``
    folder with a Dockerfile.
  * Every Dockerfile under ``docker/agent-runtimes/`` (except the
    plan-02 ``agent-runtime`` one) is referenced by a catalog entry.
  * Each Dockerfile starts with ``# syntax=docker/dockerfile:1.7`` and
    has a ``FROM`` line — a basic sanity check that catches truncated
    or empty files.
  * The catalog hits the fourteen names the plan lists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# Walk up from this test file: tests/unit/.. → repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES_DIR = REPO_ROOT / "docker" / "agent-runtimes"

# Dos imágenes de `docker/agent-runtimes/` NO son test-runtime templates y por
# eso no están en el catálogo: el `agent-runtime` (el contenedor del bucle del
# agente, plan-02) y el `browser-runtime` (la sesión de navegador sandboxeada del
# córtex, ADR 0080 — la lanza el worker por sesión aprobada, no un plan de tests).
EXCLUDED_IMAGE_DIRS = frozenset({"agent-runtime", "browser-runtime"})

# Closed set listed in Plan 06 task_06_02. Test will fail if the
# catalog drifts from this list.
EXPECTED_TEMPLATES = frozenset(
    {
        "python-pytest",
        "node-jest",
        "node-vitest",
        "node-playwright",
        "php-phpunit",
        "php-pest",
        "go-test",
        "java-maven",
        "java-gradle",
        "ruby-rspec",
        "rust-cargo",
        "dotnet-test",
        "generic-shell",
        "generic-http",
    }
)


def _catalog_ids() -> frozenset[str]:
    from shared_test_runtimes.catalog import CATALOG

    return frozenset(CATALOG)


def _on_disk_runtime_dirs() -> frozenset[str]:
    return frozenset(
        p.name
        for p in DOCKERFILES_DIR.iterdir()
        if p.is_dir() and p.name not in EXCLUDED_IMAGE_DIRS
    )


def test_catalog_has_the_expected_fourteen_templates() -> None:
    assert _catalog_ids() == EXPECTED_TEMPLATES


def test_every_catalog_entry_has_a_dockerfile() -> None:
    missing = []
    for template_id in _catalog_ids():
        dockerfile = DOCKERFILES_DIR / template_id / "Dockerfile"
        if not dockerfile.is_file():
            missing.append(str(dockerfile.relative_to(REPO_ROOT)))
    assert not missing, f"Dockerfile missing for catalog entries: {missing}"


def test_every_dockerfile_has_a_catalog_entry() -> None:
    """No orphan Dockerfile dirs. If someone adds a folder under
    ``docker/agent-runtimes/`` they must register it in the catalog
    (otherwise the worker can't resolve it)."""
    catalog_ids = _catalog_ids()
    orphans = [d for d in _on_disk_runtime_dirs() if d not in catalog_ids]
    assert not orphans, f"Dockerfile dirs without a catalog entry: {orphans}"


@pytest.mark.parametrize("template_id", sorted(EXPECTED_TEMPLATES))
def test_each_dockerfile_starts_with_syntax_and_has_from(template_id: str) -> None:
    """Light syntactic sanity. We don't run ``docker build`` here —
    that's task_06_03's job in CI. But an empty Dockerfile or one that
    skipped the ``# syntax=`` directive is a regression we can catch
    cheaply."""
    dockerfile = DOCKERFILES_DIR / template_id / "Dockerfile"
    content = dockerfile.read_text(encoding="utf-8")
    first_line = content.splitlines()[0] if content else ""
    assert first_line.startswith("# syntax=docker/dockerfile:"), (
        f"{template_id}: first line must be the syntax directive, got {first_line!r}"
    )
    assert "\nFROM " in content, f"{template_id}: Dockerfile has no FROM directive"
    # WORKDIR /workspace is the contract with the catalog (every
    # template advertises workspace_mount_path="/workspace").
    assert "WORKDIR /workspace" in content, (
        f"{template_id}: Dockerfile must declare WORKDIR /workspace"
    )


def test_catalog_get_returns_known_template() -> None:
    from shared_test_runtimes.catalog import MANIFEST, get

    t = get("python-pytest")
    assert t.id == "python-pytest"
    # La referencia sale del manifiesto de release (ADR 0148), no de una
    # constante: en un repo sin release publicada es el nombre local de
    # siempre, y tras publicar lleva registry + versión + digest.
    assert t.docker_image == MANIFEST.reference("python-pytest")


def test_catalog_get_raises_keyerror_on_unknown() -> None:
    from shared_test_runtimes.catalog import get

    with pytest.raises(KeyError, match="unknown runtime template"):
        get("brainfuck-tap")


def test_list_ids_returns_insertion_order() -> None:
    from shared_test_runtimes.catalog import list_ids

    ids = list_ids()
    assert len(ids) == 14
    # First three: python, then node-jest/vitest. Mostly to pin that
    # we're not accidentally re-sorting the catalog alphabetically.
    assert ids[0] == "python-pytest"
    assert ids[1] == "node-jest"


def test_every_image_reference_names_its_own_template() -> None:
    """Ninguna plantilla apunta a la imagen de otra.

    Es el error de copia-pega que nadie ve: los tests de un proyecto node
    corriendo dentro de la imagen de PHP fallan por «falta el intérprete», no
    por «la plantilla está mal cableada», y se investiga el proyecto.
    """
    from shared_test_runtimes.catalog import CATALOG
    from shared_test_runtimes.images import IMAGE_PREFIX, split_reference

    for tid, template in CATALOG.items():
        repo, _, _ = split_reference(template.docker_image)
        assert repo.endswith(f"{IMAGE_PREFIX}{tid}"), (
            f"{tid}: su docker_image apunta a otro repositorio ({template.docker_image!r})"
        )


def test_image_references_carry_version_and_digest_once_published() -> None:
    """Publicada la release, TODA entrada se resuelve por digest (ADR 0148).

    El tag versionado viaja además del digest, igual que en los ``FROM`` de los
    Dockerfiles: sin él nadie sabe qué versión corre ni Dependabot puede
    proponer la siguiente. Mientras no haya release, la referencia es el nombre
    local que construye `scripts/dev/build-runtime-templates.sh` — y eso es lo
    que se afirma, para que el día del salto esta guarda hable.
    """
    from shared_test_runtimes.catalog import CATALOG, MANIFEST
    from shared_test_runtimes.images import split_reference

    for tid, template in CATALOG.items():
        _, tag, digest = split_reference(template.docker_image)
        assert tag == MANIFEST.version, f"{tid}: tag {tag!r} ≠ versión del manifiesto"
        if MANIFEST.is_pinned:
            assert digest == MANIFEST.digest_for(tid), f"{tid}: digest fuera del manifiesto"
            assert template.is_pinned
        else:
            assert digest is None, f"{tid}: digest sin release publicada que lo respalde"


def test_no_digest_is_hardcoded_in_the_catalog_source() -> None:
    """Condición 1 del ADR 0148: el digest lo escribe el pipeline, no una mano.

    Un `@sha256:` tecleado en `catalog.py` no lo refresca nadie —Dependabot
    parsea Dockerfiles y ficheros compose, no fuentes Python— y sería la
    congelación de CVEs del riesgo 3 de `prod-11`, encima en las imágenes donde
    corre el código no confiable.
    """
    source = (
        REPO_ROOT
        / "packages"
        / "shared-test-runtimes"
        / "src"
        / "shared_test_runtimes"
        / "catalog.py"
    ).read_text(encoding="utf-8")
    assert "sha256:" not in source, (
        "hay un digest escrito a mano en catalog.py: debe salir del manifiesto "
        "de release (runtime_images.json), que reescribe el pipeline"
    )
