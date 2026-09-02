"""La escalera de lectura y el enrutado de escritura de memorias siguen al ADR 0071
(`task_cv_30`, E-01 y `task_cv_31`, E-02).

Auditoría 2026-09-01. **Lectura**: `_default_readable_scopes` usaba el orden
viejo (`team_shared` más estrecho que `project_shared`) sobre el scope crudo del
agente: un agente `project_shared` nunca leía `team_shared` y uno `global` sólo
leía `global`. Lo que un agente de IA lee es «todo scope compartido con
puntero»: `global`, el `project_shared` de su proyecto efectivo y el
`team_shared` del equipo de ese proyecto (los punteros los pone el endpoint, no
el cliente). **Escritura**: la tool `memory_store` persistía con
`agent.memory_scope` crudo mientras el memorizer aplica la política del equipo
(`resolve_effective_memory_scope`) y el enrutado por tipo
(`route_scope_for_type`); ahora los dos caminos calculan el scope igual.
"""

from __future__ import annotations

import pytest
from api_server.routers.internal_agent import _default_readable_scopes, _store_scope_for

pytestmark = pytest.mark.unit

_SHARED = {"team_shared", "project_shared", "global"}


@pytest.mark.parametrize("agent_scope", ["team_shared", "project_shared", "global"])
def test_an_ai_agent_reads_every_shared_scope_whatever_its_write_scope(agent_scope: str) -> None:
    assert set(_default_readable_scopes(agent_scope)) == _SHARED


def test_a_project_shared_agent_reads_its_teams_memory() -> None:
    assert "team_shared" in _default_readable_scopes("project_shared")


def test_a_global_agent_reads_the_project_and_team_of_the_task() -> None:
    readable = _default_readable_scopes("global")
    assert "project_shared" in readable and "team_shared" in readable


def test_a_private_agent_keeps_its_private_rows_on_top() -> None:
    readable = _default_readable_scopes("private")
    assert readable[0] == "private" and set(readable[1:]) == _SHARED


def test_an_unknown_scope_falls_back_to_global_only() -> None:
    assert _default_readable_scopes("bogus") == ["global"]


# ------------------------------------------------------------------ escritura


def test_an_episodic_memory_of_a_global_agent_lands_in_the_project() -> None:
    assert (
        _store_scope_for(
            agent_scope="global",
            team_scope=None,
            platform_default="project_shared",
            mem_type="episodic",
        )
        == "project_shared"
    )


def test_a_semantic_memory_travels_to_the_effective_scope() -> None:
    assert (
        _store_scope_for(
            agent_scope="global",
            team_scope=None,
            platform_default="project_shared",
            mem_type="semantic",
        )
        == "global"
    )


def test_the_team_policy_wins_over_the_agent_scope() -> None:
    assert (
        _store_scope_for(
            agent_scope="global",
            team_scope="team_shared",
            platform_default="project_shared",
            mem_type="semantic",
        )
        == "team_shared"
    )


def test_without_team_nor_agent_scope_the_platform_default_applies() -> None:
    assert (
        _store_scope_for(
            agent_scope=None,
            team_scope=None,
            platform_default="project_shared",
            mem_type="semantic",
        )
        == "project_shared"
    )
