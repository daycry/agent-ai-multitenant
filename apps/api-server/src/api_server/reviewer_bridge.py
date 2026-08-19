"""Parse the reviewer agent's output and apply its verdict (Plan 06.5
task_06_5_15).

The reviewer's system prompt (seed `builtin_agents.py`) instructs the
LLM to finish its review with structured tags:

    <verdict>approve</verdict>
    <verdict>reject</verdict>
      <rejection>
        <failed_criterion>...</failed_criterion>
        <testreport_evidence>...</testreport_evidence>
        <what_to_fix>...</what_to_fix>
        <reject_target>code, tests</reject_target>      # `task_gov_10`
        <reject_class>incorrect</reject_class>          # `task_gov_10`
      </rejection>

`task_gov_10`: los dos últimos son el par ACOTADO del rechazo — vocabulario
cerrado en `shared_domain.reject_taxonomy`, tope de tres por eje, y lo genérico
se DESCARTA en vez de guardarse. Es la única parte del veredicto que agrega: la
prosa contesta «¿qué arreglo ahora?» y el par contesta «¿qué se rechaza más en
este proyecto?».

The orchestrator (Plan 06.5 Fase F) feeds the agent's stdout through
`parse_reviewer_output` to extract a typed `ReviewerVerdict`, then
calls `apply_reviewer_verdict` which:

  * On `approve` → nothing; the task continues to PR / human validation.
  * On `reject`  → DB-side equivalent of
                   `TaskLifecycle.reject_review(task_id, ReviewComment)`
                   — task back to `backlog`, retry_count++, audit event.

Parsing is intentionally forgiving: missing tags → unknown verdict
(treated as approve by default), missing rejection fields → empty
strings. The orchestrator can re-prompt the agent if the output is
unparseable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import structlog
from shared_domain.reject_taxonomy import (
    REJECT_CLASS_TAG,
    REJECT_TARGET_TAG,
    normalise_classes,
    normalise_targets,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Task, TaskStatus
from api_server.db.task_audit_repo import append_audit_event
from api_server.task_state_machine import transition_task_status

_log = structlog.get_logger("api_server.reviewer_bridge")

VerdictLabel = Literal["approve", "reject", "unknown"]


@dataclass(frozen=True)
class CriterionOutcome:
    """El veredicto de UN criterio de aceptación (`task_wf_61`)."""

    text: str
    passed: bool
    evidence: str = ""

    def as_dict(self) -> dict[str, object]:
        return {"text": self.text, "passed": self.passed, "evidence": self.evidence}


@dataclass(frozen=True)
class ReviewerVerdict:
    """Structured outcome of one reviewer turn.

    ``label`` is the parsed `<verdict>` tag. The three rejection fields
    are non-empty only when ``label == 'reject'``.

    `task_wf_61`: ``criteria`` es el desglose POR CRITERIO cuando el reviewer lo
    emite. Es ADITIVO — el `<verdict>` sigue mandando — así que un reviewer que
    no lo emita (o un modelo que se lo salte) se comporta exactamente como antes.

    `task_gov_10`: ``reject_targets`` x ``reject_classes`` es el par ACOTADO del
    rechazo (vocabulario en ``shared_domain.reject_taxonomy``), lo único de este
    veredicto que se puede AGREGAR — la prosa sirve para el reintento inmediato y
    no responde «¿qué se rechaza más?». También aditivo: `()` cuando el reviewer
    no emite los tags o emite valores fuera del vocabulario, y `()` es un
    resultado legítimo (el rechazo queda sin clasificar y el agregado lo cuenta
    aparte) — NO hay bucket «otros» al que caer.
    """

    label: VerdictLabel
    failed_criterion: str = ""
    testreport_evidence: str = ""
    what_to_fix: str = ""
    criteria: tuple[CriterionOutcome, ...] = ()
    reject_targets: tuple[str, ...] = ()
    reject_classes: tuple[str, ...] = ()


# Audit cluster C1 (F37): capture the `<verdict>` tag BODY and normalise it,
# instead of demanding an EXACT `approve`/`reject` token. Models — especially
# non-Claude (ollama/azure/copilot) — drift from the exact shape
# ("<verdict>approve - LGTM</verdict>", "<verdict>I approve</verdict>"); the old
# strict regex read those as `unknown`, which the worker turned into a defensive
# reject and the task ended wrongly blocked. The tag itself is still required (a
# bare "approved" in prose is NOT honoured — too risky for false positives).
_VERDICT_RE = re.compile(r"<verdict>(.*?)</verdict>", re.IGNORECASE | re.DOTALL)
_FAILED_RE = re.compile(r"<failed_criterion>(.*?)</failed_criterion>", re.IGNORECASE | re.DOTALL)
_EVIDENCE_RE = re.compile(
    r"<testreport_evidence>(.*?)</testreport_evidence>", re.IGNORECASE | re.DOTALL
)
_WHAT_TO_FIX_RE = re.compile(r"<what_to_fix>(.*?)</what_to_fix>", re.IGNORECASE | re.DOTALL)

# `task_wf_61`: el desglose por criterio. Formato de LÍNEA y no de tags
# anidados: el modelo lo produce sin equivocarse, un humano lo lee tal cual en
# la UI, y el marcador `[pass]`/`[fail]` resiste la deriva de redacción que ya
# obligó a parsear el `<verdict>` con tolerancia.
_CRITERIA_BLOCK_RE = re.compile(r"<criteria>(.*?)</criteria>", re.IGNORECASE | re.DOTALL)
_CRITERION_LINE_RE = re.compile(r"^\s*[-*]?\s*\[\s*(pass|fail)\s*\]\s*(.+?)$", re.IGNORECASE)
# La evidencia va tras un guión largo o el literal `evidence:`; las dos formas
# porque el modelo alterna entre ellas y perder la evidencia por el separador
# sería tirar justo la parte accionable.
_EVIDENCE_SPLIT_RE = re.compile(r"\s+(?:—|--)\s+evidence:\s*|\s+evidence:\s*", re.IGNORECASE)


# `task_gov_10`: los dos ejes del rechazo. Tags planos (no anidados) por la misma
# razón que el bloque de criteria usa líneas: el modelo los produce sin
# equivocarse.
#
# Los nombres de los tags NO se teclean aquí: se construyen desde
# `shared_domain.reject_taxonomy`, la misma declaración con la que el runtime
# ARMA la instrucción del prompt. Así el anuncio y el parseo son literalmente la
# misma cadena y no pueden derivar — que es cómo se rompió el tag `<verdict>`
# (deletreado a mano en cinco prompts, hallazgo H3) y cómo se rompieron las 13
# categorías de aprobación (hallazgo g6). El VOCABULARIO tampoco se valida aquí:
# lo cierra `normalise_*`.
#
# Se admite el PLURAL del tag porque un modelo que emite dos etiquetas escribe
# `<reject_targets>` la mitad de las veces, y perder el par por una `s` sería
# tirar justo el dato que la casilla viene a producir.
def _tag_re(tag: str, *, plural: str) -> re.Pattern[str]:
    name = rf"{re.escape(tag)}(?:{plural})?"
    return re.compile(rf"<{name}>(.*?)</{name}>", re.IGNORECASE | re.DOTALL)


_REJECT_TARGET_RE = _tag_re(REJECT_TARGET_TAG, plural="s")
_REJECT_CLASS_RE = _tag_re(REJECT_CLASS_TAG, plural="es")


def parse_criteria_block(text: str) -> tuple[CriterionOutcome, ...]:
    """El desglose por criterio del veredicto, o `()` si no lo hay.

    Tolerante por diseño: una línea que no encaje se ignora en vez de tirar el
    bloque entero — un desglose parcial informa más que ninguno, y el
    `<verdict>` sigue siendo la fuente autoritativa pase lo que pase aquí.
    """
    block = _CRITERIA_BLOCK_RE.search(text or "")
    if not block:
        return ()
    out: list[CriterionOutcome] = []
    for raw_line in block.group(1).splitlines():
        match = _CRITERION_LINE_RE.match(raw_line)
        if not match:
            continue
        status, rest = match.group(1).lower(), match.group(2).strip()
        parts = _EVIDENCE_SPLIT_RE.split(rest, maxsplit=1)
        criterion = parts[0].strip(" .—-")
        evidence = parts[1].strip() if len(parts) > 1 else ""
        if criterion:
            out.append(CriterionOutcome(text=criterion, passed=status == "pass", evidence=evidence))
    return tuple(out)


def _normalise_verdict(body: str) -> VerdictLabel:
    """Map a ``<verdict>`` tag body to approve / reject / unknown.

    Reject is checked first so an explicit "do not approve — reject" reads as a
    reject; the stems catch approve/approved/approval and reject/rejected.
    """
    text = body.strip().lower()
    if "reject" in text:
        return "reject"
    if "approv" in text:
        return "approve"
    return "unknown"


def parse_reviewer_output(text: str) -> ReviewerVerdict:
    """Extract the verdict tags from the LLM's free-form output.

    Returns ``ReviewerVerdict(label='unknown')`` if no decisive `<verdict>` tag
    is found. Multiple `<verdict>` tags resolve to the LAST decisive one (the
    agent may have changed its mind mid-output; we honour the final call). The
    tag body is matched tolerantly (`_normalise_verdict`) so minor format drift
    no longer flips a real verdict to `unknown`.

    ADR 0108 (ancla): este es UNO de los DOS canales de veredicto y la
    divergencia es INTENCIONAL — el run reviewer externo cierra un loop
    multi-turn cuyo FINISH en claude_sdk es prosa (un tool call forzaría
    ``content=""`` y perdería el resumen), así que su veredicto viaja como tag
    parseado aquí; la self-review interna es single-turn con ``tool_choice``
    forzable y usa la tool ``submit_verdict``
    (``agent_runtime/providers.py::_review_from``). Tolerancias distintas a
    propósito: aquí ``unknown → reject`` defensivo; el runtime hace
    ``inconclusive → humano``. Fuente única del wire-format:
    ``agent_runtime/review_contract.py`` + ``test_review_verdict_wire_contract``.
    Antes de unificar canales, leer el ADR 0108 (opciones A/B/C y riesgos).
    """
    label: VerdictLabel = "unknown"
    for body in _VERDICT_RE.findall(text or ""):
        candidate = _normalise_verdict(body)
        if candidate != "unknown":
            label = candidate
    criteria = parse_criteria_block(text or "")
    if label != "reject":
        # El desglose viaja también en un APPROVE: saber qué se comprobó vale
        # tanto como saber qué falló, y es lo que hace medible el review.
        return ReviewerVerdict(label=label, criteria=criteria)

    def _grab(pattern: re.Pattern[str]) -> str:
        m = pattern.search(text)
        return m.group(1).strip() if m else ""

    failed_criterion = _grab(_FAILED_RE)
    if not failed_criterion and criteria:
        # Diana derivada: con el desglose, el criterio que falló ya está dicho.
        # Antes un reject sin `<failed_criterion>` dejaba al implementador sin
        # saber QUÉ arreglar.
        failed_criterion = "; ".join(c.text for c in criteria if not c.passed)
    return ReviewerVerdict(
        label="reject",
        failed_criterion=failed_criterion,
        testreport_evidence=_grab(_EVIDENCE_RE),
        what_to_fix=_grab(_WHAT_TO_FIX_RE),
        criteria=criteria,
        # Se leen TODAS las apariciones y se concatenan antes de normalizar: un
        # reviewer que emite un tag por etiqueta (dos `<reject_class>`) dice lo
        # mismo que quien las lista separadas por comas, y quedarse con la
        # primera perdería la mitad del par. El tope de tres y el descarte de lo
        # genérico los aplica `normalise_*`, no este código.
        reject_targets=normalise_targets(_REJECT_TARGET_RE.findall(text or "")),
        reject_classes=normalise_classes(_REJECT_CLASS_RE.findall(text or "")),
    )


async def apply_reviewer_verdict(
    session: AsyncSession,
    *,
    task_id: UUID,
    tenant_id: UUID,
    verdict: ReviewerVerdict,
    reviewer_actor: str = "agent:reviewer",
) -> dict[str, object]:
    """Apply the AI reviewer's verdict to a task in ``in_review`` (prod-17 Fase A).

    Returns ``{action, verdict, task_id, task_status?, retry_count?, event_id?}``.

    Verdicts (all moves go through the §7.2 state machine, never a raw mutation):

      * ``approve`` → ``in_review → done`` (+ ``completed_at``). ``action='approved'``.
      * ``reject`` with ``retry_count < max_retries`` → ``backlog`` + ``retry_count++``
        + one audit ``review_comment`` (the ``ReviewComment`` shape). ``action='rejected'``.
      * ``reject`` reaching ``max_retries`` → ``blocked`` (DB-legal escalation from
        ``in_review`` — ``awaiting_human_approval`` is NOT reachable from there; it is
        ADR 0020's approval-engine state). The audit payload carries ``reason=max_retries``.
        ``action='escalated'``.
      * ``unknown`` → no-op; the caller re-prompts the reviewer.

    Idempotency: a verdict on a task that is no longer ``in_review`` (a stale or
    re-delivered review execution, a task cancelled meanwhile) is a guarded no-op
    (``note='not_in_review'``) — never raises, never re-acts. The task is loaded with
    an explicit ``tenant_id`` predicate (defence in depth beyond RLS).
    """
    if verdict.label == "unknown":
        return {"action": "noop", "verdict": "unknown", "task_id": str(task_id)}

    task_row = (
        await session.execute(select(Task).where(Task.id == task_id, Task.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if task_row is None:
        raise ValueError(f"task {task_id!r} not visible to current session")

    # Only act on a task awaiting review — guards stale/duplicate verdicts.
    if task_row.status != TaskStatus.IN_REVIEW.value:
        return {
            "action": "noop",
            "verdict": verdict.label,
            "task_id": str(task_id),
            "task_status": task_row.status,
            "note": "not_in_review",
        }

    if verdict.label == "approve":
        transition_task_status(task_row, TaskStatus.DONE.value)
        task_row.completed_at = datetime.now(UTC)
        # `task_wf_61`: un APPROVE con desglose también deja constancia de QUÉ
        # se comprobó. Sin esto, «aprobado» es indistinguible de «aprobado sin
        # mirar», que es justo lo que el desglose viene a resolver.
        if verdict.criteria:
            await append_audit_event(
                session,
                tenant_id=tenant_id,
                task_id=task_id,
                kind="review_comment",
                actor=reviewer_actor,
                payload={
                    "approved": True,
                    "criteria": [c.as_dict() for c in verdict.criteria],
                },
            )
        await session.flush()
        return {
            "action": "approved",
            "verdict": "approve",
            "task_id": str(task_id),
            "task_status": TaskStatus.DONE.value,
        }

    # reject — retry until max, then escalate to `blocked` (stops the reject↔retry loop).
    task_row.retry_count += 1
    exhausted = task_row.retry_count >= task_row.max_retries
    target = TaskStatus.BLOCKED.value if exhausted else TaskStatus.BACKLOG.value
    transition_task_status(task_row, target)
    await session.flush()

    event = await append_audit_event(
        session,
        tenant_id=tenant_id,
        task_id=task_id,
        kind="review_comment",
        actor=reviewer_actor,
        payload={
            "failed_criterion": verdict.failed_criterion,
            "testreport_evidence": verdict.testreport_evidence,
            "what_to_fix": verdict.what_to_fix,
            "escalated": exhausted,
            "reason": "max_retries" if exhausted else None,
            # `task_wf_61`: el desglose por criterio. Va al MISMO evento que ya
            # consume la UI y `prior_review_feedback`, no a una tabla nueva:
            # quien lea el rechazo tiene ahí qué se comprobó y qué falló.
            # `[]` cuando el reviewer no lo emitió (comportamiento de antes).
            "criteria": [c.as_dict() for c in verdict.criteria],
            # `task_gov_10`: el par agregable. Va al MISMO evento que la prosa
            # —no a una tabla nueva ni a una columna— porque este payload JSONB
            # ES la fila del veredicto, y el value-set queda cerrado por el
            # ESCRITOR: aquí no entra nada que no haya pasado por
            # `shared_domain.reject_taxonomy.normalise_*`. Mismo patrón que las
            # 13 categorías de aprobación, que también viven en JSONB con un
            # test de contrato en vez de un CHECK.
            "reject_targets": list(verdict.reject_targets),
            "reject_classes": list(verdict.reject_classes),
        },
    )

    # P1-1 (investigación 2026-07-11): el juicio del reviewer se destila como
    # memoria semántica project_shared — antes el «qué salió mal en review» no
    # dejaba lección reutilizable (solo el audit event por-task). Determinista
    # (sin LLM) y best-effort: jamás rompe el veredicto ya aplicado.
    await _persist_rejection_memory(session, task=task_row, verdict=verdict)

    return {
        "action": "escalated" if exhausted else "rejected",
        "verdict": "reject",
        "task_id": str(task_id),
        "task_status": target,
        "retry_count": task_row.retry_count,
        "event_id": str(event.id),
    }


async def _persist_rejection_memory(session: Any, *, task: Any, verdict: ReviewerVerdict) -> None:
    """Memoria semántica del rechazo (P1-1) — determinista y best-effort."""
    try:
        from api_server.memorizer.distillation import MemoryCandidate
        from api_server.memorizer.persistence import persist_memory_candidates

        parts = [f"Review rechazó «{task.title or task.id}»"]
        if verdict.failed_criterion:
            parts.append(f"criterio fallado: {verdict.failed_criterion}")
        if verdict.what_to_fix:
            parts.append(f"arreglo requerido: {verdict.what_to_fix}")
        content = ". ".join(parts)[:2000]
        candidate = MemoryCandidate(content=content, type="semantic", tags=("review",))
        await persist_memory_candidates(
            session,
            [candidate],
            tenant_id=task.tenant_id,
            scope="project_shared",
            project_id=task.project_id,
        )
    except Exception as exc:  # la memoria nunca rompe el veredicto
        _log.warning("reviewer_bridge.rejection_memory_failed", error=str(exc))


__all__ = [
    "ReviewerVerdict",
    "VerdictLabel",
    "apply_reviewer_verdict",
    "parse_reviewer_output",
]
