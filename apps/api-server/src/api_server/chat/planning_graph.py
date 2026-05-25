"""Multi-agent planning sub-graph (Plan 03 task_03_09).

A LangGraph workflow that drives ONE turn of the planning chat. The
project manager (PM) speaks for the team by default; specialists
(architect, backend_dev, qa, ...) only chime in when the topic warrants
their input. The output is a single synthesised assistant message that
ends up persisted as a ``agent`` message in the conversation.

Why a graph and not a plain loop:
  * The team's behaviour is rule-driven (who speaks when), and the
    state we carry forward (pertinence picks, specialist drafts,
    synthesis) maps cleanly to graph nodes.
  * It keeps the planning flow consistent with the agent-runtime loop
    (same dependency, same shape — easier to share UX patterns later).

The model side is abstracted behind `PlanningModelClient` so the
integration test can drive the graph with a `ScriptedPlanningModel`
that returns predetermined turns and pertinence picks. No real LLM
is touched in tests.
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from langgraph.graph import END, START, StateGraph

PlanningNode = Callable[["PlanningState"], "PlanningState"]


# ---------------------------------------------------------------------------
# Roles allowed to speak in the planning sub-graph
# ---------------------------------------------------------------------------
class PlanningRole(enum.StrEnum):
    """Subset of `AgentRole` that the planning sub-graph admits as
    *spokespersons*. Other roles can be present in the team but are
    not invoked by name (the PM may still cite them in the synthesis).
    """

    PROJECT_MANAGER = "project_manager"
    ARCHITECT = "architect"
    BACKEND_DEV = "backend_dev"
    FRONTEND_DEV = "frontend_dev"
    QA = "qa"
    REVIEWER = "reviewer"
    DEVOPS = "devops"
    SECURITY = "security"
    TECHNICAL_WRITER = "technical_writer"


class PMIntent(enum.StrEnum):
    """What the PM has decided to do this turn.

    - ``speak_alone``      : PM answers directly, no specialist input.
    - ``invite_specialists``: PM yields the floor to one or more specialists,
                              then synthesises their contributions.
    - ``ask_user``         : The team needs information from the human; the
                              synthesised turn ends with a question.
    - ``finish_planning``  : Plan is ready to be formalised — the chat UI
                              now shows the "Generar Plan" button.
    """

    SPEAK_ALONE = "speak_alone"
    INVITE_SPECIALISTS = "invite_specialists"
    ASK_USER = "ask_user"
    FINISH_PLANNING = "finish_planning"


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PMDirective:
    """Outcome of the PM's reasoning step."""

    intent: PMIntent
    rationale: str = ""
    # Only consulted when intent=INVITE_SPECIALISTS. Roles listed here
    # must be present in the team config that was fed into the graph.
    specialists: tuple[PlanningRole, ...] = ()


@dataclass(frozen=True)
class SpecialistContribution:
    """One specialist's turn — what the role said and any usage info."""

    role: PlanningRole
    content: str
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass(frozen=True)
class PlanningTurnResult:
    """The synthesised message produced for one full sub-graph pass."""

    speaker_role: PlanningRole
    content: str
    intent: PMIntent
    contributions: tuple[SpecialistContribution, ...]


# ---------------------------------------------------------------------------
# Model-side protocol
# ---------------------------------------------------------------------------
@runtime_checkable
class PlanningModelClient(Protocol):
    """LLM seam used by the planning sub-graph.

    Three calls — kept separate so a scripted test client can drive
    the graph turn-by-turn without an LLM round-trip. The real
    implementation (Plan 04 wiring) plugs an adapter over
    `shared_llm.LLMProvider` (ADR 0021) behind the same surface.
    """

    def pm_decide(self, state: PlanningState) -> PMDirective: ...

    def specialist_speak(
        self, role: PlanningRole, state: PlanningState
    ) -> SpecialistContribution: ...

    def pm_synthesise(
        self,
        state: PlanningState,
        contributions: Sequence[SpecialistContribution],
    ) -> str: ...


