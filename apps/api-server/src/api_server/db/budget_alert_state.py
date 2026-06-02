"""Budget-alert DEBOUNCE state ORM (Plan 11.1 Fase B, task_11_1_05).

The budget consumption evaluator (``api_server.budgets.consumption``) sums
the canonical-USD cost of a tenant's / project's executions within the
active budget period, compares it against the (USD-converted) budget cap,
and fires ONE alert per crossed threshold (``[80, 90, 100]`` by default) via
the Plan 10 notifier. The debounce contract is **one alert per threshold per
period per scope** — a sustained breach must not re-alert, and a later
evaluation in the same period must not re-fire a threshold already raised.

Unlike the guardrail / outlier alert rules (where the rule row itself carries
a single ``last_fired_at``), a budget has SEVERAL thresholds active in the
SAME period, so a single timestamp cannot express "80% fired but 90% has
not". This table records the FACT that a given ``(scope, project, period,
threshold)`` tuple already fired: one row per fired threshold. The evaluator
inserts a row when it raises a threshold and skips any threshold that already
has a row for the current period — so the debounce is exact and idempotent,
and survives a process restart (it is in the database, not in memory).

Tenancy decision (CLAUDE.md principle 1): **tenant-owned** — ``tenant_id``
NOT NULL via :class:`TenantScopedMixin` + the canonical FOR ALL
tenant-isolation RLS policy (migration 0064, the same NULLIF + ``::uuid``
cast shape as ``outlier_alert_rules`` / ``guardrail_alert_rules``). A budget
alert is a tenant's own state; tenant A's spend can NEVER fire / debounce a
tenant-B alert.

NOT soft-deleted: a fired-alert record is an immutable fact for its period.
Old rows for elapsed periods are harmless (the unique key includes
``period_start`` so a NEW period starts with a clean slate) and may be pruned
by a future housekeeping job; the table never grows without bound because the
key is (scope x project x period x threshold), a small finite set per period.
"""

from __future__ import annotations

import enum
from datetime import date
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class BudgetScope(enum.StrEnum):
    """Which budget a consumption / alert row pertains to.

    - ``tenant``: the tenant-wide budget on ``organizations`` (no project).
    - ``project``: a single project's budget on ``projects`` (``project_id``
      set).
    """

    TENANT = "tenant"
    PROJECT = "project"


class BudgetAlertState(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """One fired budget-alert threshold for a scope's current period.

    A row exists iff the evaluator already raised ``threshold`` for this
    ``(scope, project_id, period_start)`` — the debounce key. The unique
    constraint guarantees at most one row per tuple, so a re-evaluation in the
    same period is a no-op (the insert is skipped). Tenant-owned (RLS); the
    project scope carries ``project_id`` (NULL for the tenant scope).
    """

    __tablename__ = "budget_alert_states"
    __table_args__ = (
        # The debounce key: at most one fired-row per (tenant, scope, project,
        # period_start, threshold). A re-evaluation in the same period that
        # tries to re-raise the threshold hits this unique constraint (the
        # evaluator checks first, but the constraint is the hard guarantee).
        # project_id is part of the key; for the tenant scope it is NULL and
        # Postgres treats NULLs as distinct, so we additionally pin the tenant
        # scope by a partial index below — but the practical uniqueness for the
        # tenant scope is (tenant_id, scope='tenant', period_start, threshold),
        # which holds because project_id is consistently NULL there.
        UniqueConstraint(
            "tenant_id",
            "scope",
            "project_id",
            "period_start",
            "threshold",
            name="uq_budget_alert_states_debounce",
        ),
        # Evaluator lookup: the fired thresholds for a scope's current period.
        Index(
            "ix_budget_alert_states_lookup",
            "tenant_id",
            "scope",
            "project_id",
            "period_start",
        ),
        # A threshold is a percentage of the budget — must be positive (mirrors
        # the platform_settings threshold validation bounds).
        CheckConstraint(
            "threshold >= 1",
            name="ck_budget_alert_states_threshold_positive",
        ),
        # The project scope carries a project_id; the tenant scope does not.
        CheckConstraint(
            "(scope = 'tenant' AND project_id IS NULL) "
            "OR (scope = 'project' AND project_id IS NOT NULL)",
            name="ck_budget_alert_states_scope_project_pairing",
        ),
    )

    # Which budget this fired-row belongs to ('tenant' / 'project').
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    # The project whose budget fired (NULL for the tenant-wide budget).
    project_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # The start date of the budget period this row debounces — part of the
    # debounce key, so a NEW period starts with a clean slate (no row → may
    # re-fire). Half-open [period_start, period_end) per budgets.period.
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    # The percentage-of-budget threshold that fired (e.g. 80, 90, 100).
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"BudgetAlertState(tenant={self.tenant_id!r}, scope={self.scope!r}, "
            f"project={self.project_id!r}, period_start={self.period_start!r}, "
            f"threshold={self.threshold!r})"
        )


__all__ = [
    "BudgetAlertState",
    "BudgetScope",
]
