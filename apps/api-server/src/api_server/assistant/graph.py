"""Assistant tool-calling sub-graph (Plan 10 task_10_14).

A LangGraph workflow that drives ONE turn of the personal-assistant
chat. The flow is the classic tool-use loop:

    decide ──(tool_calls?)──> run_tools ──> decide ──> ... ──> answer

The PM-style ``AssistantModelClient`` seam keeps the LLM out of tests:
the integration test drives the graph with a ``ScriptedAssistantModel``
that returns a fixed sequence of tool-call rounds then a final answer,
exactly like Plan 03's ``ScriptedPlanningModel``. The real adapter
(future wiring) plugs ``shared_llm.LLMProvider`` (ADR 0021) behind the
same surface — the graph never imports a provider.

Tools execute through ``run_assistant_tool`` against the request's
RLS-bound session, so tenant isolation is enforced by the database.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from langgraph.graph import END, START, StateGraph

from api_server.assistant.tools import AssistantToolContext, run_assistant_tool

# A node is async because the tool round awaits DB queries.
AssistantNode = Callable[["AssistantState"], Awaitable["AssistantState"]]

# Hard ceiling on tool rounds so a misbehaving model can't loop forever.
MAX_TOOL_ROUNDS = 6


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolInvocation:
    """One tool the model asked the host to run this round."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelTurn:
    """What the model produced on one ``decide`` step.

    Exactly one of two outcomes:
      * ``tool_calls`` non-empty -> the host runs them and loops back.
      * ``content`` set, ``tool_calls`` empty -> the final answer.
    """

    content: str | None = None
    tool_calls: tuple[ToolInvocation, ...] = ()


@dataclass(frozen=True)
class AssistantTurnResult:
    """The synthesised answer produced for one full sub-graph pass."""

    content: str
    # Names of tools that were actually invoked, in call order — lets the
    # endpoint/test assert "the read tools were used".
    tools_called: tuple[str, ...]
    rounds: int


# ---------------------------------------------------------------------------
# Model-side protocol + scripted test double
# ---------------------------------------------------------------------------
@runtime_checkable
class AssistantModelClient(Protocol):
    """LLM seam used by the assistant sub-graph.

    ``decide`` receives the full state (system prompt, chat history, the
    tool results accumulated so far) and returns either tool calls or a
    final answer. Kept a single method so a scripted test client can
    replay turns without an LLM round-trip.
    """

    def decide(self, state: AssistantState) -> ModelTurn: ...


@dataclass
class ScriptedAssistantModel:
    """Replays a fixed sequence of model turns.

    ``turns`` is consumed in order. The last turn is repeated if the
    graph asks for more (defensive — a well-formed script ends with a
    content-only turn). Matches the pattern of ``ScriptedPlanningModel``.
    """

    turns: list[ModelTurn]
    _cursor: int = 0

    def decide(self, state: AssistantState) -> ModelTurn:  # noqa: ARG002
        if not self.turns:
            raise ValueError("ScriptedAssistantModel needs at least one turn")
        index = min(self._cursor, len(self.turns) - 1)
        self._cursor += 1
        return self.turns[index]


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------
@dataclass
class AssistantState:
    """State carried through the sub-graph for one turn."""

    system_prompt: str
    chat_history: list[dict[str, Any]] = field(default_factory=list)
    enabled_tools: tuple[str, ...] = ()

    # Filled by the graph as it loops.
    pending: ModelTurn | None = None
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    rounds: int = 0
    answer: str | None = None

    # Injected, not serialised into the prompt — the RLS-bound context.
    tool_ctx: AssistantToolContext | None = None


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
def _node_decide(model: AssistantModelClient) -> AssistantNode:
    async def _run(state: AssistantState) -> AssistantState:
        turn = model.decide(state)
        # Drop any tool the tenant has not enabled — a model can never
        # widen its own surface past the tenant's allow-list.
        if turn.tool_calls:
            allowed = set(state.enabled_tools)
            kept = tuple(tc for tc in turn.tool_calls if tc.name in allowed)
            turn = ModelTurn(content=turn.content, tool_calls=kept)
        state.pending = turn
        if not turn.tool_calls:
            state.answer = turn.content or ""
        return state

    return _run


def _node_run_tools() -> AssistantNode:
    async def _run(state: AssistantState) -> AssistantState:
        assert state.pending is not None
        assert state.tool_ctx is not None
        state.rounds += 1
        for call in state.pending.tool_calls:
            result = await run_assistant_tool(call.name, state.tool_ctx, call.arguments)
            state.tools_called.append(call.name)
            state.tool_results.append({"tool": call.name, "result": result})
        return state

    return _run


def _route_after_decide(state: AssistantState) -> str:
    """Loop into the tool round when the model asked for tools AND we are
    under the round ceiling; otherwise finish."""
    assert state.pending is not None
    if state.pending.tool_calls and state.rounds < MAX_TOOL_ROUNDS:
        return "run_tools"
    return "finish"


def _node_finish() -> AssistantNode:
    async def _run(state: AssistantState) -> AssistantState:
        if state.answer is None:
            # Hit the round ceiling without a content turn — degrade
            # gracefully rather than loop. The accumulated tool results
            # are still available to the caller.
            state.answer = ""
        return state

    return _run


# ---------------------------------------------------------------------------
# Build + run
# ---------------------------------------------------------------------------
def build_assistant_graph(model: AssistantModelClient) -> Any:
    graph: StateGraph[AssistantState] = StateGraph(AssistantState)
    graph.add_node("decide", _node_decide(model))
    graph.add_node("run_tools", _node_run_tools())
    graph.add_node("finish", _node_finish())

    graph.add_edge(START, "decide")
    graph.add_conditional_edges(
        "decide",
        _route_after_decide,
        {"run_tools": "run_tools", "finish": "finish"},
    )
    # After a tool round, go back to the model to decide the next step.
    graph.add_edge("run_tools", "decide")
    graph.add_edge("finish", END)

    return graph.compile()


async def run_assistant_turn(
    model: AssistantModelClient,
    *,
    system_prompt: str,
    enabled_tools: tuple[str, ...],
    tool_ctx: AssistantToolContext,
    chat_history: Sequence[dict[str, Any]] | None = None,
) -> AssistantTurnResult:
    """Build the graph, run one full turn, return the synthesised answer.

    The answer is what the endpoint persists as an ``agent`` message in
    the conversation. ``tools_called`` lets the caller assert the read
    tools were exercised.
    """
    initial = AssistantState(
        system_prompt=system_prompt,
        chat_history=list(chat_history or []),
        enabled_tools=enabled_tools,
        tool_ctx=tool_ctx,
    )
    compiled = build_assistant_graph(model)
    final = await compiled.ainvoke(initial)
    final_state = AssistantState(**final) if isinstance(final, dict) else final
    return AssistantTurnResult(
        content=final_state.answer or "",
        tools_called=tuple(final_state.tools_called),
        rounds=final_state.rounds,
    )


__all__ = [
    "MAX_TOOL_ROUNDS",
    "AssistantModelClient",
    "AssistantState",
    "AssistantTurnResult",
    "ModelTurn",
    "ScriptedAssistantModel",
    "ToolInvocation",
    "build_assistant_graph",
    "run_assistant_turn",
]
