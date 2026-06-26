"""Per-run execution budget envelope (prod-06 task_prod06_budget_02 / workers-10).

The dispatcher used to thread ``budgets: None`` into every ``run_execution``,
so a runaway agent loop was bounded only by the agent-runtime's compiled-in
defaults — never by anything an operator could tune per project or platform.

This module resolves the envelope ``platform-default ← project-override`` and
CLAMPS every key to the runtime ceiling, so a misconfigured override can tighten
a budget but never loosen it past the platform's hard envelope. The resolved
dict (or ``None``) is what the dispatcher puts on ``ExecutionRequest.budgets``;
the agent-runtime fills any absent key with its own ``Budgets`` dataclass
default (which equals the ceiling here), so a ``None``/partial envelope is safe.
"""

from __future__ import annotations

from typing import Any

# Mirrors the agent-runtime ``Budgets`` dataclass defaults
# (docker/agent-runtimes/agent-runtime/agent_runtime/safeguards.py:38-47). These
# are the CEILING — the maximum a project/platform budget may set. The dispatcher
# must not import the sandboxed runtime package, so the values are duplicated here
# by hand; keep them in sync if the dataclass defaults ever change.
EXECUTION_BUDGET_CEILING: dict[str, float] = {
    "max_iterations": 25,
    "max_tokens": 100_000,
    "max_cost_usd": 5.0,
    "max_wall_clock_s": 600.0,
    "max_tool_calls": 50,
}

# ``max_review_retries`` is a HARD PLATFORM LIMIT owned by platform_settings
# (ADR 0013): a tenant cannot loosen it, and it is NOT a per-project budget. It is
# deliberately absent from the ceiling so a project override can never touch it.

# Keys whose value is a count (must stay ``int`` after clamping).
_INT_KEYS = frozenset({"max_iterations", "max_tokens", "max_tool_calls"})


def resolve_execution_budgets(
    *,
    platform_default: dict[str, Any] | None,
    project_override: dict[str, Any] | None,
) -> dict[str, float] | None:
    """Resolve the per-run budget envelope.

    ``platform_default`` is the platform-wide setting (operator default);
    ``project_override`` is the project's ``execution_budgets`` column. The
    project override takes precedence key-by-key. Every value is clamped to
    :data:`EXECUTION_BUDGET_CEILING`; unknown keys, non-numeric / non-positive
    values and ``max_review_retries`` are dropped.

    Returns ``None`` when nothing valid remains, so the dispatcher emits no
    ``budgets`` key and the agent-runtime falls back to its own dataclass
    defaults (which equal the ceiling).
    """
    merged: dict[str, Any] = {}
    for src in (platform_default, project_override):
        if isinstance(src, dict):
            merged.update(src)

    out: dict[str, float] = {}
    for key, ceiling in EXECUTION_BUDGET_CEILING.items():
        if key not in merged:
            continue
        raw = merged[key]
        # bool is an int subclass — reject it so True/False can't pose as a count.
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            continue
        if raw <= 0:
            continue
        value = min(float(raw), float(ceiling))
        out[key] = int(value) if key in _INT_KEYS else value

    return out or None
