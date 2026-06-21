"""Unit tests for the project-chat responder helpers (Plan 04 wiring)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from api_server.chat.planning_graph import PlanningRole
from api_server.chat.responder import history_from_messages, planning_roles_from_strings


def _msg(author_kind: str, content: str) -> Any:
    return SimpleNamespace(author_kind=author_kind, content=content)


def test_history_maps_author_kind_to_llm_role() -> None:
    out = history_from_messages(
        [
            _msg("user", "hola"),
            _msg("agent", "hola, equipo"),
            _msg("system", "modo cambiado"),
            _msg("weird", "x"),  # unknown kind → user (safe default)
        ]
    )
    assert out == [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "hola, equipo"},
        {"role": "system", "content": "modo cambiado"},
        {"role": "user", "content": "x"},
    ]


def test_history_empty() -> None:
    assert history_from_messages([]) == []


def test_planning_roles_maps_known_drops_unknown_always_pm() -> None:
    roles = planning_roles_from_strings(["architect", "qa", "researcher", "backend_dev"])
    assert PlanningRole.PROJECT_MANAGER in roles  # always present
    assert PlanningRole.ARCHITECT in roles
    assert PlanningRole.QA in roles
    assert PlanningRole.BACKEND_DEV in roles
    # "researcher" is not a planning spokesperson role → dropped
    assert all(r != "researcher" for r in roles)


def test_planning_roles_empty_team_is_pm_only() -> None:
    assert planning_roles_from_strings([]) == frozenset({PlanningRole.PROJECT_MANAGER})
