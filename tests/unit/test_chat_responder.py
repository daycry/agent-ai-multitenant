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
    """Drives _stream_planning without an LLM: PM invites 2 specialists (unless given
    another intent), each speaks, PM synthesises, then drafts a structured plan."""

    def __init__(self, intent: PMIntent = PMIntent.INVITE_SPECIALISTS) -> None:
        self._intent = intent
        self.draft_calls = 0

    def pm_decide(self, state: object) -> PMDirective:
        specialists = (
            (PlanningRole.BACKEND_DEV, PlanningRole.FRONTEND_DEV)
            if self._intent == PMIntent.INVITE_SPECIALISTS
            else ()
        )
        return PMDirective(
            intent=self._intent,
            rationale="necesito backend y frontend",
            specialists=specialists,
        )

    def specialist_speak(self, role: PlanningRole, state: object) -> SpecialistContribution:
        return SpecialistContribution(role=role, content=f"opinión de {role.value}")

    def pm_synthesise(self, state: object, contributions: object) -> str:
        return "Síntesis final del PM"

    def pm_plan_draft(self, state: object, contributions: object) -> dict[str, Any]:
        self.draft_calls += 1
        return {
            "title": "Plan de prueba",
            "summary": "resumen del plan",
            "tasks": [{"id": "t1", "title": "Tarea 1", "depends_on": []}],
        }


@pytest.mark.asyncio
async def test_stream_planning_publishes_each_step_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[tuple[str, str, Any]] = []

    async def _fake_persist(**kwargs: object) -> None:
        published.append(
            (str(kwargs["author_kind"]), str(kwargs["content"]), kwargs.get("attachments"))
        )

    monkeypatch.setattr(responder, "_persist_and_publish", _fake_persist)

    model = _FakePlanningModel()
    state = PlanningState(
        team_roles=frozenset(
            {PlanningRole.PROJECT_MANAGER, PlanningRole.BACKEND_DEV, PlanningRole.FRONTEND_DEV}
        )
    )
    ok = await responder._stream_planning(
        model=model,  # type: ignore[arg-type]
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
    assert [kind for kind, _, _ in published] == ["agent", "agent", "agent", "agent"]
    assert "Backend" in published[0][1] and "Frontend" in published[0][1]  # framing names both
    assert "opinión de backend_dev" in published[1][1]
    assert "opinión de frontend_dev" in published[2][1]
    assert published[3][1] == "Síntesis final del PM"
    # The synthesis message carries the finish_planning attachment so the UI can offer
    # "Generar Plan" — even though the PM intent was INVITE_SPECIALISTS, not FINISH_PLANNING.
    synth_attachments = published[3][2]
    assert model.draft_calls == 1
    assert synth_attachments and synth_attachments[0]["kind"] == "planning_directive"
    assert synth_attachments[0]["intent"] == "finish_planning"
    assert synth_attachments[0]["specification"]["tasks"]


@pytest.mark.asyncio
async def test_stream_planning_ask_user_does_not_draft_a_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ASK_USER means the PM is asking the user a question, not presenting a plan — so no
    structured draft is produced and no "Generar Plan" button appears."""
    published: list[tuple[str, str, Any]] = []

    async def _fake_persist(**kwargs: object) -> None:
        published.append(
            (str(kwargs["author_kind"]), str(kwargs["content"]), kwargs.get("attachments"))
        )

    monkeypatch.setattr(responder, "_persist_and_publish", _fake_persist)

    model = _FakePlanningModel(intent=PMIntent.ASK_USER)
    state = PlanningState(team_roles=frozenset({PlanningRole.PROJECT_MANAGER}))
    ok = await responder._stream_planning(
        model=model,  # type: ignore[arg-type]
        state=state,
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        mode="planning",
        redis=None,  # type: ignore[arg-type]
        default_agent_id=uuid4(),
        role_agents={},
    )
    assert ok is True
    assert model.draft_calls == 0  # never drafted a plan
    assert all(att is None for _, _, att in published)  # no finish_planning attachment


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
