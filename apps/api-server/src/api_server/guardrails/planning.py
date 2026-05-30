"""Planning-chat guardrails wiring (Plan 11 Fase E, task_11_22).

The Plan 03 planning chat (the multi-agent ``planning_graph`` + the
plan-generation flow) is a sensitive path: the team converses freely with
the human and then formalises a *draft plan* that the "Generar Plan" action
persists. This module wires the **Phase A/B guardrails engine** into that
path with three planning-specific guardrails — all of them reuse built-in
guardrail *types*, no new check is implemented here:

  1. **topic adherence** — the conversation must stay on the
     project / planning topic. Reuses the ``topic_restriction`` built-in,
     run at ``pre_llm`` (is the human steering the chat off-topic?) and
     ``post_llm`` (did the team's answer drift off-topic?). Default action
     ``warn`` (a planning chat should *nudge*, not hard-block, off-topic
     turns).
  2. **hallucination check over NUMBERS** — estimates / costs / dates the
     plan asserts must be flagged when unsupported. Reuses the
     ``factuality_citations`` built-in (a factuality-style heuristic that
     fires on numeric / quoted claims lacking a citation), run at
     ``post_llm``. Default action ``warn``.
  3. **structural validation before "Generar Plan"** — the draft plan must
     satisfy the expected canonical-template structure / required fields.
     Reuses the ``output_structure`` built-in (JSON-Schema) as a *gate*: a
     structurally invalid draft BLOCKS plan generation and returns
     actionable feedback (the JSON-Schema validation errors), while a valid
     draft passes. Run as a ``post_llm`` hook over the serialised draft.

Hooks
-----
The chat-turn guardrails (1 + 2) run at the natural ``pre_llm`` /
``post_llm`` points of one planning turn. The structural gate (3) is a
dedicated check the plan-generation endpoint calls *before* it writes the
plan — a "structural gate" in front of the "Generar Plan" action, not a
chat turn.

Observability + multi-tenancy
------------------------------
Every triggered guardrail is persisted as a tenant-scoped
``guardrail_events`` row through the task_11_20 recorder
(:func:`api_server.guardrails.events.record_pipeline_decision`), so a
planning-chat violation shows up in the tenant's guardrails dashboard and
feeds the task_11_21 alerts. The recorder masks the detail — no raw PII /
secret is persisted (the planning chat may carry both). The
:class:`GuardrailEventContext` is built from the tenant + project + a stable
``agent_label`` (``"planning_chat"`` / ``"plan_generation"``) because the
planning chat fires guardrails *before* an execution exists, so there is no
``execution_id`` / ``agent_id`` yet.

The engine itself is pure (no DB / no I/O); this module is the host seam
that resolves the planning config, runs the pipeline, and persists the
decision. It imports nothing from the chat graph — the caller passes the
text / draft in — so the dependency arrow points inward.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from shared_guardrails.pipeline import GuardrailPipeline
from shared_guardrails.types import (
    Action,
    GuardrailContext,
    HookPoint,
    PipelineDecision,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.guardrails.events import (
    GuardrailEventContext,
    record_pipeline_decision,
)

# Stable agent labels for the planning-chat guardrail events. The planning
# chat fires guardrails before an execution / agent row exists, so the event
# carries this free-form label instead of an agent_id (the guardrail_events
# row supports exactly this — see api_server.db.guardrail_event).
AGENT_LABEL_CHAT = "planning_chat"
AGENT_LABEL_GENERATION = "plan_generation"

# The default planning topics the conversation must touch — the "topic
# adherence" baseline. Cues are deliberately broad (es + en) so on-topic
# software-planning chatter is recognised and obviously off-topic detours
# (recipes, sports, ...) are flagged. The host may override per project /
# tenant by passing its own ``allowed_topics`` to :func:`build_planning_chat_pipeline`.
DEFAULT_PLANNING_TOPICS: dict[str, list[str]] = {
    "project": ["project", "proyecto", "product", "producto", "feature", "funcionalidad"],
    "planning": ["plan", "planning", "planificación", "roadmap", "milestone", "hito", "sprint"],
    "tasks": ["task", "tarea", "backlog", "epic", "historia", "story", "deliverable", "entregable"],
    "engineering": [
        "code",
        "código",
        "api",
        "database",
        "base de datos",
        "deploy",
        "test",
        "prueba",
        "architecture",
        "arquitectura",
        "frontend",
        "backend",
    ],
    "estimates": ["estimate", "estimación", "cost", "coste", "budget", "presupuesto", "timeline"],
}


# ---------------------------------------------------------------------------
# JSON Schema for the draft plan structure (the "Generar Plan" gate).
# ---------------------------------------------------------------------------
# The canonical-template specification lives in ``plans.specification``
# (Plan 03 §8.5) as ``{summary, phases, tasks, estimates, metadata}``. The
# structural gate requires the parts a *generatable* plan must carry: a
# non-empty ``summary``, at least one ``task``, and every task must have a
# string ``id`` and ``title`` plus an optional ``depends_on`` list. This is
# intentionally a *structural* check (shape / required fields), not a
# semantic one — the DAG-cycle / dependency-reference checks already live in
# ``api_server.chat.dag`` + the Pydantic ``PlanSpecification`` validator and
# run in the router. The gate's job is to stop a malformed draft from ever
# reaching "Generar Plan".
PLAN_DRAFT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["summary", "tasks"],
    "properties": {
        "summary": {
            "type": "object",
            "minProperties": 1,
        },
        "phases": {"type": "array"},
        "estimates": {"type": "object"},
        "metadata": {"type": "object"},
        "tasks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "title"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "title": {"type": "string", "minLength": 1},
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}


@dataclass(frozen=True)
class PlanGateResult:
    """Outcome of the structural gate in front of "Generar Plan".

    ``allowed`` is ``False`` when the draft is structurally invalid (the
    gate BLOCKS plan generation). ``feedback`` carries the actionable,
    human-readable validation errors (the JSON-Schema messages + their JSON
    paths) so the UI / chat can tell the team exactly what to fix. The full
    engine decision is preserved for the caller / persistence.
    """

    allowed: bool
    feedback: tuple[str, ...]
    decision: PipelineDecision


def build_planning_chat_pipeline(
    *,
    allowed_topics: dict[str, list[str]] | None = None,
    topic_action: Action = Action.WARN,
    factuality_action: Action = Action.WARN,
) -> GuardrailPipeline:
    """Build the planning chat-turn pipeline (topic adherence + numbers).

    Reuses the ``topic_restriction`` and ``factuality_citations`` built-ins
    from Phase B — no new guardrail is implemented. The pipeline runs:

      - ``pre_llm``  : topic adherence on the user's input.
      - ``post_llm`` : topic adherence + the numbers hallucination check on
        the team's answer.

    The actions default to ``warn`` (a planning chat should surface, not
    hard-block, drift / unsupported numbers). The caller may pass its own
    ``allowed_topics`` (per project / tenant) or stricter actions.
    """
    topics = allowed_topics if allowed_topics is not None else DEFAULT_PLANNING_TOPICS
    topic_spec = {
        "type": "topic_restriction",
        "action": topic_action.value,
        "config": {"allowed_topics": topics, "severity": "low"},
    }
    factuality_spec = {
        "type": "factuality_citations",
        "action": factuality_action.value,
        # ``require_document_citation`` so an unsupported NUMBER asserted in
        # the plan is flagged even when an unrelated citation appears
        # elsewhere — the plan's "hallucination check over numbers".
        "config": {"require_document_citation": True, "severity": "low"},
    }
    return GuardrailPipeline.from_dict(
        {
            "guardrails": {
                "pre_llm": [topic_spec],
                "post_llm": [topic_spec, factuality_spec],
            }
        }
    )


def build_plan_structure_pipeline(
    *,
    schema: dict[str, Any] | None = None,
) -> GuardrailPipeline:
    """Build the structural-gate pipeline for the "Generar Plan" action.

    Reuses the ``output_structure`` built-in (JSON-Schema) at ``post_llm``
    with ``action=block`` — a structurally invalid draft is blocked, not
    retried, because the gate stands in front of an explicit user action.
    """
    return GuardrailPipeline.from_dict(
        {
            "guardrails": {
                "post_llm": [
                    {
                        "type": "output_structure",
                        "action": "block",
                        "config": {
                            "schema": schema if schema is not None else PLAN_DRAFT_SCHEMA,
                            "severity": "high",
                        },
                    }
                ]
            }
        }
    )


async def run_planning_chat_guardrails(
    session: AsyncSession,
    *,
    hook: HookPoint,
    text: str,
    tenant_id: UUID,
    project_id: UUID | None = None,
    pipeline: GuardrailPipeline | None = None,
    allowed_topics: dict[str, list[str]] | None = None,
) -> PipelineDecision:
    """Run the planning chat-turn guardrails for one hook and persist events.

    ``hook`` is ``pre_llm`` (the user's input) or ``post_llm`` (the team's
    synthesised answer); ``text`` is the corresponding message body. The
    decision's triggered guardrails are persisted as tenant-scoped
    ``guardrail_events`` rows via the task_11_20 recorder (masked detail —
    no raw PII / secret reaches the DB). The caller owns the transaction /
    commit. Returns the engine decision so the chat host can act on it
    (warn the user, annotate the turn, ...).
    """
    pipe = (
        pipeline
        if pipeline is not None
        else build_planning_chat_pipeline(allowed_topics=allowed_topics)
    )
    context = GuardrailContext(
        hook=hook,
        prompt=text if hook == "pre_llm" else None,
        response=text if hook == "post_llm" else None,
        metadata={"tenant_id": str(tenant_id), "source": AGENT_LABEL_CHAT},
    )
    decision = pipe.run(context)
    await record_pipeline_decision(
        session,
        decision,
        context=GuardrailEventContext(
            tenant_id=tenant_id,
            project_id=project_id,
            agent_label=AGENT_LABEL_CHAT,
        ),
    )
    return decision


def _structure_feedback(decision: PipelineDecision) -> tuple[str, ...]:
    """Build actionable feedback from the output_structure outcome(s).

    Surfaces the JSON-Schema validation messages (and their JSON paths)
    the ``output_structure`` guardrail emitted, so the team can see exactly
    which required field / shape the draft is missing.
    """
    feedback: list[str] = []
    for outcome in decision.triggered_outcomes:
        if outcome.type != "output_structure":
            continue
        payload = outcome.payload
        reason = payload.get("reason")
        if reason == "not_json":
            feedback.append("Draft plan is not a valid JSON object; cannot validate its structure.")
            continue
        errors = payload.get("errors") or []
        paths = payload.get("error_paths") or []
        if not errors:
            feedback.append(outcome.detail)
            continue
        for idx, message in enumerate(errors):
            path = paths[idx] if idx < len(paths) else "$"
            feedback.append(f"{path}: {message}")
    return tuple(feedback)


async def gate_generate_plan(
    session: AsyncSession,
    *,
    draft: dict[str, Any],
    tenant_id: UUID,
    project_id: UUID | None = None,
    pipeline: GuardrailPipeline | None = None,
    schema: dict[str, Any] | None = None,
) -> PlanGateResult:
    """Structural gate run BEFORE the "Generar Plan" action.

    Validates the draft plan ``specification`` against the expected
    structure (:data:`PLAN_DRAFT_SCHEMA`) via the ``output_structure``
    built-in. A structurally invalid draft is BLOCKED (``allowed=False``)
    with actionable ``feedback`` (the JSON-Schema errors); a valid draft
    passes (``allowed=True``). The triggered guardrail is persisted as a
    tenant-scoped event (masked). The caller owns the transaction.

    The draft is serialised to JSON for the guardrail's text input, so the
    same ``output_structure`` check the rest of the platform uses validates
    it — no bespoke structural code here.
    """
    pipe = pipeline if pipeline is not None else build_plan_structure_pipeline(schema=schema)
    context = GuardrailContext(
        hook="post_llm",
        response=json.dumps(draft, ensure_ascii=False, default=str),
        metadata={"tenant_id": str(tenant_id), "source": AGENT_LABEL_GENERATION},
    )
    decision = pipe.run(context)
    await record_pipeline_decision(
        session,
        decision,
        context=GuardrailEventContext(
            tenant_id=tenant_id,
            project_id=project_id,
            agent_label=AGENT_LABEL_GENERATION,
        ),
    )
    # ``output_structure`` triggers on a structural problem; only ``block``
    # gates the action. A non-triggered (valid) draft is allowed.
    allowed = decision.action != Action.BLOCK
    feedback = () if allowed else _structure_feedback(decision)
    return PlanGateResult(allowed=allowed, feedback=feedback, decision=decision)


__all__ = [
    "AGENT_LABEL_CHAT",
    "AGENT_LABEL_GENERATION",
    "DEFAULT_PLANNING_TOPICS",
    "PLAN_DRAFT_SCHEMA",
    "PlanGateResult",
    "build_plan_structure_pipeline",
    "build_planning_chat_pipeline",
    "gate_generate_plan",
    "run_planning_chat_guardrails",
]
