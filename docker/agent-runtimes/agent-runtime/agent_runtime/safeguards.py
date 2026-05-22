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
    """Abort codes — recorded on the execution when a safeguard trips."""

    MAX_ITERATIONS = "max_iterations_exceeded"
    MAX_TOKENS = "max_tokens_exceeded"
    MAX_COST = "max_cost_exceeded"
    MAX_WALL_CLOCK = "max_wall_clock_exceeded"
    MAX_TOOL_CALLS = "max_tool_calls_exceeded"
    MAX_REVIEW_RETRIES = "max_review_retries_exceeded"
    REPETITIVE_LOOP = "repetitive_loop_detected"


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

    def check(self) -> SafeguardCode | None:
        """Return the first breached budget, or None while within budget."""
        budgets, usage = self.budgets, self.usage
        if usage.iterations > budgets.max_iterations:
            return SafeguardCode.MAX_ITERATIONS
        if usage.total_tokens > budgets.max_tokens:
            return SafeguardCode.MAX_TOKENS
        if usage.cost_usd > budgets.max_cost_usd:
            return SafeguardCode.MAX_COST
        if usage.tool_calls > budgets.max_tool_calls:
            return SafeguardCode.MAX_TOOL_CALLS
        if self.elapsed_s() > budgets.max_wall_clock_s:
            return SafeguardCode.MAX_WALL_CLOCK
        return None
