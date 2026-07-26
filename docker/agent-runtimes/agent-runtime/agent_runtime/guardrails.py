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

import logging
from typing import Any

# A guardrail is a SECURITY control: when it silently fails open (engine missing,
# check raises) a 100%-broken screen looks identical to a clean run. Log the
# fail-open so operators can see it (stdlib lastResort → stderr, captured by the
# worker) instead of discovering it only when prod-03 flips LOG → enforce.
_log = logging.getLogger("agent_runtime.guardrails")

# Platform baseline used when the task spec carries no resolved guardrail config
# (bare run / minimal slice): scan for prompt injection in LOG mode (warn +
# learning_mode) — detect + record, never block (ADR 0102 D1).
#
# `task_wf_50`: el baseline solo cubría `post_tool`, así que aunque los cuatro
# hooks del principio rector 10 estuvieran cableados, dos no tenían nada que
# ejecutar. `pre_llm` y `post_llm` entran aquí con la MISMA acción `warn`: lo
# que cambia es QUÉ se mira, no qué se hace con ello — ningún run cambia de
# resultado hasta que un tenant endurezca su política a `block`.
#
# Por qué los tres y no solo `post_tool`: el hook de tool ve cada resultado
# cuando ENTRA, una vez. `pre_llm` ve lo que de VERDAD se manda al modelo, que
# incluye lo acumulado en turnos anteriores y los preámbulos que arma la
# plataforma (comentarios del humano, memoria recuperada, resúmenes de tareas
# previas) — ninguno de los cuales pasa por una tool.
_INJECTION_RULE: dict[str, Any] = {
    "type": "prompt_injection",
    "action": "warn",
    "config": {"learning_mode": True, "severity": "high"},
}

_BASELINE_CONFIG: dict[str, Any] = {
    "guardrails": {
        "pre_llm": [dict(_INJECTION_RULE)],
        "post_llm": [dict(_INJECTION_RULE)],
        "post_tool": [dict(_INJECTION_RULE)],
    }
}

_MAX_STR = 40  # keep short labels; drop anything long enough to be a raw span

# ADR 0102 D6: tope del INPUT que un hook escanea — un output de MB no puede
# costar un escaneo de MB (regex/heurísticas son O(n)). El truncado se marca
# en metadata para que el evento sea honesto sobre lo que vio.
_HOOK_INPUT_MAX = 50_000


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
        _log.warning("guardrail pipeline unavailable; run proceeds UNSCREENED", exc_info=True)
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
    tool_args: dict[str, Any] | None = None,
    tool_result: Any = None,
    prompt: str | None = None,
    response: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run ``pipeline`` at ``hook`` and return JSON-safe events for the guardrails
    that TRIGGERED (never the raw span). Best-effort — ``[]`` on any error / when
    the pipeline is None. Las acciones viajan en el evento: el caller decide si
    aplica un ``block`` (ADR 0102 D2, `act`) o lo registra advisory (LOG)."""
    if pipeline is None:
        return []
    try:
        from shared_guardrails.types import GuardrailContext

        meta = dict(metadata or {})
        # ADR 0102 D6: truncado del input escaneado, marcado en metadata.
        # `task_wf_50`: aplica a los TRES campos de texto — `prompt` y
        # `response` son los que leen `pre_llm` / `post_llm`
        # (`GuardrailContext.primary_text`), y un prompt largo cuesta tanto de
        # escanear como un output largo.
        if isinstance(tool_result, str) and len(tool_result) > _HOOK_INPUT_MAX:
            tool_result = tool_result[:_HOOK_INPUT_MAX]
            meta["truncated"] = True
        if isinstance(prompt, str) and len(prompt) > _HOOK_INPUT_MAX:
            prompt = prompt[:_HOOK_INPUT_MAX]
            meta["truncated"] = True
        if isinstance(response, str) and len(response) > _HOOK_INPUT_MAX:
            response = response[:_HOOK_INPUT_MAX]
            meta["truncated"] = True
        ctx = GuardrailContext(
            hook=hook,  # type: ignore[arg-type]
            tool_name=tool_name,
            tool_args=tool_args or {},
            tool_result=tool_result,
            prompt=prompt,
            response=response,
            metadata=meta,
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
        _log.warning("guardrail hook %s failed; tool output NOT screened", hook, exc_info=True)
        return []
