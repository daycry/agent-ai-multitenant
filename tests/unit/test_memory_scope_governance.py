"""Unit: resolución de scope efectivo (equipo→agente→plataforma) y enrutado por
tipo (semantic viaja, episodic se acota a project_shared) — ADR 0071."""

from __future__ import annotations

import pytest
from api_server.memorizer.policy import resolve_effective_memory_scope, route_scope_for_type

pytestmark = pytest.mark.unit


def test_effective_scope_team_wins() -> None:
    assert resolve_effective_memory_scope("team_shared", "private", "private") == "team_shared"


def test_effective_scope_falls_to_agent_without_team() -> None:
    assert resolve_effective_memory_scope(None, "global", "private") == "global"


def test_effective_scope_falls_to_platform_default() -> None:
    assert resolve_effective_memory_scope(None, None, "private") == "private"


@pytest.mark.parametrize(
    ("effective", "expected_semantic", "expected_episodic"),
    [
        ("global", "global", "project_shared"),
        ("team_shared", "team_shared", "project_shared"),
        ("project_shared", "project_shared", "project_shared"),
        ("private", "private", "private"),
    ],
)
def test_routing_by_type(effective: str, expected_semantic: str, expected_episodic: str) -> None:
    assert route_scope_for_type(effective, "semantic") == expected_semantic
    assert route_scope_for_type(effective, "episodic") == expected_episodic


def test_unknown_type_is_treated_as_episodic() -> None:
    # Tipo ausente/desconocido → se acota como episodic (nunca sobre-comparte).
    assert route_scope_for_type("global", None) == "project_shared"
    assert route_scope_for_type("team_shared", "weird") == "project_shared"
    assert route_scope_for_type("private", None) == "private"
