"""Reading back what an execution captured (task_02_12).

The `steps_log` an execution accumulates is a flat list of step dicts.
These helpers slice it by kind and roll the model-call usage back up —
the Timeline UI (Fase E) and the execution-detail view lean on them,
and the capture tests cross-check the roll-up against the loop's own
`SafeguardTracker` usage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime.steps import StepKind

Step = dict[str, Any]


def _of_kind(steps: list[Step], kind: StepKind) -> list[Step]:
    return [step for step in steps if step.get("kind") == kind]


def model_calls(steps: list[Step]) -> list[Step]:
    """The LLM-call steps — each carries token counts and cost."""
    return _of_kind(steps, StepKind.MODEL_CALL)


def tool_calls(steps: list[Step]) -> list[Step]:
    """The builtin-tool invocation steps."""
    return _of_kind(steps, StepKind.TOOL_CALL)


def memory_reads(steps: list[Step]) -> list[Step]:
    """The memory recall steps (placeholders until Plan 04)."""
    return _of_kind(steps, StepKind.MEMORY_READ)


@dataclass(frozen=True)
class CaptureSummary:
    """Roll-up of an execution's captured calls, recomputed from steps_log."""

    model_call_count: int
    tool_call_count: int
    memory_read_count: int
    total_tokens: int
    total_cost_usd: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "model_call_count": self.model_call_count,
            "tool_call_count": self.tool_call_count,
            "memory_read_count": self.memory_read_count,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
        }


def summarize(steps: list[Step]) -> CaptureSummary:
    """Recompute the usage roll-up straight from the steps_log.

    Independent of the loop's `SafeguardTracker` — the two must agree,
    which is exactly what the capture test asserts.
    """
    calls = model_calls(steps)
    return CaptureSummary(
        model_call_count=len(calls),
        tool_call_count=len(tool_calls(steps)),
        memory_read_count=len(memory_reads(steps)),
        total_tokens=sum(int(step.get("total_tokens", 0)) for step in calls),
        total_cost_usd=round(sum(float(step.get("cost_usd", 0.0)) for step in calls), 6),
    )
