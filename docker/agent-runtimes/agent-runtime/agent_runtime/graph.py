"""The LangGraph agent loop (task_02_10).

Eight nodes — perceive → recall → plan → act → observe → reflect →
finalize → self_review — wired into a `langgraph.StateGraph`:

    perceive → recall → plan ─┬─(act)→ act → observe → reflect ─┐
                              │                                 │
                              └─(finish/abort)→ finalize → self_review
                                                                 │
                       reflect ───────────────────────→ plan ◀───┘ (loop)
                                          self_review ─(retry)→ plan
                                          self_review ─(pass)──→ END

`plan` is where the loop turns: it checks the safeguards, asks the
model for the next move, and runs loop detection. The model decides
when to finish; `self_review` may bounce the output back for another
pass, bounded by `max_review_retries`.

Dependencies (model, tools, memory recall) are injected via `AgentDeps`
so the loop is exercised offline and deterministically by the tests.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import END, START, StateGraph

from agent_runtime.loop_detection import DEFAULT_LOOP_THRESHOLD, LoopDetector
from agent_runtime.model import DecisionKind, ModelClient
from agent_runtime.safeguards import Budgets, SafeguardCode, SafeguardTracker
from agent_runtime.state import (
    STATUS_ABORTED,
    STATUS_DONE,
    AgentState,
    AgentTask,
    initial_state,
)
from agent_runtime.steps import memory_read_step, model_call_step, node_step, tool_call_step
from agent_runtime.tools import ToolRegistry, default_registry

# The eight nodes of the loop, in declaration order.
NODE_NAMES: tuple[str, ...] = (
    "perceive",
    "recall",
    "plan",
    "act",
    "observe",
    "reflect",
    "finalize",
    "self_review",
)


def _no_recall(_task: AgentTask) -> list[dict[str, Any]]:
    """Default memory recall — empty until real memory lands in Plan 04."""
    return []


@dataclass
class AgentDeps:
    """Everything the loop needs from the outside world."""

    model: ModelClient
    tools: ToolRegistry = field(default_factory=default_registry)
    recall: Callable[[AgentTask], list[dict[str, Any]]] = _no_recall


@dataclass(frozen=True)
class ExecutionResult:
    """The outcome of one agent run — the substrate of an `executions` row."""

    status: str
    abort_code: str | None
    output: str | None
    iterations: int
    steps: list[dict[str, Any]]
    usage: dict[str, float | int]

    def succeeded(self) -> bool:
        return self.status == STATUS_DONE

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe summary — the steps are streamed separately."""
        return {
            "status": self.status,
            "abort_code": self.abort_code,
            "output": self.output,
            "iterations": self.iterations,
            "usage": self.usage,
        }


# ---------------------------------------------------------------------------
# Conditional-edge routers — pure functions of the state.
# ---------------------------------------------------------------------------
def _route_after_plan(state: AgentState) -> str:
    if state["status"] == STATUS_ABORTED:
        return "finalize"
    decision = state["last_decision"]
    if decision is not None and decision["kind"] == str(DecisionKind.FINISH):
        return "finalize"
    return "act"


def _route_after_reflect(state: AgentState) -> str:
    return "finalize" if state["status"] == STATUS_ABORTED else "plan"


def _route_after_review(state: AgentState) -> str:
    if state["review_passed"] or state["status"] == STATUS_ABORTED:
        return "end"
    return "retry"


