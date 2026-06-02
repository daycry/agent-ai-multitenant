"""Human-agent orchestration domain services (Plan 16 Fase B).

The state machine and the orchestrator route a ``ready`` human task to a
concrete User (task_16_04 / task_16_05). This package holds the domain logic
that runs AFTER that initial assignment — currently the acceptance-timeout
escalation sweep (task_16_06): a best-effort, idempotent pass that finds
``pending_acceptance`` :class:`~api_server.db.domain.HumanTaskAssignment` rows
whose age exceeds the Human Agent's ``acceptance_timeout_hours`` and either
reassigns them to the ``escalation_target_user_id`` or, when escalation is
already exhausted, blocks the task and alerts the Tenant Admin.

The sweep is the DOMAIN core (no Celery, no broker): a Celery beat task in
:mod:`workers.human_escalation` calls it on a configurable cadence and supplies
the notifier seam. Tenant isolation is enforced row-by-row with an explicit
``tenant_id`` predicate (the worker runs BYPASSRLS, so RLS cannot catch it).
"""

from __future__ import annotations

from api_server.human_agents.escalation import (
    HUMAN_TASK_ASSIGNED_EVENT,
    TASK_BLOCKED_EVENT,
    EscalationNotice,
    EscalationOutcome,
    EscalationSweepResult,
    HumanEscalationNotifier,
    sweep_acceptance_timeouts,
)

__all__ = [
    "HUMAN_TASK_ASSIGNED_EVENT",
    "TASK_BLOCKED_EVENT",
    "EscalationNotice",
    "EscalationOutcome",
    "EscalationSweepResult",
    "HumanEscalationNotifier",
    "sweep_acceptance_timeouts",
]
