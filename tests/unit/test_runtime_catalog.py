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
    assert first_line.startswith(
        "# syntax=docker/dockerfile:"
    ), f"{template_id}: first line must be the syntax directive, got {first_line!r}"
    assert "\nFROM " in content, f"{template_id}: Dockerfile has no FROM directive"
    # WORKDIR /workspace is the contract with the catalog (every
    # template advertises workspace_mount_path="/workspace").
    assert (
        "WORKDIR /workspace" in content
    ), f"{template_id}: Dockerfile must declare WORKDIR /workspace"


def test_catalog_get_returns_known_template() -> None:
    from shared_test_runtimes.catalog import get

    t = get("python-pytest")
    assert t.id == "python-pytest"
    assert t.docker_image == "agent-runtime-python-pytest:v1"


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


def test_template_image_refs_use_v1_tag() -> None:
    """We launch task_06_02 with the ``v1`` tag for every template;
    task_06_03's CI also publishes ``v1``. If someone bumps the tag in
    the catalog they must update the CI workflow too — this test
    flags the drift."""
    from shared_test_runtimes.catalog import CATALOG

    for tid, template in CATALOG.items():
        assert template.docker_image.endswith(
            ":v1"
        ), f"{tid}: docker_image must end with ':v1', got {template.docker_image!r}"
