"""Budgets — tenant/project spend caps over a recurring period (Plan 11.1).

USD is the platform's canonical cost currency; a budget cap is denominated
in its own currency and converted to USD when evaluated. This package holds
the period arithmetic (the active budget window for a date) and, in later
tasks, the consumption + threshold evaluation and the auto-pause.

Binding decisions (Plan 11.1 Fase B):
  - Alert thresholds are platform-global + configurable (default
    ``[80, 90, 100]``) — see :mod:`api_server.db.platform_settings`
    (``get_budget_alert_thresholds``).
  - Auto-pause blocks NEW execution starts at 100% WITHOUT killing active
    runs (task_11_1_06).
"""

from __future__ import annotations

from api_server.budgets.consumption import (
    BUDGET_ALERT_EVENT_TYPE,
    BudgetAlertDispatcher,
    BudgetConsumption,
    BudgetEvaluationResult,
    BudgetFiring,
    CeleryBudgetAlertDispatcher,
    compute_budget_consumption,
    evaluate_budget_alerts,
    maybe_alert_budgets,
    tenant_budget_summary,
)
from api_server.budgets.human_cost import HumanCostScope, compute_human_cost_usd
from api_server.budgets.pause import (
    BUDGET_PAUSE_OVERRIDE_ACTION,
    BudgetPauseBlock,
    BudgetPauseRefresh,
    budget_pause_block,
    clear_budget_pause,
    refresh_budget_pause_flags,
)
from api_server.budgets.period import (
    BudgetPeriodWindow,
    InvalidBudgetPeriodError,
    current_budget_period,
)

__all__ = [
    "BUDGET_ALERT_EVENT_TYPE",
    "BUDGET_PAUSE_OVERRIDE_ACTION",
    "BudgetAlertDispatcher",
    "BudgetConsumption",
    "BudgetEvaluationResult",
    "BudgetFiring",
    "BudgetPauseBlock",
    "BudgetPauseRefresh",
    "BudgetPeriodWindow",
    "CeleryBudgetAlertDispatcher",
    "HumanCostScope",
    "InvalidBudgetPeriodError",
    "budget_pause_block",
    "clear_budget_pause",
    "compute_budget_consumption",
    "compute_human_cost_usd",
    "current_budget_period",
    "evaluate_budget_alerts",
    "maybe_alert_budgets",
    "refresh_budget_pause_flags",
    "tenant_budget_summary",
]
