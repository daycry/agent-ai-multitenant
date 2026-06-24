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

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

from langgraph.graph import END, START, StateGraph

from api_server.assistant.tools import AssistantToolContext, run_assistant_tool

# A node is async because the tool round awaits DB queries.
AssistantNode = Callable[["AssistantState"], Awaitable["AssistantState"]]

# How the graph executes ONE tool call: ``(name, tool_ctx, arguments) -> result``.
# Defaults to the assistant's :func:`run_assistant_tool`; the córtex (Plan F1)
# reuses this very graph with its own runner (``cortex.tools.run_cortex_tool``)
# so the loop, caps and convergence logic are shared, not duplicated.
ToolRunner = Callable[[str, Any, dict[str, Any]], Awaitable[dict[str, Any]]]

# Hard ceiling on tool rounds so a misbehaving model can't loop forever.
MAX_TOOL_ROUNDS = 6
# Backstop on how many times ONE tool may run in a single turn. The signature
# dedup below stops a model re-calling a tool with IDENTICAL args, but an
# over-eager model can re-call the SAME tool with slightly DIFFERENT args (e.g.
# saving the same fact reworded several times). This caps that runaway per tool
# name — defence in depth on top of the prompt guidance.
MAX_CALLS_PER_TOOL = 3
# Per-tool overrides of the cap. The memory WRITE tool is special: with the
# claude_sdk provider each round is a stateless SDK query, so an over-eager model
# re-decides to "remember" the user's fact every round (reworded, so the exact
# signature dedup misses it). A single user message should yield AT MOST ONE
# memory write — the model is told to fold several facts into one call — so we
# hard-cap it to 1/turn. This is the deterministic guarantee on top of the prompt.
# ``cortex_remember`` (Plan F1) is the córtex's memory WRITE tool and shares the
# exact same 1/turn guarantee — it reuses this graph, so it reuses this cap.
_PER_TOOL_CALL_CAP: dict[str, int] = {"remember_about_me": 1, "cortex_remember": 1}


