"""Allowlist del `registry-proxy` (ADR 0094).

El `registry-proxy` es una segunda instancia de tinyproxy (`FilterDefaultDeny`,
`FilterExtended`) dedicada a que los runtime-templates resuelvan los registries
de paquetes (composer/pip/npm/go/nuget/maven/gradle/ruby/cargo) y los git hosts
públicos. Su allowlist (`docker/registry-proxy/filter.txt`) es un regex ERE por
línea, anclado al `Host`/CONNECT.

Este test fija el CONTRATO de la allowlist sin levantar el proxy: cada registry
del catálogo casa, y los hosts que NO deben salir (LLM providers, hosts internos
del compose, IPs internas, dominios arbitrarios) no casan ninguna regla.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_FILTER = Path(__file__).resolve().parents[2] / "docker" / "registry-proxy" / "filter.txt"

# Cada ecosistema del catálogo de runtime-templates (packages/shared-test-runtimes)
# resuelve contra estos hosts en frío. TODOS deben pasar la allowlist.
_ALLOWED = [
    # PHP / composer
    "packagist.org",
    "repo.packagist.org",
    "getcomposer.org",
    # Python / pip
    "pypi.org",
    "files.pythonhosted.org",
    # Node / npm
    "registry.npmjs.org",
    # Go
    "proxy.golang.org",
    "sum.golang.org",
    "storage.googleapis.com",
    # Java
    "repo.maven.apache.org",
    "repo1.maven.org",
    "plugins.gradle.org",
    "services.gradle.org",
    # Ruby
    "rubygems.org",
    "index.rubygems.org",
    # Rust
    "crates.io",
    "static.crates.io",
    "index.crates.io",
    # .NET
    "api.nuget.org",
    # Git hosts + dist/archive CDNs
    "github.com",
    "codeload.github.com",
    "api.github.com",  # composer/go dist zipballs
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
    "gitlab.com",
    "dev.azure.com",
    "bitbucket.org",
]

# Lo que NUNCA debe salir por el registry-proxy: endpoints LLM (los sirve el
# egress-proxy, superficie disjunta — D3), hosts internos del compose, IPs
# internas/metadata, y dominios de exfiltración arbitrarios.
_DENIED = [
    "evil.example.com",
    "api.anthropic.com",
    "api.githubcopilot.com",
    "ollama.com",
    "169.254.169.254",
    "10.0.0.5",
    "vault",
    "api-server",
    "postgres",
    "redis",
    "localhost",
    "evilgithub.com",
    "evil-nuget.org",
    "github.com.evil.com",
]


def _patterns() -> list[re.Pattern[str]]:
    lines = _FILTER.read_text(encoding="utf-8").splitlines()
    pats: list[re.Pattern[str]] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pats.append(re.compile(line))
    return pats


def _allowed_by_filter(host: str, pats: list[re.Pattern[str]]) -> bool:
    # tinyproxy con FilterExtended usa regexec (búsqueda); las reglas se anclan
    # con ^...$ en el propio fichero. Reproducimos con re.search.
    return any(p.search(host) for p in pats)


def test_filter_file_exists_and_is_non_empty() -> None:
    pats = _patterns()
    assert pats, "registry-proxy filter.txt has no regex rules"


@pytest.mark.parametrize("host", _ALLOWED)
def test_registry_hosts_are_allowed(host: str) -> None:
    pats = _patterns()
    assert _allowed_by_filter(host, pats), f"{host} should be allowed by the registry-proxy"


@pytest.mark.parametrize("host", _DENIED)
def test_non_registry_hosts_are_denied(host: str) -> None:
    pats = _patterns()
    assert not _allowed_by_filter(host, pats), f"{host} must NOT be allowed by the registry-proxy"
