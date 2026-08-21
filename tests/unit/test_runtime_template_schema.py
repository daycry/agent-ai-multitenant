"""Unit tests for the RuntimeTemplate schema (Plan 06 task_06_01).

The schema lives in :mod:`shared_test_runtimes.types`. These tests
pin the contract:

  * Required fields (``id``, ``docker_image``) are enforced.
  * ``id`` must be kebab-case.
  * Absolute-path invariants on ``workspace_mount_path`` and
    ``dep_cache_mount``.
  * ``output_parsers`` cannot be empty and cannot have duplicates.
  * :class:`Resources` rejects non-positive ``cpu``/``memory_mb``.
  * Defaults are sane (``workspace_mount_path='/workspace'``,
    ``network_policy='none'``, ``dep_cache_mount=None``).
  * Frozen — assigning to a field raises FrozenInstanceError.

No I/O; this whole suite runs in <50ms.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def _import_module() -> SimpleNamespace:
    """Lazy import so a failed package install surfaces as a test
    error rather than a collection error (cleaner CI signal)."""
    from shared_test_runtimes.types import Resources, RuntimeTemplate

    return SimpleNamespace(Resources=Resources, RuntimeTemplate=RuntimeTemplate)


def test_minimal_construct_succeeds() -> None:
    mod = _import_module()
    t = mod.RuntimeTemplate(
        id="python-pytest",
        docker_image="ghcr.io/agent-ai/agent-runtime-python-pytest:v1",
    )
    assert t.id == "python-pytest"
    assert t.docker_image.endswith(":v1")
    assert t.workspace_mount_path == "/workspace"
    assert t.dep_cache_mount is None
    assert t.default_pre_install == ()
    assert t.output_parsers == ("raw_text",)
    assert t.network_policy == "none"
    assert t.default_resources.cpu == 1.0
    assert t.default_resources.memory_mb == 1024


def test_id_must_be_kebab_case() -> None:
    mod = _import_module()
    with pytest.raises(ValueError, match="kebab-case"):
        mod.RuntimeTemplate(id="Python-Pytest", docker_image="img:v1")
    with pytest.raises(ValueError, match="kebab-case"):
        mod.RuntimeTemplate(id="python pytest", docker_image="img:v1")


def test_id_cannot_be_empty() -> None:
    mod = _import_module()
    with pytest.raises(ValueError, match="non-empty slug"):
        mod.RuntimeTemplate(id="", docker_image="img:v1")
    with pytest.raises(ValueError, match="non-empty slug"):
        mod.RuntimeTemplate(id="   ", docker_image="img:v1")


def test_docker_image_cannot_be_empty() -> None:
    mod = _import_module()
    with pytest.raises(ValueError, match="docker_image"):
        mod.RuntimeTemplate(id="python-pytest", docker_image="")


def test_workspace_mount_path_must_be_absolute() -> None:
    mod = _import_module()
    with pytest.raises(ValueError, match="workspace_mount_path"):
        mod.RuntimeTemplate(
            id="python-pytest",
            docker_image="img:v1",
            workspace_mount_path="workspace",
        )


def test_dep_cache_mount_must_be_absolute_when_set() -> None:
    mod = _import_module()
    with pytest.raises(ValueError, match="dep_cache_mount"):
        mod.RuntimeTemplate(
            id="python-pytest",
            docker_image="img:v1",
            dep_cache_mount="root/.cache/pip",
        )


def test_dep_cache_mount_can_be_none() -> None:
    mod = _import_module()
    t = mod.RuntimeTemplate(
        id="generic-shell",
        docker_image="img:v1",
        dep_cache_mount=None,
    )
    assert t.dep_cache_mount is None


def test_output_parsers_cannot_be_empty() -> None:
    mod = _import_module()
    with pytest.raises(ValueError, match="output_parsers"):
        mod.RuntimeTemplate(
            id="python-pytest",
            docker_image="img:v1",
            output_parsers=(),
        )


def test_output_parsers_cannot_have_duplicates() -> None:
    mod = _import_module()
    with pytest.raises(ValueError, match="duplicates"):
        mod.RuntimeTemplate(
            id="python-pytest",
            docker_image="img:v1",
            output_parsers=("junit_xml", "junit_xml"),
        )


def test_output_parsers_accepts_known_ids() -> None:
    mod = _import_module()
    t = mod.RuntimeTemplate(
        id="node-jest",
        docker_image="img:v1",
        output_parsers=("jest_json", "junit_xml", "raw_text"),
    )
    assert t.output_parsers == ("jest_json", "junit_xml", "raw_text")


def test_resources_rejects_non_positive_cpu() -> None:
    mod = _import_module()
    with pytest.raises(ValueError, match="cpu"):
        mod.Resources(cpu=0.0, memory_mb=1024)
    with pytest.raises(ValueError, match="cpu"):
        mod.Resources(cpu=-1.0, memory_mb=1024)


def test_resources_rejects_non_positive_memory() -> None:
    mod = _import_module()
    with pytest.raises(ValueError, match="memory_mb"):
        mod.Resources(cpu=1.0, memory_mb=0)
    with pytest.raises(ValueError, match="memory_mb"):
        mod.Resources(cpu=1.0, memory_mb=-512)


def test_template_is_frozen() -> None:
    mod = _import_module()
    t = mod.RuntimeTemplate(id="python-pytest", docker_image="img:v1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.id = "node-jest"  # type: ignore[misc]


def test_template_is_hashable() -> None:
    """Frozen dataclasses with hashable fields are hashable. The
    catalog will use templates as dict values keyed by id, but
    callers might also put them in sets for diff/audit purposes."""
    mod = _import_module()
    t1 = mod.RuntimeTemplate(id="python-pytest", docker_image="img:v1")
    t2 = mod.RuntimeTemplate(id="python-pytest", docker_image="img:v1")
    assert hash(t1) == hash(t2)
    assert {t1, t2} == {t1}


def test_cache_env_defaults_empty() -> None:
    mod = _import_module()
    t = mod.RuntimeTemplate(id="python-pytest", docker_image="img:v1")
    assert t.cache_env == ()


def test_cache_env_accepts_pairs_and_stays_hashable() -> None:
    """ADR 0094: cache_env alinea $HOME-relative caches con el dep_cache_mount.
    Es una tupla de pares (hashable) para preservar frozen/hashable."""
    mod = _import_module()
    t = mod.RuntimeTemplate(
        id="php-phpunit",
        docker_image="img:v1",
        dep_cache_mount="/home/agent/.composer/cache",
        cache_env=(("COMPOSER_CACHE_DIR", "/home/agent/.composer/cache"),),
    )
    assert t.cache_env == (("COMPOSER_CACHE_DIR", "/home/agent/.composer/cache"),)
    assert dict(t.cache_env) == {"COMPOSER_CACHE_DIR": "/home/agent/.composer/cache"}
    # sigue hashable (par de tuplas)
    assert {t} == {t}


def test_network_policy_accepts_registries() -> None:
    """ADR 0094: nueva política proxificada para resolver registries."""
    mod = _import_module()
    t = mod.RuntimeTemplate(
        id="php-phpunit",
        docker_image="img:v1",
        network_policy="registries",
    )
    assert t.network_policy == "registries"


def test_full_template_with_all_fields() -> None:
    """End-to-end shape: every field set to a non-default value to
    catch any constructor bug that only surfaces with the full
    payload."""
    mod = _import_module()
    t = mod.RuntimeTemplate(
        id="php-phpunit",
        docker_image="ghcr.io/agent-ai/agent-runtime-php-phpunit:v1.2.3",
        workspace_mount_path="/srv/app",
        dep_cache_mount="/home/agent/.composer/cache",
        default_pre_install=("composer install --no-dev",),
        default_resources=mod.Resources(cpu=2.0, memory_mb=2048),
        output_parsers=("junit_xml", "raw_text"),
        network_policy="restricted",
    )
    assert t.workspace_mount_path == "/srv/app"
    assert t.dep_cache_mount == "/home/agent/.composer/cache"
    assert t.default_pre_install == ("composer install --no-dev",)
    assert t.default_resources.cpu == 2.0
    assert t.default_resources.memory_mb == 2048
    assert t.output_parsers == ("junit_xml", "raw_text")
    assert t.network_policy == "restricted"


# ---------------------------------------------------------------------------
# ADR 0148 — el digest, derivado de la referencia
# ---------------------------------------------------------------------------


def test_digest_is_derived_from_the_reference() -> None:
    """``digest`` no es un campo propio: se lee de ``docker_image``.

    Guardarlo aparte permitiría que discrepen, y la forma de conseguirlo está a
    una línea de distancia: ``dataclasses.replace(template, docker_image=…)`` es
    como el ADR 0129 inyecta la imagen propia de un proyecto. Un `digest`
    heredado apuntando a otra imagen es peor que no tener digest, porque
    responde con seguridad a la pregunta que el ADR 0148 vino a contestar — y
    responde mal.
    """
    mod = _import_module()
    digest = "sha256:" + "ab" * 32
    t = mod.RuntimeTemplate(
        id="php-phpunit",
        docker_image=f"ghcr.io/agentic-platform/agent-runtime-php-phpunit:v1@{digest}",
    )
    assert t.digest == digest
    assert t.is_pinned is True

    propia = dataclasses.replace(t, docker_image="proyecto-x-runtime:latest")
    assert propia.digest is None
    assert propia.is_pinned is False


def test_a_reference_without_digest_is_not_pinned() -> None:
    mod = _import_module()
    t = mod.RuntimeTemplate(id="php-phpunit", docker_image="agent-runtime-php-phpunit:v1")
    assert t.digest is None
    assert t.is_pinned is False


def test_a_malformed_digest_is_rejected_at_construction() -> None:
    """Un digest truncado o de otro algoritmo no lo resuelve ningún daemon.

    Descubrirlo al construir el catálogo (al importar) es barato; descubrirlo
    en el `docker pull` de una tarea de un tenant, no.
    """
    mod = _import_module()
    with pytest.raises(ValueError, match="digest"):
        mod.RuntimeTemplate(id="php-phpunit", docker_image="agent-runtime-php-phpunit:v1@sha256:ab")