# ---------------------------------------------------------------------------
# Scripted model — what the integration tests drive
# ---------------------------------------------------------------------------
@dataclass
class ScriptedPlanningModel:
    """Replays a fixed sequence of PM directives + specialist lines.

    `directives` and `synthesis` are consumed in order, with the last
    value repeated if exhausted (matches the pattern used by the
    agent-runtime's `ScriptedModelClient`). `specialist_voice` maps
    a role -> static contribution body.
    """

    directives: list[PMDirective]
    synthesis: list[str]
    specialist_voice: dict[PlanningRole, str] = field(default_factory=dict)
    _decide_cursor: int = 0
    _synth_cursor: int = 0

    def pm_decide(self, state: PlanningState) -> PMDirective:  # noqa: ARG002
        if not self.directives:
            raise ValueError("ScriptedPlanningModel needs at least one directive")
        index = min(self._decide_cursor, len(self.directives) - 1)
        self._decide_cursor += 1
        return self.directives[index]

    def specialist_speak(
        self,
        role: PlanningRole,
        state: PlanningState,  # noqa: ARG002
    ) -> SpecialistContribution:
        body = self.specialist_voice.get(role, f"[{role.value}] sin opinión")
        return SpecialistContribution(role=role, content=body)

    def pm_synthesise(
        self,
        state: PlanningState,  # noqa: ARG002
        contributions: Sequence[SpecialistContribution],  # noqa: ARG002
    ) -> str:
        if not self.synthesis:
            raise ValueError("ScriptedPlanningModel needs at least one synthesis line")
        index = min(self._synth_cursor, len(self.synthesis) - 1)
        self._synth_cursor += 1
        return self.synthesis[index]


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------
@dataclass
class PlanningState:
    """State carried through the sub-graph for one turn.

    Inputs:
      - ``chat_history`` is the (already-compressed) tail of the
        conversation — a list of {role, content} dicts.
      - ``project_context`` is whatever `task_03_10` builds: kanban,
        prior plans, KBs, project config.
      - ``team_roles`` lists the PlanningRoles available; specialists
        the PM asks for must be present here.

    Outputs (filled by the graph):
      - ``directive``       : PM's decision.
      - ``contributions``   : specialist turns (in invocation order).
      - ``synthesised``     : the final message body the PM produced.
    """

    chat_history: list[dict[str, Any]] = field(default_factory=list)
    project_context: dict[str, Any] = field(default_factory=dict)
    team_roles: frozenset[PlanningRole] = field(default_factory=frozenset)

    directive: PMDirective | None = None
    contributions: list[SpecialistContribution] = field(default_factory=list)
    synthesised: str | None = None


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------
def _node_pm_decide(model: PlanningModelClient) -> PlanningNode:
    def _run(state: PlanningState) -> PlanningState:
        state.directive = model.pm_decide(state)
        # Drop any specialists the PM mentioned but the team does not
        # include — silently ignored so a typo cannot derail a turn.
        if state.directive.intent == PMIntent.INVITE_SPECIALISTS:
            kept = tuple(s for s in state.directive.specialists if s in state.team_roles)
            state.directive = PMDirective(
                intent=state.directive.intent,
                rationale=state.directive.rationale,
                specialists=kept,
            )
        return state

    return _run


def _node_specialists(model: PlanningModelClient) -> PlanningNode:
    def _run(state: PlanningState) -> PlanningState:
        assert state.directive is not None
        for role in state.directive.specialists:
            state.contributions.append(model.specialist_speak(role, state))
        return state

    return _run


def _node_synthesise(model: PlanningModelClient) -> PlanningNode:
    def _run(state: PlanningState) -> PlanningState:
        state.synthesised = model.pm_synthesise(state, state.contributions)
        return state

    return _run


def _route_after_pm(state: PlanningState) -> str:
    """If PM wants specialists *and* at least one matches the team,
    invite them. Otherwise jump straight to synthesise."""
    assert state.directive is not None
    if state.directive.intent == PMIntent.INVITE_SPECIALISTS and state.directive.specialists:
        return "specialists"
    return "synthesise"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def build_planning_graph(model: PlanningModelClient) -> Any:
    """Compile the planning sub-graph.

    Returns a compiled LangGraph runnable whose `.invoke(state)` runs
    one full turn: PM decision → optional specialist round → synthesis.
    """
    graph: StateGraph[PlanningState] = StateGraph(PlanningState)
    graph.add_node("pm_decide", _node_pm_decide(model))
    graph.add_node("specialists", _node_specialists(model))
    graph.add_node("synthesise", _node_synthesise(model))

    graph.add_edge(START, "pm_decide")
    graph.add_conditional_edges(
        "pm_decide",
        _route_after_pm,
        {"specialists": "specialists", "synthesise": "synthesise"},
    )
    graph.add_edge("specialists", "synthesise")
    graph.add_edge("synthesise", END)

    return graph.compile()


def run_planning_turn(
    model: PlanningModelClient,
    *,
    chat_history: list[dict[str, Any]] | None = None,
    project_context: dict[str, Any] | None = None,
    team_roles: frozenset[PlanningRole] | None = None,
) -> PlanningTurnResult:
    """Convenience wrapper — builds the graph, invokes it, returns the
    high-level result the chat endpoint persists as one ``agent`` message.

    The default team is just the PM (which is the only required role —
    specialists are opt-in by the tenant's team config).
    """
    initial = PlanningState(
        chat_history=list(chat_history or []),
        project_context=dict(project_context or {}),
        team_roles=team_roles or frozenset({PlanningRole.PROJECT_MANAGER}),
    )
    compiled = build_planning_graph(model)
    final = compiled.invoke(initial)
    # LangGraph returns either the dataclass we put in OR a dict view
    # depending on version — coerce defensively.
    final_state = PlanningState(**final) if isinstance(final, dict) else final
    assert final_state.directive is not None
    assert final_state.synthesised is not None
    return PlanningTurnResult(
        speaker_role=PlanningRole.PROJECT_MANAGER,
        content=final_state.synthesised,
        intent=final_state.directive.intent,
        contributions=tuple(final_state.contributions),
    )


__all__ = [
    "PMDirective",
    "PMIntent",
    "PlanningModelClient",
    "PlanningRole",
    "PlanningState",
    "PlanningTurnResult",
    "ScriptedPlanningModel",
    "SpecialistContribution",
    "build_planning_graph",
    "run_planning_turn",
]
