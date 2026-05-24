"""Integration tests for the multi-agent planning sub-graph
(Plan 03 task_03_09).

These tests drive the LangGraph sub-graph end-to-end with a scripted
model — no real LLM, no DB. They live under tests/integration/ because
the sub-graph is a *piece of integration logic* between the chat
endpoint and the model layer, not a pure unit; we want them grouped
with the rest of the chat integration suite.

What we check:

  - One turn: PM decides, specialists chime in when invited, PM
    synthesises a final message.
  - PM-only turn: no specialists invited -> contributions list empty,
    synthesised message produced by the PM alone.
  - Pertinence guard: if PM lists a specialist not present in the
    team, the graph silently drops it (cannot derail a turn).
  - Intent forwarded to the result so the caller can flip on the
    "Generar Plan" button when intent=finish_planning.
"""

from __future__ import annotations

import pytest
from api_server.chat.planning_graph import (
    PlanningRole,
    PlanningState,
    PMDirective,
    PMIntent,
    ScriptedPlanningModel,
    build_planning_graph,
    run_planning_turn,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _full_team() -> frozenset[PlanningRole]:
    return frozenset(
        {
            PlanningRole.PROJECT_MANAGER,
            PlanningRole.ARCHITECT,
            PlanningRole.BACKEND_DEV,
            PlanningRole.QA,
            PlanningRole.SECURITY,
        }
    )


# ===========================================================================
# Tests
# ===========================================================================
def test_pm_invites_specialists_and_synthesises_their_input() -> None:
    """PM hands off to architect + qa; their contributions are captured
    in order; PM synthesises a unified message; intent is propagated."""
    model = ScriptedPlanningModel(
        directives=[
            PMDirective(
                intent=PMIntent.INVITE_SPECIALISTS,
                rationale="topic touches design and acceptance criteria",
                specialists=(PlanningRole.ARCHITECT, PlanningRole.QA),
            )
        ],
        synthesis=["Equipo: empezamos con arquitectura JWT y los criterios de QA."],
        specialist_voice={
            PlanningRole.ARCHITECT: "Propongo JWT con rotación semanal.",
            PlanningRole.QA: "Cubriremos auth-fail con un test de integración.",
        },
    )

    result = run_planning_turn(
        model,
        chat_history=[{"role": "user", "content": "Necesito auth JWT"}],
        project_context={"project_name": "Inventory API"},
        team_roles=_full_team(),
    )

    assert result.intent == PMIntent.INVITE_SPECIALISTS
    assert "arquitectura JWT" in result.content

    # Specialists invoked in the order the PM listed them.
    assert [c.role for c in result.contributions] == [
        PlanningRole.ARCHITECT,
        PlanningRole.QA,
    ]
    assert "rotación semanal" in result.contributions[0].content
    assert "auth-fail" in result.contributions[1].content


def test_pm_can_speak_alone_without_specialist_chime_ins() -> None:
    """When PM picks SPEAK_ALONE, no specialist nodes run."""
    model = ScriptedPlanningModel(
        directives=[PMDirective(intent=PMIntent.SPEAK_ALONE, rationale="trivial question")],
        synthesis=["Sí, el plan ya cubre ese caso."],
    )

    result = run_planning_turn(
        model,
        chat_history=[{"role": "user", "content": "Tenemos que cubrir 404?"}],
        team_roles=_full_team(),
    )

    assert result.intent == PMIntent.SPEAK_ALONE
    assert result.contributions == ()
    assert result.content == "Sí, el plan ya cubre ese caso."


def test_specialist_not_in_team_is_silently_dropped() -> None:
    """A typo or stale specialist reference cannot break a turn — the
    graph filters out roles the team does not include before invoking
    anyone, so the synthesise node always runs."""
    model = ScriptedPlanningModel(
        directives=[
            PMDirective(
                intent=PMIntent.INVITE_SPECIALISTS,
                specialists=(
                    PlanningRole.ARCHITECT,
                    PlanningRole.SECURITY,  # NOT in team_roles below
                ),
            )
        ],
        synthesis=["Tenemos lo necesario."],
        specialist_voice={
            PlanningRole.ARCHITECT: "Subimos a TLS 1.3.",
        },
    )

    result = run_planning_turn(
        model,
        team_roles=frozenset({PlanningRole.PROJECT_MANAGER, PlanningRole.ARCHITECT}),
    )

    # Security was dropped; only architect contributed.
    assert [c.role for c in result.contributions] == [PlanningRole.ARCHITECT]
    assert "TLS 1.3" in result.contributions[0].content
    assert result.content == "Tenemos lo necesario."


def test_finish_planning_intent_round_trips_to_caller() -> None:
    """When the PM decides the plan is ready, the synthesised message
    still goes through and the intent is exposed so the chat UI can
    flip on the 'Generar Plan' button."""
    model = ScriptedPlanningModel(
        directives=[
            PMDirective(
                intent=PMIntent.FINISH_PLANNING,
                rationale="all sections covered",
            )
        ],
        synthesis=[
            "Creo que ya tenemos el plan completo — pulsa 'Generar Plan'.",
        ],
    )

    result = run_planning_turn(
        model,
        team_roles=_full_team(),
    )

    assert result.intent == PMIntent.FINISH_PLANNING
    assert "Generar Plan" in result.content
    assert result.contributions == ()


def test_state_threading_passes_chat_history_and_project_context() -> None:
    """Smoke-test that the StateGraph actually carries the inputs we
    feed into it (a regression here would silently degrade prompts)."""
    captured: dict[str, PlanningState] = {}

    class _Capturer(ScriptedPlanningModel):
        def pm_decide(self, state: PlanningState) -> PMDirective:
            captured["pm"] = state
            return super().pm_decide(state)

    model = _Capturer(
        directives=[PMDirective(intent=PMIntent.SPEAK_ALONE)],
        synthesis=["ok"],
    )
    graph = build_planning_graph(model)

    initial = PlanningState(
        chat_history=[{"role": "user", "content": "hello"}],
        project_context={"project_id": "abc", "kanban_tasks": 3},
        team_roles=frozenset({PlanningRole.PROJECT_MANAGER}),
    )
    graph.invoke(initial)

    seen = captured["pm"]
    assert seen.chat_history[0]["content"] == "hello"
    assert seen.project_context["project_id"] == "abc"
    assert PlanningRole.PROJECT_MANAGER in seen.team_roles
