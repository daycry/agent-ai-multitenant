"""Guardrail-event recorder service (Plan 11 Fase E, task_11_20).

When a guardrail *triggers* anywhere in the platform, the host records it
as one append-only ``guardrail_events`` row (tenant-scoped) so the tenant
dashboard (task_11_20) and alerts (task_11_21) can observe it. This module
is the seam the host calls:

  - :func:`record_guardrail_event` — write ONE event from explicit fields.
    The low-level recorder.
  - :func:`record_pipeline_decision` — the hook the **pipeline host** calls
    after running the engine for a hook point. Given the engine's
    :class:`PipelineDecision`, it persists one row per *triggered*
    guardrail. This is the wiring point task_11_22 reuses when it runs the
    engine in the planning chat.

**The redacted-detail invariant (CLAUDE.md: NO plaintext secrets; PII
masking).** The recorder NEVER stores the raw value that tripped a
guardrail. Two layers protect that:

  1. The built-in guardrails already surface a *masked* ``detail`` string
     and a payload whose spans are offsets + family only (the raw secret
     never appears) — see ``secret_leakage`` / ``pii``.
  2. This recorder is defensive on top of that: it copies only a curated
     **allowlist** of safe payload keys into ``detail_payload`` and
     explicitly DROPS any key known to (or that could) carry raw content
     (``redacted_text`` / ``matched_text`` / ``prompt`` / ``response`` /
     ``text`` / ``value`` / ``raw`` / ``secret`` / ``tool_result`` …).
     So even a future guardrail that carelessly puts raw text in its
     payload cannot leak it into the persisted event.

The engine is host-agnostic, so this recorder takes the
``shared_guardrails`` decision + a small :class:`GuardrailEventContext`
(tenant + the project / agent / execution refs) rather than importing any
chat/execution module — keeping the dependency arrow pointing inward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from shared_guardrails.types import GuardrailOutcome, PipelineDecision
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.guardrail_event import GuardrailEvent

# Payload keys that are SAFE to persist verbatim — non-sensitive metadata a
# guardrail emits (families, counts, offsets, the resolved schema error, the
# domains it checked, …). Anything NOT in this allowlist is dropped, so a raw
# secret / PII value can never reach the persisted row even if a guardrail
# carelessly put one in its payload.
_SAFE_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        # secret_leakage / pii: families + counts + offset-only spans.
        "secret_types",
        "pii_types",
        "entity_types",
        "count",
        "spans",
        "categories",
        "category",
        # content_safety / prompt_injection: classifier verdicts + reasons.
        "verdict",
        "reason",
        "available",
        "detector",
        "matched_categories",
        # output_structure: the schema error (a validation message, not data).
        "schema_error",
        "schema_path",
        "valid",
        # allowed_domains / forbidden_actions: the offending hosts / tools.
        "blocked_domains",
        "allowed_domains",
        "blocked_tools",
        "tool_name",
        # cost_ceiling / rate_per_agent: numeric thresholds.
        "limit",
        "threshold",
        "current",
        "cost",
        "window_seconds",
    }
)

# Payload keys that MUST be stripped — they may carry the raw value / text
# that triggered the guardrail. Explicit denylist as defence in depth on top
# of the allowlist (a key not in either set is dropped by the allowlist; this
# set documents the dangerous ones and guards against an accidental allowlist
# addition).
_UNSAFE_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "redacted_text",
        "matched_text",
        "matched",
        "raw",
        "raw_text",
        "value",
        "values",
        "secret",
        "secrets",
        "text",
        "prompt",
        "response",
        "tool_result",
        "tool_args",
        "content",
        "snippet",
        "sample",
    }
)


@dataclass
class GuardrailEventContext:
    """The tenant + refs a triggered guardrail is attributed to.

    ``tenant_id`` is REQUIRED — a guardrail event is tenant-owned data, so
    there is no platform / NULL-tenant branch (unlike notifications). The
    refs are optional: the planning chat fires guardrails before an
    execution exists, so it supplies ``agent_label`` instead of
    ``execution_id`` / ``agent_id``.
    """

    tenant_id: UUID
    project_id: UUID | None = None
    agent_id: UUID | None = None
    execution_id: UUID | None = None
    agent_label: str | None = None
    # Extra non-sensitive metadata to merge into detail_payload (already
    # safe — the caller is responsible; it is still filtered through the
    # allowlist before persisting).
    extra: dict[str, Any] = field(default_factory=dict)


def _mask_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return only the allowlisted, non-sensitive keys of a guardrail payload.

    Drops every key not in :data:`_SAFE_PAYLOAD_KEYS` (and, redundantly,
    every key in :data:`_UNSAFE_PAYLOAD_KEYS`). This is the line that keeps
    a raw secret / PII value out of the persisted event even if a guardrail
    put one in its payload.
    """
    masked: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _UNSAFE_PAYLOAD_KEYS:
            continue
        if key not in _SAFE_PAYLOAD_KEYS:
            continue
        masked[key] = value
    return masked


