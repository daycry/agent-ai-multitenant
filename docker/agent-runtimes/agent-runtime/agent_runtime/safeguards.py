"""Execution safeguards — the budgets that bound an agent run (task_02_13).

An autonomous loop with a buggy model can burn tokens, money and wall
time without end. Every execution runs against a `Budgets` envelope;
`SafeguardTracker` accumulates usage and `check()` reports the first
budget that has been breached. The agent loop calls `check()` at the
top of every iteration and aborts with the returned code.

`max_review_retries` is special: a HARD PLATFORM LIMIT (default 3) that
only the System Admin may change — see `platform_settings` and ADR 0013.
A tenant cannot loosen it.
"""

from __future__ import annotations

import enum
import time
from collections.abc import Callable
from dataclasses import dataclass

# Platform-wide default for max_review_retries. Overridable only by the
# System Admin via platform_settings (task_02_13b).
DEFAULT_MAX_REVIEW_RETRIES = 3


class SafeguardCode(enum.StrEnum):
    """Abort / escalation codes — recorded on the execution as ``abort_code``.

    Two families share this enum so the graph has ONE source of truth (F27):

      * cumulative-budget safeguards (``MAX_*``, ``REPETITIVE_LOOP``) — a hard
        abort when a resource envelope is breached;
      * self-review escalation + provider-failure codes
        (``REVIEW_INCONCLUSIVE`` … ``PROVIDER_TIMEOUT``) — the run ends
        ``needs_human_review``/``aborted`` with a clear, persisted reason instead
        of crashing or silently passing.

    The string VALUES are part of the persisted contract (``executions.abort_code``,
    the worker's task-blocking reasons, the UI). Do NOT change an existing value.
    """

    MAX_ITERATIONS = "max_iterations_exceeded"
    MAX_TOKENS = "max_tokens_exceeded"
    MAX_COST = "max_cost_exceeded"
    MAX_WALL_CLOCK = "max_wall_clock_exceeded"
    MAX_TOOL_CALLS = "max_tool_calls_exceeded"
    REPETITIVE_LOOP = "repetitive_loop_detected"
    # ADR 0089-D4: a research-only streak that ignores the soft nudge (read-churn)
    # trips this hard backstop well before max_iterations, escalating the produced
    # work instead of burning the whole budget re-reading.
    RESEARCH_EXHAUSTED = "research_exhausted"
    # Self-review escalation codes (ADR 0087). The values are exactly the strings
    # already persisted by the loop ('review_inconclusive' /
    # 'max_review_retries_exhausted'); the old dead 'max_review_retries_exceeded'
    # member was removed (F27 — it never matched what the graph wrote).
    REVIEW_INCONCLUSIVE = "review_inconclusive"
    MAX_REVIEW_RETRIES_EXHAUSTED = "max_review_retries_exhausted"
    # A repetitive-loop trip that happens DURING a self-review retry cycle: the
    # churn is the SYMPTOM, a self-review that keeps rejecting the same output
    # (often a contradictory / unsatisfiable acceptance spec) is the CAUSE. The
    # graph reports this legible code + the persistent reviewer feedback instead
    # of the opaque 'repetitive_loop_detected', so the operator can act on it
    # (systemic fix 2026-07-01).
    SELF_REVIEW_STALEMATE = "self_review_stalemate"
    # A self-declared incompletion (finish_status failed/partial) that a review
    # PASS must not override into 'done' (ADR 0087 addendum D1, P2.2).
    AGENT_REPORTED_FAILURE = "agent_reported_failure"
    # Provider-layer failure that survived Phase-1 retries (F25/P1.5): the run
    # ends cleanly aborted instead of crashing to execution.error.
    PROVIDER_ERROR = "provider_error"
    PROVIDER_TIMEOUT = "provider_timeout"
    # AUD16-20: N fallos de TRANSPORTE consecutivos de stack_exec (el worker /
    # docker-socket-proxy no responde — 5xx/timeout). Es infraestructura rota,
    # no estrategia del agente: cortar en vez de quemar el presupuesto entero
    # (el detector de bucle no salta con args distintos y stack_exec es
    # producing-tool, exento de las guardas de research).
    STACK_EXEC_UNAVAILABLE = "stack_exec_unavailable"
    # `task_wf_50`: un guardrail configurado como `block` en el hook `pre_llm`
    # rechazó el prompt del turno. No es un fallo del agente ni de la
    # plataforma: es la política del tenant negándose a mandar al modelo un
    # contexto marcado. Se corta con código propio para que no se confunda con
    # un abort por presupuesto.
    GUARDRAIL_BLOCKED = "guardrail_blocked"


@dataclass(frozen=True)
class Budgets:
    """The resource envelope for one execution."""

    max_iterations: int = 25
    max_tokens: int = 100_000
    max_cost_usd: float = 5.0
    max_wall_clock_s: float = 600.0
    max_tool_calls: int = 50
    max_review_retries: int = DEFAULT_MAX_REVIEW_RETRIES


class SafeguardError(RuntimeError):
    """Raised when a safeguard is breached outside the graph's own checks."""

    def __init__(self, code: SafeguardCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Usage:
    """Running totals for one execution."""

    iterations: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    model_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def as_dict(self) -> dict[str, float | int]:
        return {
            "iterations": self.iterations,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "tool_calls": self.tool_calls,
            "model_calls": self.model_calls,
        }


class SafeguardTracker:
    """Accumulates usage and reports the first breached budget.

    The `clock` is injectable so the wall-clock budget is testable
    without sleeping.
    """

    def __init__(self, budgets: Budgets, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.budgets = budgets
        self.usage = Usage()
        self._clock = clock
        self._start = clock()

    def tick_iteration(self) -> None:
        self.usage.iterations += 1

    def record_model_call(self, tokens_in: int, tokens_out: int, cost_usd: float) -> None:
        self.usage.model_calls += 1
        self.usage.tokens_in += tokens_in
        self.usage.tokens_out += tokens_out
        self.usage.cost_usd += cost_usd

    def record_tool_call(self) -> None:
        self.usage.tool_calls += 1

    def elapsed_s(self) -> float:
        return self._clock() - self._start

    def iteration_exhausted(self) -> bool:
        """True once the iteration budget is spent.

        Checked *before* the next turn is counted, so `usage.iterations`
        never exceeds `max_iterations` — a finished execution reports an
        honest iteration count.
        """
        return self.usage.iterations >= self.budgets.max_iterations

    def check(self) -> SafeguardCode | None:
        """First breached *cumulative* budget — tokens, cost, tool calls
        or wall clock — or None while within budget.

        The iteration budget is handled separately by
        `iteration_exhausted` (it must be tested before the turn is
        counted, not after).
        """
        budgets, usage = self.budgets, self.usage
        if usage.total_tokens > budgets.max_tokens:
            return SafeguardCode.MAX_TOKENS
        if usage.cost_usd > budgets.max_cost_usd:
            return SafeguardCode.MAX_COST
        if usage.tool_calls > budgets.max_tool_calls:
            return SafeguardCode.MAX_TOOL_CALLS
        if self.elapsed_s() > budgets.max_wall_clock_s:
            return SafeguardCode.MAX_WALL_CLOCK
        return None
