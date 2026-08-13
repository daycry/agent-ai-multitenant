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

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, cast

from langgraph.graph import END, START, StateGraph
from shared_llm import LLMError

from agent_runtime.approval import ApprovalGate
from agent_runtime.guardrails import run_hook
from agent_runtime.loop_detection import DEFAULT_LOOP_THRESHOLD, LoopDetector
from agent_runtime.model import DecisionKind, ModelClient, ModelDecision
from agent_runtime.nudges import (
    _PATH_CHURN_THRESHOLD,
    _REREAD_CHURN_NUDGE_LIMIT,
    _RESEARCH_HARD_LIMIT,  # noqa: F401  (re-export: los tests lo importan de aquí)
    _SAME_TARGET_HARD_LIMIT,
    _path_churn_nudge,
    _repetition_nudge,
    _reread_churn_nudge,
    _research_exhausted,
    _research_nudge,
    _same_target_nudge,
    _sterile_hard_limit,
)
from agent_runtime.prompt_version import prompt_version
from agent_runtime.providers import ProviderTimeout
from agent_runtime.review_harvest import (
    _harvest_worktree_files,
    _referenced_paths,
    _workspace_root,
    worktree_file_list,
)
from agent_runtime.safeguards import Budgets, SafeguardCode, SafeguardTracker
from agent_runtime.state import (
    STATUS_ABORTED,
    STATUS_AWAITING_APPROVAL,
    STATUS_DONE,
    STATUS_NEEDS_HUMAN_REVIEW,
    STATUS_RUNNING,
    AgentState,
    AgentTask,
    ReviewState,
    initial_state,
)
from agent_runtime.steps import memory_read_step, model_call_step, node_step, tool_call_step
from agent_runtime.tool_classification import (
    _base_tool_name,
    _is_mutating_tool,
    _is_platform_error,
    _is_producing_tool,
    _is_research_tool,
    _read_target,
)
from agent_runtime.tools import ToolRegistry, default_registry

_log = logging.getLogger("agent_runtime.graph")

# ADR 0114: la categoría del ApprovalRequest que crea un `ask_human` — el
# worker la trata como SIEMPRE-humana (bypasa la política por categorías del
# proyecto: preguntar a un humano es, por definición, para un humano).
HUMAN_QUESTION_CATEGORY = "human_question"

# AUD16-20: fallos de TRANSPORTE consecutivos de stack_exec que abortan el run.
# Un blip aislado no corta (reintento legítimo); una cascada 5xx/timeout del
# worker/docker-socket-proxy sí — es infraestructura rota, no estrategia del
# agente (el detector de bucle no salta con args distintos y stack_exec es
# producing-tool, exento de las guardas de research).
_STACK_EXEC_TRANSPORT_TRIP = 3


def _is_stack_exec_transport_failure(tool: str | None, observation: Any) -> bool:
    """Whether this observation is a stack_exec TRANSPORT failure (AUD16-20).

    Solo cuenta el fallo de transporte (``failed to reach the worker``, el
    marcador exacto de ``StackExecTool``): un rc!=0 del toolchain del usuario
    es transporte SANO y resetea la racha. Namespace-stripped, como el resto
    de clasificadores."""
    if _base_tool_name(tool) != "stack_exec":
        return False
    if not isinstance(observation, dict) or observation.get("ok"):
        return False
    return "failed to reach the worker" in str(observation.get("error") or "")


# ADR 0112 fase 2: veredictos "stuck" consecutivos que arman el escalado.
_ASSESS_STUCK_TRIP = 2
_ASK_HUMAN_QUESTION_MAX = 2000
_ASK_HUMAN_OPTIONS_MAX = 8

# ADR 0112 (fase 1): cadencia del self-check semántico. Cada K iteraciones el
# reflect inyecta un sticky que hace que el modelo se auto-evalúe contra los
# criterios EN SU TURNO NORMAL (cero llamadas LLM extra — la llamada dedicada
# queda como fase 2 si la telemetría lo pide).
_SELF_CHECK_EVERY = 10
_SELF_CHECK_NUDGE = (
    "Pause and self-assess before acting: score your progress toward the "
    "acceptance criteria (0-10) in one line, then update your plan with "
    "update_plan (what is DONE, what remains, the next concrete step). If this "
    "is your second consecutive self-check without real progress, stop looping: "
    "call submit_result with status='failed' explaining exactly what is blocking "
    "you, so a human can unblock it early instead of burning the budget."
)

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


# Memoria de lecturas (plan guardas-research C1): digests por fichero leído,
# renderizados en el bloque PROGRESS para que el modelo no relea para recordar.
# LRU acotado — presupuesto de prompt, no de memoria.
_READ_DIGESTS_MAX = 20
# G10 (ADR 0103): 100 chars barely held one line — too little for the model to reuse
# a digest instead of re-reading. 300 gives a few useful lines; the LRU cap (20) still
# bounds the PROGRESS block.
_READ_DIGEST_CHARS = 300
# G10 (ADR 0103, cierre 2026-07-12): la 1.ª FIRMA de símbolo del fichero leído
# entra en el digest — def/class (Python), function/class/interface (JS/TS/PHP),
# fn (Rust), func (Go). Deliberadamente simple: es una pista de memoria, no un
# parser; una línea que empiece por uno de estos verbos basta.
_SYMBOL_LINE_RE = re.compile(
    r"^(?:export\s+|public\s+|abstract\s+|final\s+|async\s+)*"
    r"(?:def|class|function|interface|trait|fn|func)\b"
)
# H5 (2026-07-07): caps de lo que el bloque PROGRESS muestra por-turno (antes un
# `12` suelto repetido inline). Ficheros ya escritos / digests de lectura más
# recientes — presupuesto de prompt, la lista completa vive en el estado.
_PROGRESS_FILES_MAX = 12
_PROGRESS_DIGESTS_MAX = 12


def _abort_or_escalate_status(
    has_produced: bool, *, is_review: bool = False, has_deliverable: bool = False
) -> str:
    """The terminal status for a budget/loop trip, gated by whether work exists.

    ADR 0087 (B2/B3): a run that has ALREADY produced a deliverable must not be
    discarded as a hard ``aborted`` failure when a safeguard trips — its work is
    preserved and the run is ESCALATED to a human (``needs_human_review``). A
    STERILE run (nothing produced) is a clean ``aborted`` as before. The abort_code
    is unchanged in either case; only the lifecycle status differs.

    ADR 0095: a REVIEW run is sterile by design (it produces a verdict, not a
    file), so a safeguard trip there ESCALATES to a human (the worker converges
    the task) instead of a silent hard abort that parks it in ``in_review``.

    ADR 0130-fix (run 019f9323): ``has_produced`` only latches on a producing TOOL
    (write_file/shell_exec/…). A REVIEW/ANALYSIS task's deliverable is the PROSE
    report it submits via ``submit_result`` — it writes no files, so ``has_produced``
    is structurally False and such a run was wrongly HARD-ABORTED (→ blocked) on a
    safeguard trip. ``has_deliverable`` (latched in :meth:`finalize` when the agent
    finished with a real result) fixes that: a produced prose deliverable escalates
    to a human, same as a file deliverable.
    """
    if is_review or has_produced or has_deliverable:
        return STATUS_NEEDS_HUMAN_REVIEW
    return STATUS_ABORTED


