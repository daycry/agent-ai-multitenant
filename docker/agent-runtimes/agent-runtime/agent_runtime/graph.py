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

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from shared_llm import LLMError

from agent_runtime.approval import ApprovalGate
from agent_runtime.loop_detection import DEFAULT_LOOP_THRESHOLD, LoopDetector
from agent_runtime.model import DecisionKind, ModelClient
from agent_runtime.providers import ProviderTimeout
from agent_runtime.safeguards import Budgets, SafeguardCode, SafeguardTracker
from agent_runtime.state import (
    STATUS_ABORTED,
    STATUS_AWAITING_APPROVAL,
    STATUS_DONE,
    STATUS_NEEDS_HUMAN_REVIEW,
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


# Read/search tools that gather context but produce no deliverable. A run that
# only calls these is researching, not making progress.
_RESEARCH_TOOLS = frozenset({"list_files", "read_file", "memory_recall", "rag_search"})
# Tools that produce/modify the deliverable — calling one means real progress
# (and that the agent HAS produced, which changes the nudge from "write" to "finish").
_PRODUCING_TOOLS = frozenset(
    {"write_file", "edit_file", "create_file", "shell_exec", "stack_exec", "apply_patch"}
)
# After this many research-only tool calls in a row, push the agent off research.
_RESEARCH_STREAK_LIMIT = 5
# ADR 0089-D4: after this many research-only calls in a row (read-churn), trip a HARD
# backstop — the soft nudge fires at 5 but a model can ignore it and re-read to
# max_iterations. 10 is 2x the nudge (one last chance to converge) and far below the
# per-kind iteration cap (25/50), so it fails fast instead of burning the budget.
_RESEARCH_HARD_LIMIT = 10
# ADR 0089: after this many writes to the SAME path (ANY content), nudge the agent to
# stop re-writing and FINISH — a 'churn' the byte-exact detector/nudge cannot see.
_PATH_CHURN_THRESHOLD = 4


def _base_tool_name(tool: str | None) -> str:
    """The tool name without its MCP/custom namespace (``filesystem.write_file`` →
    ``write_file``).

    Audit cluster C2 (F24): production/research classification matched bare
    builtin names only, so a file written via an MCP server (``fs.write_file``)
    or a namespaced custom tool was invisible — ``has_produced`` never latched and
    the self-review saw no code, escalating a run that DID produce. Stripping the
    namespace lets the same writer verbs count whatever wires them.
    """
    return (tool or "").rsplit(".", 1)[-1]


def _is_research_tool(tool: str | None) -> bool:
    return _base_tool_name(tool) in _RESEARCH_TOOLS


def _is_producing_tool(tool: str | None) -> bool:
    return _base_tool_name(tool) in _PRODUCING_TOOLS


# Read-only / idempotent tools EXEMPT from the hard repetitive-loop abort (Tema C):
# repeating them wastes turns but cannot corrupt the deliverable, so they only earn
# the repetition nudge (B1) and are bounded by max_iterations/wall_clock — never a
# hard abort. The research/inspection tools are the read-only allowlist.
_READONLY_TOOLS = _RESEARCH_TOOLS


def _is_readonly_tool(tool: str | None) -> bool:
    return _base_tool_name(tool) in _READONLY_TOOLS


def _is_mutating_tool(tool: str | None) -> bool:
    """Whether repeating ``tool`` could change the deliverable (Tema C).

    A MUTATOR (a producing tool, OR any unknown/unclassified verb — conservative by
    default, e.g. ``echo``) trips the hard repetitive-loop abort/escalation; a known
    READ-ONLY tool (``_READONLY_TOOLS``) does NOT — repeating it merely wastes turns,
    which the iteration / wall-clock budgets already bound, so it gets the B1 nudge
    instead. Defaulting unknowns to MUTATING preserves the existing hard-abort
    guarantee (a runaway writer — or any non-read-only verb — is always caught).
    """
    return not _is_readonly_tool(tool)


def _abort_or_escalate_status(has_produced: bool, *, is_review: bool = False) -> str:
    """The terminal status for a budget/loop trip, gated by whether work exists.

    ADR 0087 (B2/B3): a run that has ALREADY produced a deliverable must not be
    discarded as a hard ``aborted`` failure when a safeguard trips — its work is
    preserved and the run is ESCALATED to a human (``needs_human_review``). A
    STERILE run (nothing produced) is a clean ``aborted`` as before. The abort_code
    is unchanged in either case; only the lifecycle status differs.

    ADR 0095: a REVIEW run is sterile by design (it produces a verdict, not a
    file), so a safeguard trip there ESCALATES to a human (the worker converges
    the task) instead of a silent hard abort that parks it in ``in_review``.
    """
    if is_review or has_produced:
        return STATUS_NEEDS_HUMAN_REVIEW
    return STATUS_ABORTED


def _repetition_nudge(
    *, tool: str | None, repeat_count: int, threshold: int, has_produced: bool
) -> str | None:
    """Warn the agent one turn BEFORE the loop detector aborts a repeated action.

    The detector aborts on the ``(threshold + 1)``-th IDENTICAL action (same tool +
    same args → same bytes). This fires earlier, once ``repeat_count >= threshold``,
    so the agent gets a chance to break the rut itself. Returns ``None`` until the
    action has repeated enough to warn. The wording branches by tool CLASS:

      * a MUTATING tool (``write_file``/``edit_file``/…) — the bytes are already
        saved; repeating writes nothing new, so the fix is to apply the review
        feedback or FINISH via ``submit_result``, not to re-write;
      * a READ-ONLY / verification tool — the result is already in hand; reuse it
        instead of re-running the identical query.
    """
    if tool is None or repeat_count < threshold:
        return None
    name = _base_tool_name(tool) or "that tool"
    if _is_mutating_tool(tool):
        finish_hint = (
            "apply the REVIEW FEEDBACK or FINISH now by calling submit_result"
            if has_produced
            else "apply the REVIEW FEEDBACK or move on to a DIFFERENT step"
        )
        return (
            f"You have written the SAME bytes with '{name}' {repeat_count} times — it "
            f"is already saved and repeating it changes nothing. Stop repeating: {finish_hint}."
        )
    return (
        f"You have already run '{name}' with these exact arguments {repeat_count} times — "
        "use the result you already have instead of repeating it."
    )


def _path_churn_nudge(*, path: str | None, write_count: int, threshold: int) -> str | None:
    """Advisory nudge when the agent keeps re-writing the SAME file without finishing.

    A model can churn one file with slightly DIFFERENT content each turn — never
    byte-identical, so the loop detector (content-aware fingerprint) and the
    identical-args repetition nudge both miss it, yet it burns the iteration budget
    without converging (observed: a migration re-written 17 times before MAX_ITERATIONS).
    Fires once a path has been written ``threshold`` times, pushing the agent to FINISH
    (and let the review / a human judge) or fix the SPECIFIC cross-file problem instead
    of rewriting the whole file again. Advisory only — it never aborts; the iteration /
    wall-clock budgets remain the hard ceiling.
    """
    if not path or write_count < threshold:
        return None
    return (
        f"You have re-written '{path}' {write_count} times without finishing. STOP "
        "re-writing it: either FINISH now by calling submit_result (the review / a human "
        "will judge it), or fix the SPECIFIC cross-file inconsistency the review pointed "
        "out — do NOT rewrite the whole file again."
    )


def _research_nudge(
    *,
    tool: str | None,
    research_streak: int,
    repeat_count: int,
    has_produced: bool = False,
    is_review: bool = False,
) -> str | None:
    """Guidance pushing the agent off a research rut toward the right next move.

    Triggers (the loop-detector already aborts on the 4th *identical* action; this
    nudges earlier and more gently, without killing an otherwise-fine run):

      * a research tool repeated with the SAME args (``repeat_count > 1``) — the
        agent re-listing a directory / re-running a search it already has;
      * a long research-only streak — many reads/searches with no progress.

    The DIRECTION depends on whether the agent has already produced (``has_produced``):
    if it has written the deliverable and is now re-listing/re-reading to verify, the
    fix is to FINISH (reply with a summary, no tool call) — not to write more. This is
    the over-verification trap that left a run looping until ``repetitive_loop_detected``
    even though every file was already written. Returns ``None`` when no nudge applies.
    """
    is_repeat = _is_research_tool(tool) and repeat_count > 1
    if not (is_repeat or research_streak >= _RESEARCH_STREAK_LIMIT):
        return None
    if is_review:
        # ADR 0095: a reviewer is FORBIDDEN to write_file; push it to conclude with
        # its verdict (claude_sdk FINISH = a no-tool-call prose turn) — never to
        # "produce the deliverable".
        return (
            "You have enough context to judge this task. STOP researching and FINISH "
            "your review now: reply with your final summary ending in exactly one "
            "<verdict>approve</verdict> or <verdict>reject</verdict> tag — do NOT call "
            "more tools and do NOT write files."
        )
    if has_produced:
        # C0 (ADR 0087): provider-neutral wording — do NOT prescribe "no tool call".
        # FINISH on the HTTP providers IS a `submit_result` tool call; on claude_sdk
        # it is a prose summary. Either way: report the final result and stop.
        return (
            "You have ALREADY produced the deliverable. Stop verifying/re-reading and "
            "FINISH now: report the final result and stop working."
        )
    if is_repeat:
        return (
            f"You already ran '{tool}' with these exact arguments {repeat_count} times. "
            "Do not repeat it — use the result you already have and move forward."
        )
    return (
        f"You have made {research_streak} research calls in a row without producing "
        "anything. STOP researching — you have enough context. Produce the task's "
        "deliverable now (e.g. write_file) instead of more reads or searches."
    )


def _research_exhausted(
    *,
    research_streak: int,
    has_produced: bool,
    review_retries: int,
    hard_limit: int,
    is_review: bool = False,
) -> bool:
    """The HARD read-churn backstop (ADR 0089-D4).

    Trips when the research-only streak (reads/searches since the last producing
    tool) crosses ``hard_limit`` AND the run has something worth preserving — it has
    already produced a deliverable, OR a prior self-review failed and the model keeps
    reading without re-writing (the live read-churn scenario). A sterile analysis-only
    run that legitimately only reads (no production, no failed review) is NOT cut here;
    its termination stays bounded by ``max_iterations``/``wall_clock`` (D3 invariant).

    ADR 0095: a REVIEW run is the one sterile-by-design case that MUST be cut — it
    never produces and would otherwise leak to ``max_iterations`` every time.
    """
    return research_streak >= hard_limit and (has_produced or review_retries > 0 or is_review)


def _no_recall(_task: AgentTask) -> list[dict[str, Any]]:
    """Default memory recall — empty until real memory lands in Plan 04."""
    return []


# --- review harvest: the agent's CUMULATIVE deliverable, read from the worktree ---
_WORKSPACE_ROOT_ENV = "AGENT_WORKSPACE_ROOT"
# Never part of the reviewable deliverable: VCS, framework deps, agent scratch,
# build noise. Mirrors what file_tools/list_files already hide from the agent.
_REVIEW_EXCLUDE_DIRS = frozenset(
    {".git", "vendor", "node_modules", "__pycache__", ".venv", "venv", ".claude"}
)
_REVIEW_EXCLUDE_NAMES = frozenset({"agent_task.json", ".claude.json"})
_REVIEW_EXCLUDE_SUFFIXES = (".pyc", ".lock", ".log", ".map")
# Bound the worktree scan (the review prompt caps further to _REVIEW_MAX_FILES).
_WORKTREE_SCAN_MAX_FILES = 40
_WORKTREE_SKIP_FILE_BYTES = 200_000


def _workspace_root() -> Path:
    """The worktree root the agent's file tools resolve against (``/workspace``,
    or ``AGENT_WORKSPACE_ROOT`` for tests). Mirrors ``builtin_families``."""
    return Path(os.environ.get(_WORKSPACE_ROOT_ENV) or "/workspace")


def _harvest_worktree_files(root: Path, prefer: list[str]) -> list[dict[str, str]]:
    """Read the agent's CUMULATIVE deliverable from the worktree on disk.

    The per-run write capture (``_AgentLoop.written_files``) only sees files
    written in the CURRENT run; an incremental run that builds on a prior committed
    run leaves earlier files untouched, so the self-review would judge an INCOMPLETE
    picture and reject a whole deliverable as "missing files" (observed live on a
    re-run of an escalated JWT task). Reading the worktree gives the reviewer the
    TRUE current state, and the on-disk content is the FINAL content (after every
    edit), not the write-time argument. VCS/framework dirs are excluded and the scan
    is bounded; ``prefer`` (this run's written paths) are ordered FIRST so the
    current work is always shown even when the cap truncates. Returns ``[]`` when
    there is no worktree (analysis/design runs, tests) → the caller falls back to the
    per-run capture and prose-only review is unchanged.
    """
    if not root.is_dir():
        return []
    try:
        candidates = [p for p in root.rglob("*") if p.is_file()]
    except OSError:  # pragma: no cover - defensive (permission / race)
        return []
    rels: list[str] = []
    for path in candidates:
        try:
            rel_path = path.relative_to(root)
        except ValueError:  # pragma: no cover - defensive
            continue
        if set(rel_path.parts) & _REVIEW_EXCLUDE_DIRS:
            continue
        if rel_path.name in _REVIEW_EXCLUDE_NAMES or rel_path.suffix in _REVIEW_EXCLUDE_SUFFIXES:
            continue
        rels.append(rel_path.as_posix())
    preferred = [r for r in prefer if r in rels]
    ordered = preferred + sorted(r for r in rels if r not in preferred)
    harvested: list[dict[str, str]] = []
    for rel in ordered[:_WORKTREE_SCAN_MAX_FILES]:
        file_path = root / rel
        try:
            if file_path.stat().st_size > _WORKTREE_SKIP_FILE_BYTES:
                continue
            harvested.append(
                {"path": rel, "content": file_path.read_text(encoding="utf-8", errors="replace")}
            )
        except OSError:  # pragma: no cover - defensive (binary / permission)
            continue
    return harvested


def _provider_abort_code(exc: LLMError) -> str:
    """The abort code for a provider-layer failure (F25/P1.5).

    Phase 1 (`providers.py`) already retried transient errors and re-raised a
    TYPED error once the budget was spent: :class:`ProviderTimeout` (a stuck
    call) or another ``shared_llm`` :class:`LLMError` (rate-limit / 5xx / auth).
    A timeout gets its own code so a wedged provider is distinguishable from a
    generic failure in the persisted ``abort_code``.
    """
    if isinstance(exc, ProviderTimeout):
        return str(SafeguardCode.PROVIDER_TIMEOUT)
    return str(SafeguardCode.PROVIDER_ERROR)


@dataclass
class AgentDeps:
    """Everything the loop needs from the outside world."""

    model: ModelClient
    tools: ToolRegistry = field(default_factory=default_registry)
    recall: Callable[[AgentTask], list[dict[str, Any]]] = _no_recall
    # When set, gates sensitive tool calls before they run (task_02_33).
    approval: ApprovalGate | None = None
    # ADR 0095: this run is an AI REVIEW (judges another task's output). Makes the
    # convergence safeguards reviewer-aware: the nudge tells it to emit its verdict
    # (not write_file), the read-churn backstop cuts it, and a safeguard trip
    # escalates to a human instead of a silent abort.
    is_review: bool = False


@dataclass(frozen=True)
class ExecutionResult:
    """The outcome of one agent run — the substrate of an `executions` row."""

    status: str
    abort_code: str | None
    output: str | None
    iterations: int
    steps: list[dict[str, Any]]
    usage: dict[str, float | int]
    # Set when status is `awaiting_human_approval`: {category, action}.
    approval: dict[str, Any] | None = None
    # The agent's self-reported finish status (ADR 0087): "success"|"failed"|
    # "partial" when it finished via `submit_result`, else None. A HINT for the UI
    # + reviewer, distinct from `status` (the execution lifecycle outcome).
    finish_status: str | None = None

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
            "approval": self.approval,
            "finish_status": self.finish_status,
        }


