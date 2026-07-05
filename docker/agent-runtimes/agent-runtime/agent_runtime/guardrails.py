"""Runtime guardrail seam — g1 minimal slice (ADR 0102 D1).

Runs the platform baseline ``post_tool`` guardrails (``prompt_injection`` in LOG
mode) over each tool result BEFORE it re-enters the model context, closing
indirect prompt injection: a page fetched via http/MCP or a RAG snippet carrying
``"ignore previous instructions…"`` no longer reaches the model unscreened
(audit 2026-07-03, g1 — principle 10). Everything here is BEST-EFFORT: any
failure yields no events and never breaks a run (LOG mode blocks nothing). The
triggered events accumulate in the result envelope; the worker persists them
(ADR 0102 D4). The full 4-hook + enforce scope is prod-03.
"""

from __future__ import annotations

from typing import Any

# Platform baseline used when the task spec carries no resolved guardrail config
# (bare run / minimal slice): scan tool OUTPUTS for prompt injection in LOG mode
# (warn + learning_mode) — detect + record, never block (ADR 0102 D1).
_BASELINE_CONFIG: dict[str, Any] = {
    "guardrails": {
        "post_tool": [
            {
                "type": "prompt_injection",
                "action": "warn",
                "config": {"learning_mode": True, "severity": "high"},
            }
        ]
    }
}

_MAX_STR = 40  # keep short labels; drop anything long enough to be a raw span


def build_pipeline(spec: dict[str, Any] | None) -> Any | None:
    """A GuardrailPipeline for this run, or None when guardrails are unavailable.

    Uses the resolved config the worker put on the task spec (``spec["guardrails"]``,
    ADR 0102 D3) when present; otherwise the platform baseline. Never raises — a
    missing/broken engine just means the run proceeds without guardrails.
    """
    try:
        from shared_guardrails.pipeline import GuardrailPipeline

        source = (spec or {}).get("guardrails") or _BASELINE_CONFIG
        return GuardrailPipeline.from_dict(source)
    except Exception:
        return None


def _safe_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Strip anything that could leak the raw injected span — keep only
    counts/offsets/short labels so the event is safe to persist."""
    out: dict[str, Any] = {}
    for key, value in (payload or {}).items():
        keep_scalar = isinstance(value, bool | int | float) or (
            isinstance(value, str) and len(value) <= _MAX_STR
        )
        if keep_scalar:
            out[key] = value
        elif isinstance(value, list | tuple):
            out[key] = [x for x in value if isinstance(x, int | float)][:20]
    return out


def run_hook(
    pipeline: Any | None,
    *,
    hook: str,
    tool_name: str | None = None,
    tool_result: Any = None,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run ``pipeline`` at ``hook`` and return JSON-safe events for the guardrails
    that TRIGGERED (never the raw span). Best-effort — ``[]`` on any error / when
    the pipeline is None. In the minimal slice actions are advisory (LOG); the
    caller records the events but does not block."""
    if pipeline is None:
        return []
    try:
        from shared_guardrails.types import GuardrailContext

        ctx = GuardrailContext(
            hook=hook,  # type: ignore[arg-type]
            tool_name=tool_name,
            tool_result=tool_result,
            metadata=metadata or {},
        )
        decision = pipeline.run(ctx)
        events: list[dict[str, Any]] = []
        for outcome in decision.triggered_outcomes:  # property, not a method
            action = outcome.action
            severity = outcome.severity
            events.append(
                {
                    "hook_point": hook,
                    "guardrail_type": outcome.type,
                    "severity": str(getattr(severity, "value", severity)),
                    "action": str(getattr(action, "value", action)) if action else None,
                    "detail": (outcome.detail or "")[:500],
                    "detail_payload": _safe_payload(outcome.payload),
                    "tool_name": tool_name,
                }
            )
        return events
    except Exception:
        return []