def _trip_outcome(
    *,
    review_retries: int,
    last_review_feedback: str,
    fallback_code: str,
    fallback_summary: str,
) -> tuple[str, str, str | None]:
    """Decide ``(abort_code, step_summary, output_override)`` for a safeguard trip.

    When a trip fires INSIDE a self-review retry cycle (``review_retries > 0``) the
    repeated action (identical re-writes, or sterile re-reads) is the SYMPTOM; the
    CAUSE is a self-review that keeps rejecting the same output — usually a
    contradictory/unsatisfiable acceptance spec. In that case we return the legible
    ``SELF_REVIEW_STALEMATE`` code and put the reviewer's persistent feedback in the
    escalation ``output`` so the operator sees WHY, instead of the opaque
    per-safeguard code. Outside a review cycle it stays the caller's fallback
    (``fallback_code``/``fallback_summary``) — unchanged contract.

    ADR 0130-fix: shared by BOTH the repetitive-loop trip (:func:`_loop_trip_outcome`)
    and the ``research_exhausted`` trip, so a self-review stalemate reads the same
    whether the agent churned writes or reads."""
    feedback = (last_review_feedback or "").strip()
    if review_retries > 0:
        plural = "y" if review_retries == 1 else "ies"
        summary = (
            f"Self-review stalemate: the reviewer keeps rejecting the output after "
            f"{review_retries} retr{plural}" + (f" — {feedback[:200]}" if feedback else "")
        )
        # H5 (2026-07-07): in English like every other platform summary/output —
        # this output also re-enters later runs' prompts as the prior output.
        output = (
            "Escalated to human validation: the self-review repeatedly rejected the "
            "output for the same reason, so the task's acceptance criteria "
            "may be contradictory or unsatisfiable. "
            f"Reviewer feedback: {feedback}"
            if feedback
            else None
        )
        return str(SafeguardCode.SELF_REVIEW_STALEMATE), summary, output
    return (fallback_code, fallback_summary, None)


def _loop_trip_outcome(
    *, review_retries: int, last_review_feedback: str, tool: str
) -> tuple[str, str, str | None]:
    """``(abort_code, step_summary, output_override)`` for a mutating-tool
    repetitive-loop trip — a thin wrapper over :func:`_trip_outcome` with the
    historical repetitive-loop fallback (unchanged contract)."""
    return _trip_outcome(
        review_retries=review_retries,
        last_review_feedback=last_review_feedback,
        fallback_code=str(SafeguardCode.REPETITIVE_LOOP),
        fallback_summary=f"Repetitive loop detected on tool '{tool}'",
    )


# P1-6: techo del scratchpad (sticky, entra al prompt cada turno).
_AGENT_PLAN_MAX_CHARS = 1500


def _no_recall(_task: AgentTask) -> list[dict[str, Any]]:
    """Recall stub para bare runs sin API interno — el boot de producción
    cablea el recall real (``__main__._build_auto_recall``, D1 2026-07-03)."""
    return []


