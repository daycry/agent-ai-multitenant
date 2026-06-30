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