# ---------------------------------------------------------------------------
# Conditional-edge routers — pure functions of the state.
# ---------------------------------------------------------------------------
def _route_after_plan(state: AgentState) -> str:
    # NEEDS_HUMAN_REVIEW joins the terminal set (B2/B3): a plan trip that ESCALATED
    # (loop/budget with work already produced) must go to finalize, NOT act — else
    # the ACT decision still on the state would route to `act` and EXECUTE the very
    # action the trip was meant to stop (e.g. the 4th identical write).
    if state["status"] in (STATUS_ABORTED, STATUS_AWAITING_APPROVAL, STATUS_NEEDS_HUMAN_REVIEW):
        return "finalize"
    decision = state["last_decision"]
    if decision is not None and decision["kind"] == str(DecisionKind.FINISH):
        return "finalize"
    return "act"


def _route_after_reflect(state: AgentState) -> str:
    return "finalize" if state["status"] == STATUS_ABORTED else "plan"


def _route_after_review(state: AgentState) -> str:
    terminal = (STATUS_ABORTED, STATUS_AWAITING_APPROVAL, STATUS_NEEDS_HUMAN_REVIEW)
    if state["review_passed"] or state["status"] in terminal:
        return "end"
    return "retry"


class _AgentLoop:
    """Builds the compiled graph; its methods are the graph nodes."""

    def __init__(self, deps: AgentDeps, tracker: SafeguardTracker, detector: LoopDetector) -> None:
        self.deps = deps
        self.tracker = tracker
        self.detector = detector
        # ADR 0095: reviewer-aware safeguards (see AgentDeps.is_review).
        self.is_review = deps.is_review
        # Consecutive research-only tool calls (reset by any producing tool) —
        # drives the "stop researching, write" nudge in `reflect`.
        self.research_streak = 0
        # Whether a producing tool (write_file/…) has run — flips the nudge from
        # "write the deliverable" to "you're done, FINISH" (avoids over-verification).
        self.has_produced = False
        # ADR 0087 (Option 1): path → latest content the agent wrote, harvested from
        # producing tool-call args. Fed to the self-review so it judges the ACTUAL
        # code (not the unverifiable prose summary). Empty for analysis/design runs.
        self.written_files: dict[str, str] = {}
        # ADR 0089 (path-churn): how many times the agent has WRITTEN each path,
        # regardless of content. The byte-exact loop detector cannot catch a model
        # that re-writes the SAME file with slightly DIFFERENT content each turn
        # (different content → different fingerprint), and the identical-args nudge
        # misses it too — yet burning the iteration budget re-writing one file is a
        # non-convergence signal. This counter drives an advisory churn nudge.
        self.path_write_counts: dict[str, int] = {}

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

    def plan(self, state: AgentState) -> dict[str, Any]:  # noqa: PLR0911
        """Check safeguards, ask the model for the next move, detect loops."""
        base = len(state["steps"])
        steps: list[dict[str, Any]] = []

        # Iteration budget — checked before this turn is counted, so the
        # reported iteration count never exceeds max_iterations.
        if self.tracker.iteration_exhausted():
            # B3: a model that VARIES its output every turn never trips the loop
            # detector and would leak out here via the iteration budget; gate it like
            # the loop trip so a run that already produced work ESCALATES (preserving
            # the deliverable) instead of being discarded as a hard abort.
            status = _abort_or_escalate_status(self.has_produced, is_review=self.is_review)
            steps.append(
                node_step(
                    base,
                    "plan",
                    f"Safeguard tripped: {SafeguardCode.MAX_ITERATIONS}",
                    status="aborted" if status == STATUS_ABORTED else status,
                )
            )
            return {
                "status": status,
                "abort_code": str(SafeguardCode.MAX_ITERATIONS),
                "iteration": self.tracker.usage.iterations,
                "steps": steps,
            }

        self.tracker.tick_iteration()
        tripped = self.tracker.check()
        if tripped is not None:
            # B3: same gating for the cumulative budgets (tool calls / wall clock /
            # tokens / cost) — preserve produced work, abort a sterile run.
            status = _abort_or_escalate_status(self.has_produced, is_review=self.is_review)
            steps.append(
                node_step(
                    base,
                    "plan",
                    f"Safeguard tripped: {tripped}",
                    status="aborted" if status == STATUS_ABORTED else status,
                )
            )
            return {
                "status": status,
                "abort_code": str(tripped),
                "iteration": self.tracker.usage.iterations,
                "steps": steps,
            }

        # D4 (ADR 0089 addendum): the HARD read-churn backstop. A research-only streak
        # that ignored the soft nudge (the model re-reads/re-lists without producing)
        # would otherwise burn the WHOLE iteration budget — read-only tools are exempt
        # from the loop detector's hard abort, and distinct args never fingerprint as a
        # loop. Once the run has produced (or a prior self-review failed and it still
        # won't re-write), escalate NOW (preserving the deliverable) instead of leaking
        # to max_iterations. A sterile analysis-only run is NOT cut (gate fails).
        if _research_exhausted(
            research_streak=self.research_streak,
            has_produced=self.has_produced,
            review_retries=state["review_retries"],
            hard_limit=_RESEARCH_HARD_LIMIT,
            is_review=self.is_review,
        ):
            status = _abort_or_escalate_status(self.has_produced, is_review=self.is_review)
            steps.append(
                node_step(
                    base + len(steps),
                    "plan",
                    f"Safeguard tripped: {SafeguardCode.RESEARCH_EXHAUSTED}",
                    status="aborted" if status == STATUS_ABORTED else status,
                )
            )
            return {
                "status": status,
                "abort_code": str(SafeguardCode.RESEARCH_EXHAUSTED),
                "iteration": self.tracker.usage.iterations,
                "steps": steps,
            }

        # F25/P1.5: a provider error that survived Phase-1's retry+timeout
        # re-raises a typed LLMError. Catch it HERE and end the run cleanly
        # aborted (preserving the steps so far) instead of letting it bubble to
        # __main__ → execution.error → the worker doubling it to a hard `failed`
        # and losing all progress. Only LLM-layer errors are caught — a real bug
        # (KeyError/TypeError/…) is NOT an LLMError and still propagates.
        try:
            response = self.deps.model.decide(dict(state))
        except LLMError as exc:
            code = _provider_abort_code(exc)
            steps.append(
                node_step(
                    base + len(steps),
                    "plan",
                    f"Provider call failed: {code}",
                    status="aborted",
                )
            )
            return {
                "status": STATUS_ABORTED,
                "abort_code": code,
                "iteration": self.tracker.usage.iterations,
                "steps": steps,
            }
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
            # ALWAYS record (the count feeds count_of/the B1 nudge), but only a
            # MUTATING tool trips the hard repetitive-loop guard (Tema C): a read-only
            # tool repeated identically wastes turns but cannot corrupt the deliverable,
            # so it gets the nudge and is bounded by max_iterations/wall_clock instead.
            tripped_loop = self.detector.record(action)
            if tripped_loop and _is_mutating_tool(decision.tool):
                # B2: when work was already produced, ESCALATE (preserve it) rather
                # than hard-abort; a sterile loop stays aborted. The abort_code is
                # unchanged either way (the persisted contract).
                status = _abort_or_escalate_status(self.has_produced, is_review=self.is_review)
                steps.append(
                    node_step(
                        base + len(steps),
                        "plan",
                        f"Repetitive loop detected on tool '{decision.tool}'",
                        status="aborted" if status == STATUS_ABORTED else status,
                    )
                )
                return {
                    "status": status,
                    "abort_code": str(SafeguardCode.REPETITIVE_LOOP),
                    "last_decision": decision.as_dict(),
                    "iteration": self.tracker.usage.iterations,
                    "steps": steps,
                }

            # Approval gate: a sensitive tool is parked *before* it runs.
            category = (
                self.deps.approval.review(decision.tool) if self.deps.approval is not None else None
            )
            if category is not None:
                steps.append(
                    node_step(
                        base + len(steps),
                        "plan",
                        f"Awaiting human approval for '{decision.tool}' ({category})",
                        status="awaiting_human_approval",
                    )
                )
                return {
                    "status": STATUS_AWAITING_APPROVAL,
                    "approval": {
                        "category": category,
                        "action": {"tool": decision.tool, "args": decision.tool_args},
                    },
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

    def reflect(self, state: AgentState) -> dict[str, Any]:
        """Note progress before the next planning turn, nudging the agent off a
        research rut (repeated reads/searches with no deliverable) when needed."""
        observation = state["last_observation"] or {}
        tool = observation.get("tool")
        # Track the research-only streak: any producing tool resets it. A producing
        # tool also latches `has_produced`, which flips the nudge to "now FINISH".
        # Namespace-aware (audit C2/F24): an MCP/custom writer counts too.
        if _is_research_tool(tool):
            self.research_streak += 1
        else:
            self.research_streak = 0
        if _is_producing_tool(tool):
            self.has_produced = True
        decision = state["last_decision"] or {}
        # ADR 0087 (Option 1): harvest the file the agent just wrote (path+content)
        # so the self-review can judge the real code. Keeps the latest per path.
        if _is_producing_tool(tool):
            args = decision.get("tool_args") or {}
            path, content = args.get("path"), args.get("content")
            if isinstance(path, str) and path:
                # ADR 0089: count EVERY write to this path (any content) for the churn
                # nudge; harvest the content for the review only when it is a string.
                self.path_write_counts[path] = self.path_write_counts.get(path, 0) + 1
                if isinstance(content, str):
                    self.written_files[path] = content
        repeat_count = self.detector.count_of(
            {"tool": decision.get("tool"), "args": decision.get("tool_args")}
        )
        note = (
            "tool succeeded — continuing"
            if observation.get("ok")
            else "tool failed — will reconsider"
        )
        nudge = _research_nudge(
            tool=tool,
            research_streak=self.research_streak,
            repeat_count=repeat_count,
            has_produced=self.has_produced,
            is_review=self.is_review,
        )
        updates: dict[str, Any] = {"reflections": [note]}
        summary = f"Reflection: {note}"
        if nudge is not None:
            # Surface the nudge in the working context so the model SEES it next turn
            # (_decide_messages feeds the recent context tail to the model).
            updates["context"] = [{"role": "guidance", "note": nudge}]
            summary = f"Reflection: {note} — guidance: stop researching, produce output"
        # B1: the repetition warning fires one turn before the detector would abort
        # (count >= threshold). It rides the SCALAR `repetition_warning` field — NOT
        # `context` (operator.add): appending to context would reorder context[0] and
        # bury the warning in the bounded tail; `_decide_messages` renders the scalar
        # always, outside that tail.
        rep_warning = _repetition_nudge(
            tool=tool,
            repeat_count=repeat_count,
            threshold=self.detector.threshold,
            has_produced=self.has_produced,
        )
        # ADR 0089: a same-path CHURN (the agent re-writing one file with VARYING
        # content, never byte-identical) is the harder case the identical-args nudge
        # above cannot see — prefer the churn warning when it fires.
        churn_path = (decision.get("tool_args") or {}).get("path")
        churn_warning = (
            _path_churn_nudge(
                path=churn_path,
                write_count=self.path_write_counts.get(churn_path, 0),
                threshold=_PATH_CHURN_THRESHOLD,
            )
            if _is_producing_tool(tool) and isinstance(churn_path, str)
            else None
        )
        warning = churn_warning or rep_warning
        if warning is not None:
            updates["repetition_warning"] = warning
        updates["steps"] = [node_step(len(state["steps"]), "reflect", summary)]
        return updates

    def _deliverable_summary(self) -> str:
        """A human-readable summary of the deliverable on disk — the files the agent
        produced — for a plan-trip escalation (B2/B3).

        The looping/over-budget action's own ``output`` is the WRONG thing to show
        (it is the repeated action, not the work). The work is the set of files
        written: prefer the CUMULATIVE worktree state, falling back to this run's
        write capture when there is no worktree (tests / analysis runs). Returns ``""``
        when nothing was produced (the caller then keeps the abort_code summary).
        """
        files = _harvest_worktree_files(_workspace_root(), list(self.written_files))
        paths = [entry["path"] for entry in files] if files else sorted(self.written_files)
        if not paths:
            return ""
        listed = "\n".join(f"- {path}" for path in paths)
        return f"Deliverable produced ({len(paths)} file(s)) — escalated to human review:\n{listed}"

    def finalize(self, state: AgentState) -> dict[str, Any]:
        """Produce the final output (or the abort / approval / escalation summary)."""
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
        if state["status"] == STATUS_NEEDS_HUMAN_REVIEW:
            # Reached here ONLY from a plan-trip escalation (B2/B3): loop/budget with
            # work already produced. Render a SUMMARY of the deliverable (the files on
            # disk) — NOT decision['output'], which is the looping action. The
            # POST-review escalations (agent_reported_failure / inconclusive / retries)
            # set this status in self_review and go straight to END, so they never
            # reach this branch.
            output = (
                state["output"]
                or self._deliverable_summary()
                or (f"Execution escalated to human review ({state['abort_code']}).")
            )
            step = node_step(
                base,
                "finalize",
                f"Finalized — escalated to human review ({state['abort_code']})",
                status=STATUS_NEEDS_HUMAN_REVIEW,
            )
            return {"output": output, "steps": [step]}
        if state["status"] == STATUS_AWAITING_APPROVAL:
            approval = state["approval"] or {}
            action = approval.get("action", {})
            output = (
                f"Awaiting human approval for '{action.get('tool')}' "
                f"({approval.get('category')})."
            )
            step = node_step(
                base,
                "finalize",
                "Finalized — parked for human approval",
                status="awaiting_human_approval",
            )
            return {"output": output, "steps": [step]}
        decision = state["last_decision"] or {}
        output = decision.get("output") or "(no output produced)"
        return {"output": output, "steps": [node_step(base, "finalize", "Finalized output")]}

    def self_review(self, state: AgentState) -> dict[str, Any]:  # noqa: PLR0911
        """Review the output; pass, or bounce it back bounded by retries.

        The self-review is the AUTHORITATIVE gate (ADR 0087). A review PASS does
        NOT blindly become ``done``: if the agent itself reported ``finish_status``
        of ``failed``/``partial`` via ``submit_result``, it ADMITTED it did not
        complete the task — P2.2 (ADR 0087 addendum, decision D1) escalates that to
        a human (``agent_reported_failure``) rather than committing an admitted
        failure as success. A provider error during the review (F25/P1.5) ends the
        run cleanly aborted instead of crashing the container.
        """
        base = len(state["steps"])
        steps: list[dict[str, Any]] = []

        # NEEDS_HUMAN_REVIEW joins the skip-set (B2): a plan-trip escalation already
        # reached its verdict — running review() on it could turn a `passed` into a
        # false `done` (STATUS_DONE), erasing the escalation. _route_after_review
        # already routes NEEDS_HUMAN_REVIEW to END.
        if state["status"] in (
            STATUS_ABORTED,
            STATUS_AWAITING_APPROVAL,
            STATUS_NEEDS_HUMAN_REVIEW,
        ):
            steps.append(
                node_step(
                    base,
                    "self_review",
                    f"Skipped review — execution {state['status']}",
                    status=state["status"],
                )
            )
            return {"review_passed": False, "steps": steps}

        # ADR 0087 (Option 1): hand the reviewer the ACTUAL code, not the prose
        # summary it can't verify. Prefer the CUMULATIVE worktree state on disk, so
        # an INCREMENTAL run that did not re-write every file is still reviewed whole
        # (the "missing files" false negative observed live); fall back to this run's
        # write capture when there is no worktree (analysis/design runs, tests) →
        # prose-only review unchanged.
        review_state = dict(state)
        worktree_files = _harvest_worktree_files(_workspace_root(), list(self.written_files))
        if worktree_files:
            review_state["written_files"] = worktree_files
        elif self.written_files:
            review_state["written_files"] = [
                {"path": path, "content": content} for path, content in self.written_files.items()
            ]
        # F25/P1.5: same guard as plan()'s decide — a provider error that
        # outlived Phase-1's retries ends the run cleanly aborted (steps so far +
        # the deliverable from finalize preserved), not a container crash.
        try:
            review = self.deps.model.review(review_state)
        except LLMError as exc:
            code = _provider_abort_code(exc)
            steps.append(
                node_step(
                    base + len(steps),
                    "self_review",
                    f"Provider call failed during review: {code}",
                    status="aborted",
                )
            )
            return {
                "review_passed": False,
                "status": STATUS_ABORTED,
                "abort_code": code,
                "steps": steps,
            }
        self.tracker.record_model_call(review.tokens_in, review.tokens_out, review.cost_usd)
        steps.append(
            model_call_step(
                base + len(steps),
                "self_review",
                model=review.model,
                tokens_in=review.tokens_in,
                tokens_out=review.tokens_out,
                cost_usd=review.cost_usd,
                # Surface the verdict reason in the step so a failing review is
                # debuggable from steps_log (instrument, ADR 0086 / 2026-06-27).
                summary=(
                    f"Self-review: {'pass' if review.passed else 'fail'}"
                    + (f" — {review.feedback[:160]}" if review.feedback else "")
                ),
            )
        )

        if review.passed:
            # P2.2 (ADR 0087 addendum D1): a review PASS must NOT override the
            # agent's OWN admission that it didn't finish. When it reported
            # finish_status=failed/partial via submit_result, escalate to a human
            # instead of returning DONE (which would get committed as success).
            finish_status = (state.get("last_decision") or {}).get("finish_status")
            if finish_status in ("failed", "partial"):
                steps.append(
                    node_step(
                        base + len(steps),
                        "self_review",
                        f"Agent self-reported '{finish_status}' — a review pass cannot "
                        "turn an admitted incompletion into 'done'; escalating to human",
                        status=STATUS_NEEDS_HUMAN_REVIEW,
                    )
                )
                return {
                    "review_passed": False,
                    "status": STATUS_NEEDS_HUMAN_REVIEW,
                    "abort_code": str(SafeguardCode.AGENT_REPORTED_FAILURE),
                    "steps": steps,
                }
            steps.append(
                node_step(base + len(steps), "self_review", "Output approved by self-review")
            )
            return {"review_passed": True, "status": STATUS_DONE, "steps": steps}

        # Authoritative gate (ADR 0087): an INCONCLUSIVE verdict (untrustworthy —
        # no structured verdict + ambiguous prose, or malformed tool args) is
        # escalated to a human WITHOUT spending retries. Re-prompting an ambiguous
        # reviewer just burns budget; the human is the authoritative fallback
        # (CLAUDE.md ppio 7). The deliverable produced by `finalize` is preserved.
        if review.inconclusive:
            steps.append(
                node_step(
                    base + len(steps),
                    "self_review",
                    "Self-review inconclusive — escalating to human validation",
                    status=STATUS_NEEDS_HUMAN_REVIEW,
                )
            )
            return {
                "review_passed": False,
                "status": STATUS_NEEDS_HUMAN_REVIEW,
                "abort_code": str(SafeguardCode.REVIEW_INCONCLUSIVE),
                "steps": steps,
            }

        retries = state["review_retries"] + 1
        budget = self.tracker.budgets.max_review_retries
        # An EXPLICIT rejection is retried with feedback up to the budget; once the
        # budget is exhausted the run is ESCALATED to a human (ADR 0087), NOT
        # aborted — the work stands and a human decides, instead of being discarded
        # as a hard failure (the old `max_review_retries_exceeded` abort).
        if retries > budget:
            steps.append(
                node_step(
                    base + len(steps),
                    "self_review",
                    "Self-review retry budget exhausted — escalating to human validation",
                    status=STATUS_NEEDS_HUMAN_REVIEW,
                )
            )
            return {
                "review_passed": False,
                "status": STATUS_NEEDS_HUMAN_REVIEW,
                "abort_code": str(SafeguardCode.MAX_REVIEW_RETRIES_EXHAUSTED),
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
            # A1: the NEW authoritative channel — a SCALAR replayed verbatim every
            # turn, rendered outside the bounded context tail by `_decide_messages`,
            # so the feedback can't be evicted before the agent acts on it. The
            # existing context item is kept (some tests/consumers read it) but the
            # scalar is the one that survives a long context.
            "last_review_feedback": review.feedback,
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
    system_preamble: str | None = None,
) -> ExecutionResult:
    """Run one execution of the agent loop end to end.

    `on_step`, when given, is called with each step the moment it is
    produced — the graph is streamed node by node, so a live consumer
    (the agent-runtime entrypoint, task_02_29) sees steps as they
    happen rather than only at the end.

    `system_preamble` (Plan 06.18 task_06_18_13) carries the assigned skills'
    prompt fragments to prepend to the model's system prompt; `None` keeps the
    historical prompt untouched (backward-compat).
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
    final: AgentState = initial_state(task, system_preamble=system_preamble)
    emitted = 0
    for state in graph.stream(final, stream_mode="values", config=config):
        final = state
        if on_step is not None:
            steps = state["steps"]
            for step in steps[emitted:]:
                on_step(step)
            emitted = len(steps)
    last_decision = final["last_decision"] or {}
    return ExecutionResult(
        status=final["status"],
        abort_code=final["abort_code"],
        output=final["output"],
        iterations=tracker.usage.iterations,
        steps=final["steps"],
        usage=tracker.usage.as_dict(),
        approval=final["approval"],
        # The structured finish status (ADR 0087) rides on the last decision; it
        # is set only when the agent finished via `submit_result`.
        finish_status=last_decision.get("finish_status"),
    )
