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
      </rejection>

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
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Task, TaskStatus
from api_server.db.task_audit_repo import append_audit_event
from api_server.task_state_machine import transition_task_status

VerdictLabel = Literal["approve", "reject", "unknown"]


@dataclass(frozen=True)
class ReviewerVerdict:
    """Structured outcome of one reviewer turn.

    ``label`` is the parsed `<verdict>` tag. The three rejection fields
    are non-empty only when ``label == 'reject'``.
    """

    label: VerdictLabel
    failed_criterion: str = ""
    testreport_evidence: str = ""
    what_to_fix: str = ""


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
    """
    label: VerdictLabel = "unknown"
    for body in _VERDICT_RE.findall(text or ""):
        candidate = _normalise_verdict(body)
        if candidate != "unknown":
            label = candidate
    if label != "reject":
        return ReviewerVerdict(label=label)

    def _grab(pattern: re.Pattern[str]) -> str:
        m = pattern.search(text)
        return m.group(1).strip() if m else ""

    return ReviewerVerdict(
        label="reject",
        failed_criterion=_grab(_FAILED_RE),
        testreport_evidence=_grab(_EVIDENCE_RE),
        what_to_fix=_grab(_WHAT_TO_FIX_RE),
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
        },
    )

    return {
        "action": "escalated" if exhausted else "rejected",
        "verdict": "reject",
        "task_id": str(task_id),
        "task_status": target,
        "retry_count": task_row.retry_count,
        "event_id": str(event.id),
    }


__all__ = [
    "ReviewerVerdict",
    "VerdictLabel",
    "apply_reviewer_verdict",
    "parse_reviewer_output",
]