def _no_knowledge(_task: AgentTask) -> list[dict[str, Any]]:
    """Auto-RAG stub para bare runs sin API interno — el boot de producción
    cablea el pre-fetch real de KB (``__main__._build_auto_rag``, P0-2)."""
    return []


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
    # P0-2 (investigación 2026-07-11): pre-fetch de pasajes de KB (rag_search
    # server-side con la task como query) inyectados al contexto inicial — la
    # KB deja de depender de que el modelo invoque la tool por su cuenta.
    knowledge: Callable[[AgentTask], list[dict[str, Any]]] = _no_knowledge
    # When set, gates sensitive tool calls before they run (task_02_33).
    approval: ApprovalGate | None = None
    # ADR 0095: this run is an AI REVIEW (judges another task's output). Makes the
    # convergence safeguards reviewer-aware: the nudge tells it to emit its verdict
    # (not write_file), the read-churn backstop cuts it, and a safeguard trip
    # escalates to a human instead of a silent abort.
    is_review: bool = False
    # ADR 0102 / g1: the resolved guardrail pipeline (or None). run_hook scans
    # tool OUTPUTS for prompt injection (post_tool) before they re-enter context.
    guardrails: Any = None
    # ADR 0112 fase 2: cadencia del mini-turno DEDICADO de reflexion (0 = OFF,
    # el default). Con K>0, cada K iteraciones reflect llama
    # model.assess_progress (si el cliente lo expone) y dos veredictos "stuck"
    # consecutivos escalan DETERMINISTA en el siguiente plan.
    reflection_assess_every: int = 0
    # AUD16-15: el KIND del proveedor del run (claude_sdk/ollama/...) — viaja
    # en cada step model_call para que el price-snapshot resuelva el catálogo.
    provider_kind: str | None = None
    # `task_wf_71`: sondeo de la guía humana sobre un run EN MARCHA. Devuelve el
    # texto que un humano acaba de escribir desde el visor, o `None`. Se
    # consulta una vez por iteración; `None` (el default) desactiva la
    # intervención — un run sin API interna se comporta igual que antes.
    guidance_poll: Callable[[], str | None] | None = None


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
    # Guardrail events triggered during the run (ADR 0102 / g1); the worker
    # persists them tenant-scoped from the result envelope.
    guardrail_events: list[dict[str, Any]] = field(default_factory=list)
    # `task_wf_52`: etiqueta del conjunto de prompts que produjo este run. Sin
    # ella, dos runs con resultados distintos son indistinguibles y no se puede
    # atribuir una mejora (ni una regresión) a un cambio de prompt.
    prompt_version: str | None = None

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
            "guardrail_events": self.guardrail_events,
            "prompt_version": self.prompt_version,
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
        # Plan guardas-research-por-novedad: las señales de research son de
        # NOVEDAD, nunca de cantidad. `read_targets` = targets NUEVOS logrados
        # con éxito (novedad); `read_churn_streak` = racha ESTÉRIL consecutiva
        # (re-reads, calls sin target, lecturas con error) — resetea con un
        # target nuevo o un producing tool; `read_counts` = lecturas por-target
        # (caza el patrón intercalado A,A,B,A,A); `read_digests` = memoria de lo
        # leído para el bloque PROGRESS (LRU, cap _READ_DIGESTS_MAX).
        self.read_targets: set[str] = set()
        # ADR 0112 fase 2: racha de self-assessments "stuck" consecutivos.
        self._assess_stuck_streak = 0
        # AUD16-20: racha de fallos de TRANSPORTE consecutivos de stack_exec.
        self._stack_exec_transport_streak = 0
        self.read_churn_streak = 0
        self.read_counts: dict[str, int] = {}
        self.read_digests: dict[str, str] = {}
        # Instrumentación (plan guardas-research B1): qué nudge/trip disparó y
        # cuántas veces — viaja en el step de finalize → steps_log → SQL.
        self.safeguard_stats: dict[str, int] = {}
        # Whether a producing tool (write_file/…) has run — flips the nudge from
        # "write the deliverable" to "you're done, FINISH" (avoids over-verification).
        self.has_produced = False
        # ADR 0130-fix: whether the agent FINISHED with a real deliverable (a
        # successful ``submit_result`` / non-empty final output), latched in
        # ``finalize``. A REVIEW/ANALYSIS task writes no files (``has_produced``
        # stays False) but its prose report IS a deliverable — so a later safeguard
        # trip ESCALATES to a human instead of hard-aborting the task to ``blocked``.
        self.has_deliverable = False
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
        # G8-B (ADR 0103): the last PRODUCTIVE action's (tool, args); a productive turn
        # whose action DIFFERS is intermediate progress that resets the loop-detector.
        self._last_productive_action: dict[str, Any] | None = None

    # -- nodes ---------------------------------------------------------------
    @staticmethod
    def perceive(state: AgentState) -> dict[str, Any]:
        """Read the task and seed the working context.

        P0-6 (investigación 2026-07-11): en un re-dispatch el worktree acumula
        el trabajo de intentos anteriores, pero el implementador arrancaba CIEGO
        y lo re-descubría a base de list_files/read_file (read-churn). Sembramos
        un overview acotado (solo paths) para orientarlo desde el turno 1.
        Worktree vacío (primer intento) → sin bloque, cero ruido."""
        task = state["task"]
        context: list[dict[str, Any]] = [
            {
                "role": "task",
                "title": task["title"],
                "description": task.get("description", ""),
            }
        ]
        summary = f"Perceived task: {task['title']}"
        existing_files = worktree_file_list()
        if existing_files:
            context.append(
                {
                    "role": "workspace",
                    "note": (
                        "these files ALREADY EXIST in the worktree (prior work) — "
                        "read what you need before re-creating anything"
                    ),
                    "files": existing_files,
                }
            )
            summary += f" — {len(existing_files)} existing file(s) in worktree"
        step = node_step(len(state["steps"]), "perceive", summary)
        return {"context": context, "steps": [step]}

    def recall(self, state: AgentState) -> dict[str, Any]:
        """Pull relevant memories for the task into the model's context.

        El boot (``__main__``) cablea ``deps.recall`` contra el endpoint
        scope-safe ``/internal/agent/memory-recall`` (D1, 2026-07-03); en un
        bare run sin API interno queda el stub ``_no_recall`` y el step se
        declara ``placeholder`` honestamente."""
        task = state["task"]
        hits = list(self.deps.recall(task))
        is_stub = self.deps.recall is _no_recall
        context = [{"role": "memory", **hit} for hit in hits]
        # P0-2: pasajes de KB pre-fetcheados (auto-RAG) — mismo raíl que las
        # memorias, tagueados como "knowledge" para que el modelo distinga la
        # fuente. Bare run (stub) → sin knowledge, y el summary no lo menciona.
        knowledge_is_stub = self.deps.knowledge is _no_knowledge
        knowledge_hits = [] if knowledge_is_stub else list(self.deps.knowledge(task))
        context += [{"role": "knowledge", **hit} for hit in knowledge_hits]
        # g1 (ADR 0102): recalled memory is an attacker-influenceable input path —
        # a prior run may have distilled a malicious tool output into team/global
        # memory ("ignore previous instructions…"). Screen it for prompt injection
        # before it reaches the model, exactly like a tool output. LOG mode: records,
        # never blocks. Scans every string field of the hit (content/title/…).
        # KB passages (tenant-uploaded documents) are screened the same way.
        guardrail_events: list[dict[str, Any]] = []
        for tool_name, tool_hits in (
            ("memory_recall", hits),
            ("rag_search", knowledge_hits),
        ):
            for hit in tool_hits:
                text = " ".join(str(v) for v in hit.values() if isinstance(v, str))
                guardrail_events += run_hook(
                    self.deps.guardrails,
                    hook="post_tool",
                    tool_name=tool_name,
                    tool_result=text,
                )
        summary = f"Recalled {len(hits)} memory item(s)"
        if not knowledge_is_stub:
            summary += f" + {len(knowledge_hits)} knowledge passage(s)"
        if is_stub:
            summary += " — no recall wired"
        step = memory_read_step(
            len(state["steps"]),
            "recall",
            query=task["title"],
            hits=len(hits),
            summary=summary,
            placeholder=is_stub,
        )
        # G11 (plan guardas-research): el SOBRE de presupuesto del run, en el
        # PRIMER step. El visor ya recibe lo GASTADO en vivo (`iterations`,
        # `total_tokens`) pero no el techo, así que no podía decir cuánto queda
        # — el aviso solo existía dentro del prompt, donde no lo ve nadie.
        #
        # Va aquí y no en `finalize` (donde vive `safeguard_stats`) porque el
        # caso de uso es un run EN CURSO: en finalize llegaría cuando ya no
        # sirve para decidir si intervenir. Y es el envelope que ESTE run
        # recibió, no el que esté configurado hoy: recalcularlo al leer mentiría
        # en cuanto el operador cambiara los presupuestos.
        step["budgets"] = self._budget_envelope()
        return {"context": context, "steps": [step], "guardrail_events": guardrail_events}

    def _budget_envelope(self) -> dict[str, float]:
        """Los topes del run, para que el visor calcule lo que queda."""
        budgets = self.tracker.budgets
        return {
            "max_iterations": budgets.max_iterations,
            "max_tokens": budgets.max_tokens,
            "max_cost_usd": budgets.max_cost_usd,
            "max_tool_calls": budgets.max_tool_calls,
        }

    def plan(self, state: AgentState) -> dict[str, Any]:  # noqa: PLR0911, PLR0912, PLR0915
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
            self._count_safeguard(f"trip:{SafeguardCode.MAX_ITERATIONS}")
            status = _abort_or_escalate_status(
                self.has_produced, is_review=self.is_review, has_deliverable=self.has_deliverable
            )
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
            self._count_safeguard(f"trip:{tripped}")
            status = _abort_or_escalate_status(
                self.has_produced, is_review=self.is_review, has_deliverable=self.has_deliverable
            )
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
            sterile_streak=self.read_churn_streak,
            max_same_target_reads=max(self.read_counts.values(), default=0),
            has_produced=self.has_produced,
            review_retries=state["review_retries"],
            sterile_limit=_sterile_hard_limit(self.tracker.budgets.max_iterations),
            same_target_limit=_SAME_TARGET_HARD_LIMIT,
            is_review=self.is_review,
        ):
            self._count_safeguard(f"trip:{SafeguardCode.RESEARCH_EXHAUSTED}")
            # ADR 0130-fix: `_research_exhausted` is eligible ONLY when the run has
            # something worth preserving (produced / a prior self-review failed /
            # is_review), so a trip here is never a sterile abort. When it fires
            # DURING a self-review retry cycle the sterile re-reads are the SYMPTOM
            # of a self-review stalemate (contradictory/unsatisfiable criteria) →
            # report it legibly with the reviewer feedback (Fix B). Either way a
            # produced deliverable escalates to a human, never hard-blocks (Fix A).
            abort_code, summary, output_override = _trip_outcome(
                review_retries=state["review_retries"],
                last_review_feedback=state.get("last_review_feedback") or "",
                fallback_code=str(SafeguardCode.RESEARCH_EXHAUSTED),
                fallback_summary=f"Safeguard tripped: {SafeguardCode.RESEARCH_EXHAUSTED}",
            )
            status = _abort_or_escalate_status(
                self.has_produced,
                is_review=self.is_review,
                has_deliverable=self.has_deliverable or state["review_retries"] > 0,
            )
            steps.append(
                node_step(
                    base + len(steps),
                    "plan",
                    summary,
                    status="aborted" if status == STATUS_ABORTED else status,
                )
            )
            trip_result: dict[str, Any] = {
                "status": status,
                "abort_code": abort_code,
                "iteration": self.tracker.usage.iterations,
                "steps": steps,
            }
            if output_override and status != STATUS_ABORTED:
                trip_result["output"] = output_override
            return trip_result

        # AUD16-20: una cascada de fallos de TRANSPORTE de stack_exec es infra
        # rota — cortar aquí en vez de quemar el presupuesto (el 07-02 un 502
        # en cascada del docker-socket-proxy consumió las 50 iteraciones).
        if self._stack_exec_transport_streak >= _STACK_EXEC_TRANSPORT_TRIP:
            self._count_safeguard(f"trip:{SafeguardCode.STACK_EXEC_UNAVAILABLE}")
            status = _abort_or_escalate_status(
                self.has_produced, is_review=self.is_review, has_deliverable=self.has_deliverable
            )
            steps.append(
                node_step(
                    base + len(steps),
                    "plan",
                    (
                        f"Safeguard tripped: {SafeguardCode.STACK_EXEC_UNAVAILABLE} "
                        f"({self._stack_exec_transport_streak} consecutive transport failures)"
                    ),
                    status="aborted" if status == STATUS_ABORTED else status,
                )
            )
            return {
                "status": status,
                "abort_code": str(SafeguardCode.STACK_EXEC_UNAVAILABLE),
                "iteration": self.tracker.usage.iterations,
                "steps": steps,
            }

        # F25/P1.5: a provider error that survived Phase-1's retry+timeout
        # re-raises a typed LLMError. Catch it HERE and end the run cleanly
        # aborted (preserving the steps so far) instead of letting it bubble to
        # __main__ → execution.error → the worker doubling it to a hard `failed`
        # and losing all progress. Only LLM-layer errors are caught — a real bug
        # (KeyError/TypeError/…) is NOT an LLMError and still propagates.
        # `task_wf_71`: ¿ha escrito un humano una corrección para este run? Se
        # pregunta ANTES de construir el turno para que entre en ESTE prompt y
        # no en el siguiente — media iteración de retraso en una intervención
        # manual es media iteración quemada en la dirección equivocada.
        human_guidance = self._poll_human_guidance()
        if human_guidance:
            steps.append(
                node_step(
                    base + len(steps),
                    "plan",
                    f"Human guidance received: {human_guidance[:120]}",
                )
            )

        # `task_wf_50`: de los cuatro hooks del principio rector 10, `pre_llm` y
        # `post_llm` estaban declarados y sin cablear — solo se tamizaban las
        # tools. Justo el prompt, que es donde se pliegan el contenido de
        # ficheros y la salida de MCP, viajaba al modelo sin mirar. Aquí se
        # cierra el ciclo: lo que ENTRA al modelo y lo que SALE.
        pre_events = self._screen_prompt(state)
        pre_blocked = [
            str(e.get("guardrail_type") or "?") for e in pre_events if e.get("action") == "block"
        ]
        if pre_blocked:
            summary = (
                "Prompt blocked by guardrail "
                f"({', '.join(pre_blocked)}) — the project's policy refuses to "
                "send this context to the model."
            )
            steps.append(node_step(base + len(steps), "plan", summary, status="aborted"))
            return {
                "status": STATUS_ABORTED,
                "abort_code": str(SafeguardCode.GUARDRAIL_BLOCKED),
                "output": summary,
                "iteration": self.tracker.usage.iterations,
                "steps": steps,
                "guardrail_events": pre_events,
            }

        try:
            response = self.deps.model.decide(dict(state))
        except LLMError as exc:
            code = _provider_abort_code(exc)
            # Observabilidad (2026-07-03): el mensaje del LLMError viaja en el
            # step Y en el output — antes solo sobrevivía el código y diagnosticar
            # exigía cazar los logs del contenedor efímero antes de su reap.
            detail = " ".join(str(exc).split())[:300]
            summary = f"Provider call failed: {code}" + (f" — {detail}" if detail else "")
            steps.append(
                node_step(
                    base + len(steps),
                    "plan",
                    summary,
                    status="aborted",
                )
            )
            return {
                "status": STATUS_ABORTED,
                "abort_code": code,
                "output": summary,
                "iteration": self.tracker.usage.iterations,
                "steps": steps,
            }
        self.tracker.record_model_call(response.tokens_in, response.tokens_out, response.cost_usd)
        decision = response.decision
        # `post_llm`: lo que el modelo acaba de devolver, ANTES de actuar sobre
        # ello. Un `block` aquí no aborta el run — reescribe la decisión a un
        # noop con el motivo, que es un rechazo VISIBLE del que el modelo puede
        # recuperarse en el turno siguiente (mismo patrón que una tool bloqueada).
        post_events = self._screen_response(decision)
        post_blocked = [
            str(e.get("guardrail_type") or "?") for e in post_events if e.get("action") == "block"
        ]
        if post_blocked:
            decision = replace(
                decision,
                kind=DecisionKind.ACT,
                tool="noop",
                tool_args={
                    "reason": (
                        f"your previous answer was blocked by a guardrail "
                        f"({', '.join(post_blocked)}); rephrase it without the "
                        "flagged content"
                    )
                },
                batch_calls=(),
            )
        guardrail_events = pre_events + post_events
        steps.append(
            model_call_step(
                base + len(steps),
                "plan",
                model=response.model,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                cost_usd=response.cost_usd,
                summary=decision.rationale or f"decision: {decision.kind}",
                provider=self.deps.provider_kind,
                cache_read_tokens=response.cache_read_tokens,
            )
        )

        stall = self._reflection_stall_trip(steps, base)
        if stall is not None:
            return stall

        decision, park = self._maybe_park_ask_human(decision, steps, base)
        if park is not None:
            return park

        if decision.kind == DecisionKind.ACT:
            # ADR 0111: el gate de aprobación aplica POR ELEMENTO también al lote
            # read-only — un elemento con categoría sensible se EXPULSA del lote
            # (nunca se ejecuta sin aprobación); el call principal sigue el gate
            # histórico de más abajo intacto.
            if decision.batch_calls and self.deps.approval is not None:
                kept = tuple(
                    extra
                    for extra in decision.batch_calls
                    if self.deps.approval.review(str(extra.get("tool") or "")) is None
                )
                if len(kept) != len(decision.batch_calls):
                    _log.info(
                        "batch: dropped %d call(s) requiring approval",
                        len(decision.batch_calls) - len(kept),
                    )
                    decision = replace(decision, batch_calls=kept)
            action: dict[str, Any] = {"tool": decision.tool, "args": decision.tool_args}
            if decision.batch_calls:
                # ADR 0111: la fingerprint del detector incluye el LOTE — repetir
                # exactamente el mismo batch cuenta como la misma acción.
                action["batch"] = [dict(extra) for extra in decision.batch_calls]
            # ALWAYS record (the count feeds count_of/the B1 nudge), but only a
            # MUTATING tool trips the hard repetitive-loop guard (Tema C): a read-only
            # tool repeated identically wastes turns but cannot corrupt the deliverable,
            # so it gets the nudge and is bounded by max_iterations/wall_clock instead.
            tripped_loop = self.detector.record(action)
            if tripped_loop and _is_mutating_tool(decision.tool):
                # B2: when work was already produced, ESCALATE (preserve it) rather
                # than hard-abort; a sterile loop stays aborted. When the loop trips
                # DURING a self-review retry cycle, report the legible
                # SELF_REVIEW_STALEMATE + reviewer feedback instead of the opaque
                # repetitive-loop code (systemic fix 2026-07-01).
                status = _abort_or_escalate_status(
                    self.has_produced,
                    is_review=self.is_review,
                    has_deliverable=self.has_deliverable,
                )
                abort_code, summary, output_override = _loop_trip_outcome(
                    review_retries=state["review_retries"],
                    last_review_feedback=str(state.get("last_review_feedback") or ""),
                    tool=decision.tool or "",
                )
                steps.append(
                    node_step(
                        base + len(steps),
                        "plan",
                        summary,
                        status="aborted" if status == STATUS_ABORTED else status,
                    )
                )
                result: dict[str, Any] = {
                    "status": status,
                    "abort_code": abort_code,
                    "last_decision": decision.as_dict(),
                    "iteration": self.tracker.usage.iterations,
                    "steps": steps,
                }
                if output_override:
                    result["output"] = output_override
                return result

            # Approval gate: a sensitive tool is parked *before* it runs.
            # ADR 0135: los ARGS viajan con la llamada — lo que un humano
            # autoriza es la acción exacta (tool + args verbatim), no la tool.
            category = (
                self.deps.approval.review(decision.tool, decision.tool_args)
                if self.deps.approval is not None
                else None
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
            "guardrail_events": guardrail_events,
            # `task_wf_71`: la corrección humana vale para el turno siguiente y
            # se limpia. Es una intervención puntual: dejarla pegada la
            # repetiría hasta el final del run y el agente re-aplicaría una
            # corrección que ya hizo.
            "human_guidance": human_guidance,
        }

    def _poll_human_guidance(self) -> str | None:
        """La corrección que un humano acaba de escribir para este run, si la hay.

        `task_wf_71`. Best-effort y con timeout corto: es una comodidad del
        operador, no puede hacer esperar al run ni tumbarlo si el api-server va
        lento. Sin sondeo configurado (bare run, sin API interna) devuelve
        ``None`` y el bucle se comporta exactamente como antes.
        """
        poll = self.deps.guidance_poll
        if poll is None:
            return None
        try:
            return poll()
        except Exception:  # pragma: no cover - la intervención jamás rompe el run
            _log.warning("human guidance poll failed; continuing without it", exc_info=True)
            return None

    # ---- pre_llm / post_llm (task_wf_50) --------------------------------

    def _screen_prompt(self, state: AgentState) -> list[dict[str, Any]]:
        """Tamiza lo que va a viajar al modelo ESTE turno (`pre_llm`).

        Se escanea el preámbulo del sistema y el contexto plegado, que es donde
        aterriza el contenido de terceros: lo leído de ficheros, la salida de
        las tools y la de los servidores MCP. El hook de tools ya mira cada
        resultado cuando entra; éste mira lo que de verdad se manda, que incluye
        lo acumulado en turnos anteriores y los preámbulos que arma la
        plataforma.

        Best-effort como el resto del seam: sin pipeline o con un fallo, `[]`.
        """
        parts: list[str] = []
        preamble = state.get("system_preamble")
        if isinstance(preamble, str) and preamble:
            parts.append(preamble)
        for entry in state.get("context") or []:
            if isinstance(entry, dict):
                content = entry.get("content")
                if isinstance(content, str) and content:
                    parts.append(content)
        if not parts:
            return []
        return run_hook(
            self.deps.guardrails,
            hook="pre_llm",
            prompt="\n".join(parts),
            metadata={"parts": len(parts)},
        )

    def _screen_response(self, decision: ModelDecision) -> list[dict[str, Any]]:
        """Tamiza lo que el modelo acaba de responder (`post_llm`).

        Mira el razonamiento y los argumentos de la decisión: es donde se vería
        un secreto exfiltrado o el efecto de una inyección que ya se coló. El
        hook `pre_tool` cubre la llamada concreta; éste cubre la respuesta
        ENTERA, incluida la prosa que nunca llega a ser una tool.
        """
        payload = " ".join(
            str(x)
            for x in (decision.rationale or "", decision.tool or "", decision.tool_args or {})
            if x
        ).strip()
        if not payload:
            return []
        return run_hook(
            self.deps.guardrails,
            hook="post_llm",
            tool_name=decision.tool,
            response=payload,
        )

    def _run_reflection_assess(self, state: AgentState, iterations: int) -> None:
        """ADR 0112 fase 2 (flag OFF): en cadencia, el mini-turno DEDICADO con
        veredicto estructurado; dos "stuck" consecutivos arman el trip
        determinista del siguiente plan. Best-effort: assess None no cuenta y
        un veredicto sano resetea la racha."""
        every = int(getattr(self.deps, "reflection_assess_every", 0) or 0)
        assess_fn = getattr(self.deps.model, "assess_progress", None)
        if every <= 0 or not callable(assess_fn) or not iterations or iterations % every:
            return
        verdict = assess_fn(dict(state))
        if not isinstance(verdict, dict):
            return
        self._count_safeguard("assess:call")
        if verdict.get("stuck"):
            self._assess_stuck_streak += 1
            self._count_safeguard("assess:stuck")
        else:
            self._assess_stuck_streak = 0

    def _reflection_stall_trip(
        self, steps: list[dict[str, Any]], base: int
    ) -> dict[str, Any] | None:
        """ADR 0112 fase 2: dos self-assessments "stuck" consecutivos → escalado
        DETERMINISTA (no depende de que el modelo obedezca la instrucción):
        needs_human_review si ya produjo trabajo, aborted si el run es estéril.
        ``None`` mientras la racha no llegue al umbral."""
        if self._assess_stuck_streak < _ASSESS_STUCK_TRIP:
            return None
        self._count_safeguard("trip:reflection_stalled")
        trip_status = _abort_or_escalate_status(
            self.has_produced, is_review=self.is_review, has_deliverable=self.has_deliverable
        )
        steps.append(
            node_step(
                base + len(steps),
                "plan",
                "Safeguard tripped: reflection_stalled (model self-reported no progress twice)",
                status="aborted" if trip_status == STATUS_ABORTED else trip_status,
            )
        )
        return {
            "status": trip_status,
            "abort_code": "reflection_stalled",
            "iteration": self.tracker.usage.iterations,
            "steps": steps,
        }

    def _maybe_park_ask_human(
        self, decision: ModelDecision, steps: list[dict[str, Any]], base: int
    ) -> tuple[ModelDecision, dict[str, Any] | None]:
        """ADR 0114: pregunta a humano NO terminal — capacidad del LOOP (patrón
        update_plan, no del registry).

        No-op para cualquier decisión que no sea un ACT de ``ask_human``. Con
        pregunta: el run PARQUEA por la maquinaria de aprobaciones (category
        ``human_question`` → ApprovalRequest → task a awaiting_human_approval);
        la respuesta re-dispatcha la task y llega al siguiente run como preámbulo
        ``human_answers``. Devuelve ``(decision, delta_de_park)``. Sin pregunta
        no hay nada que preguntar: devuelve ``(noop con razón visible, None)`` —
        reintento dirigido, nunca un park vacío."""
        if decision.kind != DecisionKind.ACT or decision.tool != "ask_human":
            return decision, None
        question = str((decision.tool_args or {}).get("question") or "").strip()
        if not question:
            rewritten = replace(
                decision,
                tool="noop",
                tool_args={
                    "reason": (
                        "ask_human requires a non-empty 'question'. Re-emit it "
                        "with the exact question you need answered, or continue "
                        "working if you can decide yourself."
                    )
                },
                batch_calls=(),
            )
            return rewritten, None
        ask_action: dict[str, Any] = {
            "tool": "ask_human",
            "args": {"question": question[:_ASK_HUMAN_QUESTION_MAX]},
        }
        raw_options = (decision.tool_args or {}).get("options")
        if isinstance(raw_options, list) and raw_options:
            ask_action["args"]["options"] = [
                str(opt)[:200] for opt in raw_options[:_ASK_HUMAN_OPTIONS_MAX]
            ]
        steps.append(
            node_step(
                base + len(steps),
                "plan",
                f"Awaiting human answer: {question[:120]}",
                status="awaiting_human_approval",
            )
        )
        return decision, {
            "status": STATUS_AWAITING_APPROVAL,
            "approval": {"category": HUMAN_QUESTION_CATEGORY, "action": ask_action},
            "last_decision": decision.as_dict(),
            "iteration": self.tracker.usage.iterations,
            "steps": steps,
        }

    def _screened_tool_call(
        self, tool: str, args: dict[str, Any]
    ) -> tuple[Any, list[dict[str, Any]]]:
        """Ejecuta una tool ENTRE los hooks pre_tool/post_tool (ADR 0102 D2).

        Un ``block`` configurado en pre_tool RECHAZA la llamada (la tool no
        corre; el error explica el guardrail — deny visible, reintento
        dirigido); un ``block`` en post_tool SUSTITUYE el output antes de que
        re-entre al contexto del modelo. El baseline warn/LOG no cambia nada:
        los eventos son advisory y viajan al envelope (D4). Best-effort — un
        engine roto degrada a la llamada sin escudo, jamás rompe el run."""
        from agent_runtime.tools import ToolResult

        pre_events = run_hook(self.deps.guardrails, hook="pre_tool", tool_name=tool, tool_args=args)
        blocked_types = [
            str(e.get("guardrail_type") or "?") for e in pre_events if e.get("action") == "block"
        ]
        if blocked_types:
            return (
                ToolResult(
                    ok=False,
                    error=(
                        f"call blocked by guardrail ({', '.join(blocked_types)}) — "
                        "this action is not permitted by the project's policy; "
                        "take a different route"
                    ),
                ),
                pre_events,
            )
        result = self.deps.tools.call(tool, args)
        self.tracker.record_tool_call()
        # g1 (ADR 0102): scan the tool OUTPUT for prompt injection BEFORE it folds
        # into the model context (observe) — closes indirect injection.
        post_events = run_hook(
            self.deps.guardrails, hook="post_tool", tool_name=tool, tool_result=result.output
        )
        post_blocked = [
            str(e.get("guardrail_type") or "?") for e in post_events if e.get("action") == "block"
        ]
        if post_blocked:
            result = ToolResult(
                ok=result.ok,
                output=f"[tool output blocked by guardrail ({', '.join(post_blocked)})]",
                error=result.error,
            )
        return result, pre_events + post_events

    def act(self, state: AgentState) -> dict[str, Any]:
        """Run the tool the model chose."""
        decision = state["last_decision"] or {}
        tool = decision.get("tool") or "noop"
        args = decision.get("tool_args") or {}
        # P1-6 (investigación 2026-07-11): scratchpad del agente. `update_plan`
        # es una capacidad del LOOP, no del registry: guarda la estrategia como
        # sticky (`agent_plan`) que el modelo VE todos los turnos — compensa la
        # reconstrucción single-turn (el agente no tenía memoria de su propio
        # plan salvo lo que cupiera en la ventana de 8 items).
        if tool == "update_plan":
            plan_text = str(args.get("plan") or "").strip()[:_AGENT_PLAN_MAX_CHARS]
            # `task_wf_51`: el scratchpad pasa por los MISMOS hooks que cualquier
            # otra tool. Ser una capacidad del loop y no del registry no lo
            # exime: su contenido se vuelve sticky y el modelo lo relee TODOS
            # los turnos, así que es el sitio con más permanencia del prompt —
            # exactamente donde una inyección quiere aterrizar. Saltárselo hacía
            # del scratchpad el único camino sin escudo hacia el contexto.
            plan_events = run_hook(
                self.deps.guardrails, hook="pre_tool", tool_name=tool, tool_args=args
            )
            plan_events += run_hook(
                self.deps.guardrails, hook="post_tool", tool_name=tool, tool_result=plan_text
            )
            blocked = [
                str(e.get("guardrail_type") or "?")
                for e in plan_events
                if e.get("action") == "block"
            ]
            if blocked:
                # No se guarda NADA: un plan a medias sería peor que ninguno, y
                # el sticky anterior sigue siendo válido. El error explica el
                # motivo para que el modelo reintente por otra vía (deny
                # visible, igual que en `_screened_tool_call`).
                plan_text = ""
            error = (
                f"plan blocked by guardrail ({', '.join(blocked)})"
                if blocked
                else (None if plan_text else "empty plan")
            )
            step = tool_call_step(
                len(state["steps"]),
                "act",
                tool=tool,
                args={"plan": plan_text[:200]},
                result={"ok": bool(plan_text)},
                status="ok" if plan_text else "error",
                summary="Plan actualizado" if plan_text else "update_plan sin contenido",
            )
            observation = {
                "tool": tool,
                "ok": bool(plan_text),
                "output": "plan stored" if plan_text else None,
                "error": error,
            }
            return {
                "agent_plan": plan_text or state.get("agent_plan"),
                "last_observation": observation,
                "steps": [step],
                "guardrail_events": plan_events,
            }
        result, guardrail_events = self._screened_tool_call(tool, args)
        steps = [
            tool_call_step(
                len(state["steps"]),
                "act",
                tool=tool,
                args=args,
                result=result.as_dict(),
                status="ok" if result.ok else "error",
                summary=f"Tool '{tool}' → {'ok' if result.ok else 'error'}",
            )
        ]
        observation = {
            "tool": tool,
            "ok": result.ok,
            "output": result.output,
            "error": result.error,
        }
        # AUD16-20: la racha de transporte de stack_exec — cualquier stack_exec
        # con transporte sano (incluso rc!=0 del toolchain) la resetea.
        if _base_tool_name(tool) == "stack_exec":
            if _is_stack_exec_transport_failure(tool, observation):
                self._stack_exec_transport_streak += 1
            else:
                self._stack_exec_transport_streak = 0
        # ADR 0111: el lote read-only del mismo turno — cada elemento se ejecuta,
        # cuenta contra el presupuesto de tool_calls y pasa por el hook post_tool;
        # sus resultados viajan agregados en la MISMA observación (un error por
        # elemento no tumba el turno). Solo la capa de decisión puebla el lote y
        # solo con tools read-only, así que ningún mutador puede colarse aquí.
        batch_calls = decision.get("batch_calls") or []
        if batch_calls:
            batch_observations: list[dict[str, Any]] = []
            for extra in batch_calls:
                extra_tool = str(extra.get("tool") or "")
                extra_args = extra.get("args") or {}
                extra_result, extra_events = self._screened_tool_call(extra_tool, extra_args)
                guardrail_events += extra_events
                steps.append(
                    tool_call_step(
                        len(state["steps"]) + len(steps),
                        "act",
                        tool=extra_tool,
                        args=extra_args,
                        result=extra_result.as_dict(),
                        status="ok" if extra_result.ok else "error",
                        summary=(
                            f"Tool '{extra_tool}' (batch) → {'ok' if extra_result.ok else 'error'}"
                        ),
                    )
                )
                batch_observations.append(
                    {
                        "tool": extra_tool,
                        "args": dict(extra_args),
                        "ok": extra_result.ok,
                        "output": extra_result.output,
                        "error": extra_result.error,
                    }
                )
            observation["batch"] = batch_observations
        return {
            "last_observation": observation,
            "steps": steps,
            "guardrail_events": guardrail_events,
        }

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

    def _track_research(
        self, tool: str | None, decision: dict[str, Any], observation: Any
    ) -> tuple[str | None, bool]:
        """Actualiza las señales de research por NOVEDAD (plan guardas-research A1/A2).

        Un target NUEVO logrado con ÉXITO es exploración (resetea la racha
        estéril); re-reads, calls sin target y lecturas con ERROR son estériles —
        los fallos NO acumulan "novedad" (anti-gaming: inventar paths
        inexistentes nuevos cada turno no es explorar). Namespace-aware
        (audit C2/F24). Devuelve ``(target, turn_productive)``."""
        target: str | None = None
        turn_productive = False
        if _is_research_tool(tool):
            target = _read_target(tool, decision.get("tool_args") or {})
            read_ok = bool(observation.get("ok"))
            # G3b (ADR 0103): a PLATFORM failure (tool denied / no executor / EACCES /
            # empty worktree) is not the agent's churn — it neither counts as a re-read
            # nor accumulates sterility. A file-not-found on a GUESSED path still does.
            platform_error = (not read_ok) and _is_platform_error(observation)
            if target is not None and not platform_error:
                self.read_counts[target] = self.read_counts.get(target, 0) + 1
            if read_ok and target is not None and target not in self.read_targets:
                self.read_targets.add(target)
                self.read_churn_streak = 0
                turn_productive = True
            elif not platform_error:
                self.read_churn_streak += 1
            if read_ok:
                self._harvest_read_digest(tool, target, observation)
        else:
            self.read_churn_streak = 0
        if _is_producing_tool(tool) and bool(observation.get("ok")):
            # G3/r4 (audit 2026-07-03): only a SUCCESSFUL producing tool latches
            # has_produced. A denied/failed shell_exec ("command not allowed") or
            # a write that errored produced nothing — latching it wrongly flipped
            # every safeguard trip from ABORTED to needs_human_review (contaminating
            # the human queue with sterile runs) and switched the nudge to "FINISH".
            self.has_produced = True
            turn_productive = True
            # G2 (ADR 0103): a productive turn DECAYS the per-target read counters — a
            # legit TDD loop (re-read the central file after a failed test) must not
            # accumulate toward the same-target nudge/trip. The CONSECUTIVE sterile
            # streak + budgets remain the convergence ceiling (ADR 0089 D4).
            self.read_counts.clear()
        return target, turn_productive

    def _select_nudge(
        self, *, tool: str | None, target: str | None, repeat_count: int
    ) -> str | None:
        """El nudge del turno, si aplica — el mensaje más ESPECÍFICO gana
        (per-target > esterilidad > repetición exacta) y se instrumenta (B1)."""
        candidates: tuple[tuple[str, str | None], ...] = (
            (
                "nudge:same_target",
                _same_target_nudge(
                    target=target,
                    count=self.read_counts.get(target, 0) if target else 0,
                    has_produced=self.has_produced,
                    is_review=self.is_review,
                ),
            ),
            (
                "nudge:sterile_churn",
                _reread_churn_nudge(
                    churn_streak=self.read_churn_streak,
                    limit=_REREAD_CHURN_NUDGE_LIMIT,
                    has_produced=self.has_produced,
                    is_review=self.is_review,
                ),
            ),
            (
                "nudge:exact_repeat",
                _research_nudge(
                    tool=tool,
                    repeat_count=repeat_count,
                    has_produced=self.has_produced,
                    is_review=self.is_review,
                ),
            ),
        )
        for kind, nudge in candidates:
            if nudge is not None:
                self._count_safeguard(kind)
                return nudge
        return None

    def reflect(self, state: AgentState) -> dict[str, Any]:
        """Note progress before the next planning turn, nudging the agent off a
        research rut (repeated reads/searches with no deliverable) when needed."""
        observation = state["last_observation"] or {}
        tool = observation.get("tool")
        decision = state["last_decision"] or {}
        target, turn_productive = self._track_research(tool, decision, observation)
        # ADR 0111: las guardas de novedad se aplican POR ELEMENTO del lote
        # read-only — cada lectura del batch registra su propio target (nuevo =
        # exploración; re-read = churn), igual que si hubiera ido en su turno.
        for sub in observation.get("batch") or []:
            sub_tool = sub.get("tool")
            _, sub_productive = self._track_research(
                sub_tool, {"tool": sub_tool, "tool_args": sub.get("args") or {}}, sub
            )
            turn_productive = turn_productive or sub_productive
        # G8-B (ADR 0103, ratificado opción B): un turno productivo cuya acción DIFIERE
        # de la última productiva es PROGRESO INTERMEDIO → resetea los contadores de
        # repetición para que un build idempotente en un ciclo edit→build→edit→build no
        # tripe. Un producing tool repetido SIN acción distinta entre medias (misma
        # fingerprint) NO dispara esto, así que sigue acumulando y tripando (pin del
        # identical-write); los repetidos no-productivos (echo) quedan intactos.
        if turn_productive:
            current_action = {"tool": decision.get("tool"), "args": decision.get("tool_args")}
            if (
                self._last_productive_action is not None
                and current_action != self._last_productive_action
            ):
                self.detector.note_progress()
            self._last_productive_action = current_action
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
        nudge = self._select_nudge(tool=tool, target=target, repeat_count=repeat_count)
        updates: dict[str, Any] = {"reflections": [note]}
        summary = f"Reflection: {note}"
        if nudge is not None:
            # F2b.3 (auditoría 2026-07-02): el nudge viaja en el escalar STICKY
            # `guidance_nudge` (renderizado SIEMPRE, fuera del tail acotado) —
            # antes iba como item de `context` y la ventana de 8 items podía
            # evictarlo antes de que el modelo actuara sobre él.
            updates["guidance_nudge"] = nudge
            # G5 (ADR 0103): the step summary shows the ACTUAL nudge variant
            # (same-target / sterile / already-produced-FINISH / repetition) instead
            # of a hardcoded "stop researching, produce output" that misreported an
            # "you're done, FINISH" nudge as "keep producing" in the run viewer (r3).
            guidance = " ".join(nudge.split())
            summary = f"Reflection: {note} — guidance: {guidance[:120]}"
        elif turn_productive:
            # A4: el sticky se LIMPIA con progreso real — sin esto, un nudge
            # antiguo seguía presionando a escribir tras retomar la exploración.
            updates["guidance_nudge"] = None
        # F2b.1/2: resumen de progreso siempre-visible (iteración N/límite +
        # ficheros ya escritos + aviso de cierre al 80% del presupuesto). Ataca
        # la causa raíz del read-churn: el modelo no podía recordar qué escribió
        # hace >8 pasos y re-leía para reconstruirlo.
        updates["progress_summary"] = self._progress_summary()
        # ADR 0112 (fase 1): self-check semántico periódico — a la iteración K,
        # 2K, … el sticky pide al modelo puntuar su progreso contra los criterios
        # y refrescar su scratchpad; fuera de cadencia se LIMPIA (None) para que
        # no presione todos los turnos.
        iterations = self.tracker.usage.iterations
        if iterations and iterations % _SELF_CHECK_EVERY == 0:
            updates["self_check_nudge"] = _SELF_CHECK_NUDGE
            self._count_safeguard("nudge:self_check")
        else:
            updates["self_check_nudge"] = None
        # ADR 0112 fase 2 (flag OFF por defecto): mini-turno dedicado.
        self._run_reflection_assess(state, iterations)
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
            self._count_safeguard(
                "nudge:path_churn" if churn_warning is not None else "nudge:repetition_warning"
            )
        updates["steps"] = [node_step(len(state["steps"]), "reflect", summary)]
        return updates

    def _count_safeguard(self, kind: str) -> None:
        """Instrumentación B1 (plan guardas-research): contadores de nudges/trips
        que viajan en el step de finalize → ``steps_log`` → consultables por SQL
        para medir falsos positivos y ajustar umbrales con datos."""
        self.safeguard_stats[kind] = self.safeguard_stats.get(kind, 0) + 1

    def _harvest_read_digest(self, tool: str | None, target: str | None, observation: Any) -> None:
        """Memoria de lecturas C1: digest por fichero leído para el bloque PROGRESS.

        Del ``last_observation`` (sin I/O extra): 1.ª línea significativa del
        contenido + la 1.ª FIRMA de símbolo (def/class/function — G10, ADR
        0103: el modelo recuerda QUÉ define el fichero sin re-leerlo) para
        read_file, o nº de entradas (list_files). LRU acotado a
        ``_READ_DIGESTS_MAX`` — presupuesto de prompt, no de memoria."""
        if target is None:
            return
        output = observation.get("output")
        if not isinstance(output, dict):
            return
        digest: str | None = None
        base = _base_tool_name(tool)
        if base == "read_file":
            content = output.get("content")
            if isinstance(content, str):
                lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
                first = lines[0] if lines else ""
                symbol = next(
                    (ln for ln in lines if _SYMBOL_LINE_RE.match(ln) and ln != first),
                    None,
                )
                head = f"{first} … {symbol}" if symbol else first
                digest = f"{head[:_READ_DIGEST_CHARS]} · {len(content)}B"
        elif base == "list_files":
            files = output.get("files")
            if isinstance(files, list):
                digest = f"{len(files)} entries"
        if digest is None:
            return
        self.read_digests.pop(target, None)  # refresh LRU order
        self.read_digests[target] = digest
        while len(self.read_digests) > _READ_DIGESTS_MAX:
            self.read_digests.pop(next(iter(self.read_digests)))

    # Fracción del presupuesto de iteraciones a partir de la cual el resumen de
    # progreso avisa de cerrar (F2b.2) — antes el modelo nunca sabía cuánto le
    # quedaba: los límites solo abortaban.
    _BUDGET_WARN_FRACTION = 0.8

    def _progress_summary(self) -> str:
        """El bloque PROGRESS siempre-visible del prompt (F2b.1/2 + C1)."""
        used = self.tracker.usage.iterations
        cap = self.tracker.budgets.max_iterations
        parts = [f"iteration {used}/{cap}"]
        if self.written_files:
            names = sorted(self.written_files)
            shown = ", ".join(names[:_PROGRESS_FILES_MAX])
            if len(names) > _PROGRESS_FILES_MAX:
                shown += f" (+{len(names) - _PROGRESS_FILES_MAX} more)"
            parts.append(f"files you have ALREADY written (do not re-read them): {shown}")
        elif not self.is_review:
            # Sin sesgo a escribir (casuística del operador 2026-07-03): una
            # tarea puede ser SOLO de análisis — su respuesta final ES el
            # entregable, no tiene por qué tocar ficheros.
            parts.append(
                "no files written yet (fine if this task only requires analysis — "
                "your final written answer is the deliverable)"
            )
        if self.read_digests:
            # C1 (plan guardas-research): memoria de lecturas — el modelo relee
            # porque la ventana de contexto descarta lo leído; estos digests le
            # devuelven lo esencial sin otra lectura. Las más recientes.
            entries = [
                f"{target.split(':', 1)[-1]} — {digest}"
                for target, digest in list(self.read_digests.items())[-_PROGRESS_DIGESTS_MAX:]
            ]
            parts.append(
                "files you have already READ (use what you learned; do not re-read): "
                + "; ".join(entries)
            )
        if cap and used >= max(1, int(cap * self._BUDGET_WARN_FRACTION)):
            remaining = max(0, cap - used)
            parts.append(
                f"only {remaining} iterations left — wrap up and FINISH now with what you have"
            )
        return " · ".join(parts)

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
        """Produce the final output (or the abort / approval / escalation summary).

        Instrumentación B1 (plan guardas-research): el step de finalize lleva
        ``safeguard_stats`` — qué nudges/trips dispararon y cuántas veces — que
        persiste en ``steps_log`` para medir falsos positivos por SQL."""
        base = len(state["steps"])
        stats = dict(self.safeguard_stats)
        if state["status"] == STATUS_ABORTED:
            output = state["output"] or f"Execution aborted ({state['abort_code']})."
            step = node_step(
                base,
                "finalize",
                f"Finalized aborted execution ({state['abort_code']})",
                status="aborted",
            )
            step["safeguard_stats"] = stats
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
            step["safeguard_stats"] = stats
            return {"output": output, "steps": [step]}
        if state["status"] == STATUS_AWAITING_APPROVAL:
            approval = state["approval"] or {}
            action = approval.get("action", {})
            output = (
                f"Awaiting human approval for '{action.get('tool')}' ({approval.get('category')})."
            )
            step = node_step(
                base,
                "finalize",
                "Finalized — parked for human approval",
                status="awaiting_human_approval",
            )
            step["safeguard_stats"] = stats
            return {"output": output, "steps": [step]}
        decision = state["last_decision"] or {}
        raw_output = decision.get("output")
        output = raw_output or "(no output produced)"
        # ADR 0130-fix: latch that a REAL deliverable was produced this run — either
        # the agent finished via submit_result (finish_status set) or it wrote a
        # non-empty final answer (analysis/review runs). This makes a LATER safeguard
        # trip (on a self-review retry) escalate to a human instead of hard-aborting
        # a task whose deliverable is prose, not files (has_produced stays False).
        if decision.get("finish_status") in ("success", "partial") or (
            isinstance(raw_output, str) and raw_output.strip()
        ):
            self.has_deliverable = True
        step = node_step(base, "finalize", "Finalized output")
        step["safeguard_stats"] = stats
        return {"output": output, "steps": [step]}

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

        # prod-17 A5 (auditoría 2026-07-06): un run de AI-REVIEWER NO se auto-revisa.
        # Su entregable ES el veredicto (`<verdict>…` en el output), que el WORKER
        # parsea con `parse_reviewer_output` — someterlo a una segunda `model.review()`
        # duplicaba el coste y, si esa review de 2º orden salía inconclusa, mandaba un
        # approve correcto a `blocked`. Se salta y enruta a END.
        if self.is_review:
            # Regresión QA 2026-07-07 (run 019f3ced): en el flujo NORMAL es la
            # self-review quien fija DONE — al saltarla, un review limpio llegaba
            # aquí con STATUS_RUNNING y el runtime emitía `finished status=running`;
            # el worker (ADR 0096) degradaba ese approve a `blocked`. El skip fija
            # `done` para un run limpio y PRESERVA cualquier status ya terminal
            # (aborted / needs_human_review / awaiting — las escaladas mandan).
            final_status = STATUS_DONE if state["status"] == STATUS_RUNNING else state["status"]
            steps.append(
                node_step(
                    base,
                    "self_review",
                    "Skipped self-review — this IS a review run (verdict is the output)",
                    status=final_status,
                )
            )
            # review_passed=True enruta a END (no a retry); no reinterpreta el
            # veredicto del reviewer (el worker lo lee del output, no de este flag).
            return {"review_passed": True, "status": final_status, "steps": steps}

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
        # M-5 (auditoría 2026-07-10): tipado como ReviewState — la clave inyectada
        # `written_files` de abajo la verifica mypy además del scanner AST.
        review_state = cast(ReviewState, dict(state))
        # Caso 019f27cc (2026-07-03): los paths que la task/output NOMBRAN entran
        # primero en el harvest — un entregable pre-existente (run anterior) debe
        # ser visible para el reviewer aunque este run no escribiera nada y el
        # worktree tenga más ficheros que el cap.
        prefer = list(self.written_files) + [
            p for p in _referenced_paths(state) if p not in self.written_files
        ]
        worktree_files = _harvest_worktree_files(_workspace_root(), prefer)
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
            detail = " ".join(str(exc).split())[:300]
            steps.append(
                node_step(
                    base + len(steps),
                    "self_review",
                    f"Provider call failed during review: {code}"
                    + (f" — {detail}" if detail else ""),
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
                provider=self.deps.provider_kind,
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
    # F1.6c: el flag is_review viaja en el estado para que `_system_content`
    # seleccione el contrato del reviewer.
    final: AgentState = initial_state(
        task, system_preamble=system_preamble, is_review=deps.is_review
    )
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
        guardrail_events=final.get("guardrail_events") or [],
        # `task_wf_52`: se calcula al CERRAR el run, del código que acaba de
        # correr — no se pasa desde fuera, que es como se acaba etiquetando un
        # run con la versión de otra imagen.
        prompt_version=prompt_version(),
    )