async def record_guardrail_event(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    guardrail_type: str,
    hook_point: str,
    severity: str,
    action: str | None,
    detail: str,
    detail_payload: dict[str, Any] | None = None,
    project_id: UUID | None = None,
    agent_id: UUID | None = None,
    execution_id: UUID | None = None,
    agent_label: str | None = None,
) -> GuardrailEvent:
    """Write ONE append-only ``guardrail_events`` row (the low-level recorder).

    ``detail`` is expected to already be a masked summary (the built-in
    guardrails produce one). ``detail_payload`` is filtered through the
    allowlist here, so the raw secret / PII never lands in the DB even if a
    caller passes a payload that carries one.

    The row is added to ``session`` but NOT committed — the caller owns the
    transaction (so the event can be written atomically with the work that
    produced it). Runs under the tenant-scoped RLS session, so the row's
    ``tenant_id`` must match the bound ``app.tenant_id`` GUC.
    """
    event = GuardrailEvent(
        tenant_id=tenant_id,
        guardrail_type=guardrail_type,
        hook_point=hook_point,
        severity=severity,
        action=action,
        detail=detail,
        detail_payload=_mask_payload(detail_payload or {}),
        project_id=project_id,
        agent_id=agent_id,
        execution_id=execution_id,
        agent_label=agent_label,
    )
    session.add(event)
    await session.flush()
    return event


async def record_outcome(
    session: AsyncSession,
    outcome: GuardrailOutcome,
    *,
    hook_point: str,
    context: GuardrailEventContext,
) -> GuardrailEvent:
    """Persist one *triggered* :class:`GuardrailOutcome` as an event row.

    Builds ``detail_payload`` from the outcome's payload (masked) merged
    with the context's ``extra`` (also masked), and uses the outcome's own
    (already masked) ``detail`` string.
    """
    payload = dict(outcome.payload)
    if context.extra:
        payload.update(context.extra)
    return await record_guardrail_event(
        session,
        tenant_id=context.tenant_id,
        guardrail_type=outcome.type,
        hook_point=hook_point,
        severity=outcome.severity.value,
        action=outcome.action.value if outcome.action is not None else None,
        detail=outcome.detail,
        detail_payload=payload,
        project_id=context.project_id,
        agent_id=context.agent_id,
        execution_id=context.execution_id,
        agent_label=context.agent_label,
    )


async def record_pipeline_decision(
    session: AsyncSession,
    decision: PipelineDecision,
    *,
    context: GuardrailEventContext,
) -> list[GuardrailEvent]:
    """The hook the pipeline host calls after running the engine.

    Persists one ``guardrail_events`` row per *triggered* guardrail in the
    decision (a non-triggered pipeline writes nothing). Returns the rows
    written, in engine order. The caller owns the transaction / commit.

    This is the single wiring point: any host that runs the
    :class:`GuardrailPipeline` (the planning chat in task_11_22, an
    execution worker, …) calls this with the decision + a context to make
    the firing observable in the tenant dashboard.
    """
    written: list[GuardrailEvent] = []
    for outcome in decision.triggered_outcomes:
        written.append(
            await record_outcome(session, outcome, hook_point=decision.hook, context=context)
        )
    return written


__all__ = [
    "GuardrailEventContext",
    "record_guardrail_event",
    "record_outcome",
    "record_pipeline_decision",
]