class _AgentLoop:
    """Builds the compiled graph; its methods are the graph nodes."""

    def __init__(self, deps: AgentDeps, tracker: SafeguardTracker, detector: LoopDetector) -> None:
        self.deps = deps
        self.tracker = tracker
        self.detector = detector

    # -- nodes ---------------------------------------------------------------
    @staticmethod
    def perceive(state: AgentState) -> dict[str, Any]:
        """Read the task and seed the working context."""
        task = state["task"]
        context = {
            "role": "task",
            "title": task["title"],
            "description": task.get("description", ""),
        }
        step = node_step(len(state["steps"]), "perceive", f"Perceived task: {task['title']}")
        return {"context": [context], "steps": [step]}

    def recall(self, state: AgentState) -> dict[str, Any]:
        """Pull relevant memory (placeholder until Plan 04)."""
        task = state["task"]
        hits = list(self.deps.recall(task))
        context = [{"role": "memory", **hit} for hit in hits]
        step = memory_read_step(
            len(state["steps"]),
            "recall",
            query=task["title"],
            hits=len(hits),
            summary=f"Recalled {len(hits)} memory item(s) — placeholder until Plan 04",
        )
        return {"context": context, "steps": [step]}

    def plan(self, state: AgentState) -> dict[str, Any]:
        """Check safeguards, ask the model for the next move, detect loops."""
        base = len(state["steps"])
        steps: list[dict[str, Any]] = []

        # Iteration budget — checked before this turn is counted, so the
        # reported iteration count never exceeds max_iterations.
        if self.tracker.iteration_exhausted():
            steps.append(
                node_step(
                    base,
                    "plan",
                    f"Safeguard tripped: {SafeguardCode.MAX_ITERATIONS}",
                    status="aborted",
                )
            )
            return {
                "status": STATUS_ABORTED,
                "abort_code": str(SafeguardCode.MAX_ITERATIONS),
                "iteration": self.tracker.usage.iterations,
                "steps": steps,
            }

        self.tracker.tick_iteration()
        tripped = self.tracker.check()
        if tripped is not None:
            steps.append(node_step(base, "plan", f"Safeguard tripped: {tripped}", status="aborted"))
            return {
                "status": STATUS_ABORTED,
                "abort_code": str(tripped),
                "iteration": self.tracker.usage.iterations,
                "steps": steps,
            }

        response = self.deps.model.decide(dict(state))
        self.tracker.record_model_call(response.tokens_in, response.tokens_out, response.cost_usd)
        decision = response.decision
        steps.append(
            model_call_step(
                base + len(steps),
                "plan",
                model=response.model,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                cost_usd=response.cost_usd,
                summary=decision.rationale or f"decision: {decision.kind}",
            )
        )

        if decision.kind == DecisionKind.ACT:
            action = {"tool": decision.tool, "args": decision.tool_args}
            if self.detector.record(action):
                steps.append(
                    node_step(
                        base + len(steps),
                        "plan",
                        f"Repetitive loop detected on tool '{decision.tool}'",
                        status="aborted",
                    )
                )
                return {
                    "status": STATUS_ABORTED,
                    "abort_code": str(SafeguardCode.REPETITIVE_LOOP),
                    "last_decision": decision.as_dict(),
                    "iteration": self.tracker.usage.iterations,
                    "steps": steps,
                }

        return {
            "last_decision": decision.as_dict(),
            "iteration": self.tracker.usage.iterations,
            "steps": steps,
        }

    def act(self, state: AgentState) -> dict[str, Any]:
        """Run the tool the model chose."""
        decision = state["last_decision"] or {}
        tool = decision.get("tool") or "noop"
        args = decision.get("tool_args") or {}
        result = self.deps.tools.call(tool, args)
        self.tracker.record_tool_call()
        step = tool_call_step(
            len(state["steps"]),
            "act",
            tool=tool,
            args=args,
            result=result.as_dict(),
            status="ok" if result.ok else "error",
            summary=f"Tool '{tool}' → {'ok' if result.ok else 'error'}",
        )
        observation = {
            "tool": tool,
            "ok": result.ok,
            "output": result.output,
            "error": result.error,
        }
        return {"last_observation": observation, "steps": [step]}

    @staticmethod
    def observe(state: AgentState) -> dict[str, Any]:
        """Fold the tool result into the working context."""
        observation = state["last_observation"] or {}
        context = {"role": "observation", **observation}
        step = node_step(
            len(state["steps"]),
            "observe",
            f"Observed result of '{observation.get('tool', '?')}'",
        )
        return {"context": [context], "steps": [step]}

    @staticmethod
    def reflect(state: AgentState) -> dict[str, Any]:
        """Note progress before the next planning turn."""
        observation = state["last_observation"] or {}
        note = (
            "tool succeeded — continuing"
            if observation.get("ok")
            else "tool failed — will reconsider"
        )
        step = node_step(len(state["steps"]), "reflect", f"Reflection: {note}")
        return {"reflections": [note], "steps": [step]}

    @staticmethod
    def finalize(state: AgentState) -> dict[str, Any]:
        """Produce the final output (or the abort summary)."""
        base = len(state["steps"])
        if state["status"] == STATUS_ABORTED:
            output = state["output"] or f"Execution aborted ({state['abort_code']})."
            step = node_step(
                base,
                "finalize",
                f"Finalized aborted execution ({state['abort_code']})",
                status="aborted",
            )
            return {"output": output, "steps": [step]}
        decision = state["last_decision"] or {}
        output = decision.get("output") or "(no output produced)"
        return {"output": output, "steps": [node_step(base, "finalize", "Finalized output")]}

    def self_review(self, state: AgentState) -> dict[str, Any]:
        """Review the output; pass, or bounce it back bounded by retries."""
        base = len(state["steps"])
        steps: list[dict[str, Any]] = []

        if state["status"] == STATUS_ABORTED:
            steps.append(
                node_step(
                    base, "self_review", "Skipped review — execution aborted", status="aborted"
                )
            )
            return {"review_passed": False, "steps": steps}

        review = self.deps.model.review(dict(state))
        self.tracker.record_model_call(review.tokens_in, review.tokens_out, review.cost_usd)
        steps.append(
            model_call_step(
                base + len(steps),
                "self_review",
                model=review.model,
                tokens_in=review.tokens_in,
                tokens_out=review.tokens_out,
                cost_usd=review.cost_usd,
                summary=f"Self-review: {'pass' if review.passed else 'fail'}",
            )
        )

        if review.passed:
            steps.append(
                node_step(base + len(steps), "self_review", "Output approved by self-review")
            )
            return {"review_passed": True, "status": STATUS_DONE, "steps": steps}

        retries = state["review_retries"] + 1
        budget = self.tracker.budgets.max_review_retries
        if retries > budget:
            steps.append(
                node_step(
                    base + len(steps),
                    "self_review",
                    "Self-review failed — retry budget exhausted",
                    status="aborted",
                )
            )
            return {
                "review_passed": False,
                "status": STATUS_ABORTED,
                "abort_code": str(SafeguardCode.MAX_REVIEW_RETRIES),
                "review_retries": retries,
                "steps": steps,
            }

        steps.append(
            node_step(
                base + len(steps),
                "self_review",
                f"Self-review failed — retrying ({retries}/{budget})",
            )
        )
        return {
            "review_passed": False,
            "review_retries": retries,
            "context": [{"role": "review_feedback", "feedback": review.feedback}],
            "steps": steps,
        }

    # -- wiring --------------------------------------------------------------
    def build(self) -> Any:
        """Wire the nodes and edges into a compiled LangGraph graph."""
        graph: StateGraph = StateGraph(AgentState)
        graph.add_node("perceive", self.perceive)
        graph.add_node("recall", self.recall)
        graph.add_node("plan", self.plan)
        graph.add_node("act", self.act)
        graph.add_node("observe", self.observe)
        graph.add_node("reflect", self.reflect)
        graph.add_node("finalize", self.finalize)
        graph.add_node("self_review", self.self_review)

        graph.add_edge(START, "perceive")
        graph.add_edge("perceive", "recall")
        graph.add_edge("recall", "plan")
        graph.add_conditional_edges(
            "plan", _route_after_plan, {"act": "act", "finalize": "finalize"}
        )
        graph.add_edge("act", "observe")
        graph.add_edge("observe", "reflect")
        graph.add_conditional_edges(
            "reflect", _route_after_reflect, {"plan": "plan", "finalize": "finalize"}
        )
        graph.add_edge("finalize", "self_review")
        graph.add_conditional_edges(
            "self_review", _route_after_review, {"retry": "plan", "end": END}
        )
        return graph.compile()


