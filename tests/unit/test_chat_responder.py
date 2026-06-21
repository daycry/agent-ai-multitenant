"""Unit tests for the project-chat responder helpers (Plan 04 wiring)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from api_server.chat import responder
from api_server.chat.planning_graph import (
    PlanningRole,
    PlanningState,
    PMDirective,
    PMIntent,
    SpecialistContribution,
)
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


class _FakePlanningModel:
    """Drives _stream_planning without an LLM: PM invites 2 specialists, each speaks,
    PM synthesises."""

    def pm_decide(self, state: object) -> PMDirective:
        return PMDirective(
            intent=PMIntent.INVITE_SPECIALISTS,
            rationale="necesito backend y frontend",
            specialists=(PlanningRole.BACKEND_DEV, PlanningRole.FRONTEND_DEV),
        )

    def specialist_speak(self, role: PlanningRole, state: object) -> SpecialistContribution:
        return SpecialistContribution(role=role, content=f"opinión de {role.value}")

    def pm_synthesise(self, state: object, contributions: object) -> str:
        return "Síntesis final del PM"


@pytest.mark.asyncio
async def test_stream_planning_publishes_each_step_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[tuple[str, str]] = []

    async def _fake_persist(**kwargs: object) -> None:
        published.append((str(kwargs["author_kind"]), str(kwargs["content"])))

    monkeypatch.setattr(responder, "_persist_and_publish", _fake_persist)

    state = PlanningState(
        team_roles=frozenset(
            {PlanningRole.PROJECT_MANAGER, PlanningRole.BACKEND_DEV, PlanningRole.FRONTEND_DEV}
        )
    )
    ok = await responder._stream_planning(
        model=_FakePlanningModel(),  # type: ignore[arg-type]
        state=state,
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        mode="planning",
        redis=None,  # type: ignore[arg-type]
        default_agent_id=uuid4(),
        role_agents={},
    )
    assert ok is True
    # PM framing + backend + frontend + synthesis = 4 streamed agent messages, in order.
    assert [kind for kind, _ in published] == ["agent", "agent", "agent", "agent"]
    assert "Backend" in published[0][1] and "Frontend" in published[0][1]  # framing names both
    assert "opinión de backend_dev" in published[1][1]
    assert "opinión de frontend_dev" in published[2][1]
    assert published[3][1] == "Síntesis final del PM"


def test_plan_summary_handles_dict_str_and_none() -> None:
    from api_server.chat.responder import _plan_summary

    # dict summary → description (then title) preferred
    assert (
        _plan_summary(
            SimpleNamespace(
                specification={"summary": {"description": "desc", "title": "t"}}, description=None
            )
        )
        == "desc"
    )
    # string summary → used verbatim
    assert (
        _plan_summary(SimpleNamespace(specification={"summary": "plano"}, description=None))
        == "plano"
    )
    # no summary → falls back to the plan description
    assert _plan_summary(SimpleNamespace(specification={}, description="fallback")) == "fallback"