def _tool_call_cap(name: str) -> int:
    """Max times tool ``name`` may run in one turn (write tool capped tighter)."""
    return _PER_TOOL_CALL_CAP.get(name, MAX_CALLS_PER_TOOL)


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
    final answer. It is **async** so the real adapter can ``await`` the
    provider on the request's event loop (no cross-loop bridging); the
    scripted test double just returns its next turn.
    """

    async def decide(self, state: AssistantState) -> ModelTurn: ...


@dataclass
class ScriptedAssistantModel:
    """Replays a fixed sequence of model turns.

    ``turns`` is consumed in order. The last turn is repeated if the
    graph asks for more (defensive — a well-formed script ends with a
    content-only turn). Matches the pattern of ``ScriptedPlanningModel``.
    """

    turns: list[ModelTurn]
    _cursor: int = 0

    async def decide(self, state: AssistantState) -> ModelTurn:  # noqa: ARG002
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
    # The latest non-empty content the model produced this turn — used as the
    # answer when the loop ends without a fresh content turn.
    last_content: str | None = None
    # Signatures (name+args) of tool calls already executed this turn, so an
    # over-eager model re-calling the SAME tool doesn't loop (a weak/reasoning
    # model otherwise repeats the same call until the round ceiling).
    executed_signatures: set[str] = field(default_factory=set)

    # Injected, not serialised into the prompt — the RLS-bound context.
    tool_ctx: AssistantToolContext | None = None


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
def _signature(call: ToolInvocation) -> str:
    """Stable identity of a tool call (name + arguments) used to detect a model
    re-calling the exact same tool, so the loop converges instead of repeating
    it until the round ceiling."""
    return f"{call.name}|{json.dumps(call.arguments, sort_keys=True, default=str)}"


def _admissible_tool_calls(
    state: AssistantState, calls: tuple[ToolInvocation, ...]
) -> tuple[ToolInvocation, ...]:
    """Tool calls from one ``decide`` round the host will actually run: (a) enabled
    for the tenant, (b) not already executed this turn (same name+args), and (c) under
    the per-tool cap — counting BOTH prior rounds (``state.tools_called``) AND calls
    already kept within THIS round. Counting the current round is what stops an
    over-eager model from exceeding the cap in a single round (e.g. emitting several
    ``remember_about_me`` with distinct args), which ``state.tools_called`` alone — only
    updated AFTER the round in ``run_tools`` — would miss."""
    allowed = set(state.enabled_tools)
    kept: list[ToolInvocation] = []
    round_counts: dict[str, int] = {}
    for tc in calls:
        if tc.name not in allowed:
            continue
        if _signature(tc) in state.executed_signatures:
            continue
        used = state.tools_called.count(tc.name) + round_counts.get(tc.name, 0)
        if used >= _tool_call_cap(tc.name):
            continue
        kept.append(tc)
        round_counts[tc.name] = round_counts.get(tc.name, 0) + 1
    return tuple(kept)


def _node_decide(model: AssistantModelClient) -> AssistantNode:
    async def _run(state: AssistantState) -> AssistantState:
        turn = await model.decide(state)
        if turn.content:
            state.last_content = turn.content
        # Filter to the calls the host will actually run (enabled, not already
        # executed, under the per-tool cap incl. this round) — see _admissible_tool_calls.
        kept = _admissible_tool_calls(state, turn.tool_calls)
        state.pending = ModelTurn(content=turn.content, tool_calls=kept)
        if not kept:
            # No new work to do → this is the answer (the model's content, or
            # the latest content it produced earlier this turn).
            state.answer = turn.content or state.last_content or ""
        return state

    return _run


def _node_run_tools(tool_runner: ToolRunner) -> AssistantNode:
    async def _run(state: AssistantState) -> AssistantState:
        assert state.pending is not None
        assert state.tool_ctx is not None
        state.rounds += 1
        for call in state.pending.tool_calls:
            state.executed_signatures.add(_signature(call))
            result = await tool_runner(call.name, state.tool_ctx, call.arguments)
            state.tools_called.append(call.name)
            state.tool_results.append({"tool": call.name, "result": result})
        return state

    return _run


def _route_after_decide(state: AssistantState) -> str:
    """Loop into the tool round when the model asked for NEW tools AND we are
    under the round ceiling; otherwise finish."""
    assert state.pending is not None
    if state.pending.tool_calls and state.rounds < MAX_TOOL_ROUNDS:
        return "run_tools"
    return "finish"


def _node_finish(model: AssistantModelClient) -> AssistantNode:
    async def _run(state: AssistantState) -> AssistantState:
        if state.answer:
            return state
        if state.last_content:
            state.answer = state.last_content
            return state
        # The model kept calling tools without ever answering. Ask once more
        # with NO tools available so it MUST produce a textual answer, grounded
        # on the tool results gathered so far.
        final = replace(state, enabled_tools=(), pending=None)
        turn = await model.decide(final)
        state.answer = turn.content or ""
        return state

    return _run


# ---------------------------------------------------------------------------
# Build + run
# ---------------------------------------------------------------------------
def build_assistant_graph(
    model: AssistantModelClient,
    *,
    state_type: type = AssistantState,
    tool_runner: ToolRunner = run_assistant_tool,
) -> Any:
    """Compile the one-turn tool-use loop.

    ``state_type`` / ``tool_runner`` are the two seams the córtex (Plan F1) reuses
    to drive the SAME loop with its own state subclass and tool runner — no fork
    of the convergence/cap logic. The defaults keep the assistant behaviour
    identical."""
    graph: StateGraph[Any] = StateGraph(state_type)
    graph.add_node("decide", _node_decide(model))
    graph.add_node("run_tools", _node_run_tools(tool_runner))
    graph.add_node("finish", _node_finish(model))

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