def build_agent_graph(
    deps: AgentDeps,
    *,
    tracker: SafeguardTracker | None = None,
    detector: LoopDetector | None = None,
) -> Any:
    """Compile the agent loop graph. `tracker`/`detector` are per-run —
    `run_agent` supplies fresh ones; callers building the graph directly
    (the graph-shape tests) may let this default them."""
    tracker = tracker or SafeguardTracker(Budgets())
    detector = detector or LoopDetector()
    return _AgentLoop(deps, tracker, detector).build()


def run_agent(
    deps: AgentDeps,
    task: AgentTask,
    *,
    budgets: Budgets | None = None,
    loop_threshold: int = DEFAULT_LOOP_THRESHOLD,
    clock: Callable[[], float] | None = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> ExecutionResult:
    """Run one execution of the agent loop end to end.

    `on_step`, when given, is called with each step the moment it is
    produced — the graph is streamed node by node, so a live consumer
    (the agent-runtime entrypoint, task_02_29) sees steps as they
    happen rather than only at the end.
    """
    budgets = budgets or Budgets()
    tracker = SafeguardTracker(budgets, clock=clock or time.monotonic)
    detector = LoopDetector(threshold=loop_threshold)
    graph = build_agent_graph(deps, tracker=tracker, detector=detector)

    # LangGraph trips its own recursion guard after N super-steps; size it
    # well above the worst case our own safeguards would allow.
    recursion_limit = (budgets.max_iterations + budgets.max_review_retries + 2) * 8 + 100
    config = {"recursion_limit": recursion_limit}

    # Stream the full state after every super-step: the last one is the
    # final state, and the growing `steps` list feeds `on_step` live.
    final: AgentState = initial_state(task)
    emitted = 0
    for state in graph.stream(final, stream_mode="values", config=config):
        final = state
        if on_step is not None:
            steps = state["steps"]
            for step in steps[emitted:]:
                on_step(step)
            emitted = len(steps)
    return ExecutionResult(
        status=final["status"],
        abort_code=final["abort_code"],
        output=final["output"],
        iterations=tracker.usage.iterations,
        steps=final["steps"],
        usage=tracker.usage.as_dict(),
    )
