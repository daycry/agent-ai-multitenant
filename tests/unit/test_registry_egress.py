"""Egress de runtime-templates a registries vía registry-proxy (ADR 0094).

Cubre el cableado (settings + spec) y el mecanismo de red (attach/detach del
proxy, inyección de env) del puente que da a los runtime-templates egress
allowlisted para resolver sus dependencias.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# --- settings + spec defaults (cableado) -----------------------------------


def test_settings_expose_registry_proxy_defaults() -> None:
    from workers.config import Settings

    s = Settings()
    assert s.registry_proxy_url == "http://registry-proxy:8888"
    assert s.registry_proxy_container == "agentic-registry-proxy"
    assert s.registry_proxy_alias == "registry-proxy"
    # el host del alias debe coincidir con el host de la URL (lo usa el runtime
    # para encontrar el proxy en su bridge).
    assert s.registry_proxy_alias in s.registry_proxy_url


def test_spec_dep_egress_defaults_false() -> None:
    from shared_test_runtimes.types import RuntimeTemplate
    from workers.test_runtime import RuntimePlan, TestRuntimeSpec

    plan = RuntimePlan(
        template=RuntimeTemplate(id="php-phpunit", docker_image="img:v1"),
        checks=(),
    )
    spec = TestRuntimeSpec(plan=plan, worktree_host_path="/data/wt")
    assert spec.dep_egress is False


# --- catálogo: alineación cache_env ↔ dep_cache_mount (ADR 0094) -----------


def test_every_dep_installing_template_has_cache_env() -> None:
    """Cada plantilla que instala deps (tiene dep_cache_mount + pre_install) debe
    alinear su caché para que el cache caliente reduzca egress (ADR 0094)."""
    from shared_test_runtimes.catalog import CATALOG

    for t in CATALOG.values():
        if t.dep_cache_mount and t.default_pre_install:
            assert t.cache_env, f"{t.id} tiene dep_cache_mount pero no cache_env"


def test_cache_env_values_target_the_dep_cache_mount() -> None:
    """Cada cache_env apunta al dep_cache_mount: el mount está EN/BAJO algún valor
    (igualdad, prefijo —home que contiene el cache— o substring —flag con la ruta)."""
    from shared_test_runtimes.catalog import CATALOG

    for t in CATALOG.values():
        if not t.cache_env:
            continue
        assert t.dep_cache_mount, f"{t.id}: cache_env sin dep_cache_mount"
        mount = t.dep_cache_mount
        values = [v for _k, v in t.cache_env]
        assert any(
            mount == v or mount.startswith(v) or mount in v for v in values
        ), f"{t.id}: ningún cache_env apunta a dep_cache_mount {mount!r}: {values}"
