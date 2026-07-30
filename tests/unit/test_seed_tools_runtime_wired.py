"""PROJ-08/F3 (auditoría proyecto 2026-07-17): los seeds solo asignan tools
que el runtime puede EJECUTAR.

`apply-patch`, `search-code` y `summarize-text` se sembraban en
ROLE_DEFAULT_TOOLS y en el equipo CI4 pero no están cableadas en el runtime
(RUNTIME_WIRED_TOOL_NAMES): el agente las veía en su prompt, las invocaba y
fallaban siempre — iteraciones quemadas en cada run.
"""

from __future__ import annotations

import pytest
from api_server.seeds.builtin_role_capabilities import ROLE_DEFAULT_TOOLS
from api_server.seeds.ci4_team import CI4_AGENTS
from shared_domain.tool_names import is_runtime_wired

pytestmark = pytest.mark.unit


def _wired(slug: str) -> bool:
    # Los seeds usan slugs kebab (`read-file`); el catálogo canónico usa snake
    # (`read_file`) — la misma normalización que hace schemas.catalog.
    return is_runtime_wired(slug.replace("-", "_"))


def test_role_default_tools_are_all_runtime_wired() -> None:
    for role, tools in ROLE_DEFAULT_TOOLS.items():
        dead = sorted(t for t in tools if not _wired(t))
        assert not dead, f"ROLE_DEFAULT_TOOLS[{role!r}] siembra tools sin cablear: {dead}"


def test_ci4_agents_tools_are_all_runtime_wired() -> None:
    for agent in CI4_AGENTS:
        dead = sorted(t for t in agent.tool_slugs if not _wired(t))
        assert not dead, f"CI4 agent {agent.slug!r} siembra tools sin cablear: {dead}"
