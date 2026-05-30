"""Tenant-configurable OUTLIER ALERT RULE ORM (Plan 14 Fase D, task_14_13).

A single tenant-scoped table — ``outlier_alert_rules`` — that lets a Tenant
Admin say "alert me when an agent's quality / cost / latency goes off the
rails". When the platform evaluates a tenant's agent statistics over the rule's
trailing window and an agent breaches the rule, ONE alert fires through the
Plan 10 notification system (an event → notification to the tenant's Tenant
Admins), debounced like the guardrail-alert / drift-alert pattern.

The plan calls for TWO complementary outlier notions, both expressed as
configurable rules — never magic numbers:

  * a **success-rate FLOOR** ("if agent X success rate < 70%, alert"): an
    agent whose success rate over the window drops below ``success_rate_floor``
    is an outlier. Absolute, per-agent, independent of the tenant norm.
  * a **statistical deviation** from the tenant norm: an agent whose metric
    (cost / latency) is more than ``stddev_k`` standard deviations ABOVE the
    tenant mean over the window is an outlier — the "agente que destaca o
    flaquea" relative to its peers (Plan 14 Alcance).

``metric`` selects which signal the rule watches (``success_rate`` /
``cost`` / ``latency``); ``direction`` is implied by the metric (a success-rate
floor is a LOWER bound, a cost/latency deviation is an UPPER bound), so a single
rule is unambiguous. A rule sets EITHER ``success_rate_floor`` (for the
``success_rate`` metric) OR ``stddev_k`` (for ``cost`` / ``latency``); the API
validates the pairing. ``min_runs`` guards against alerting on a tiny,
statistically meaningless sample.

Tenancy decision (CLAUDE.md principle 1 — multi-tenancy from day one):
**tenant-owned** (``tenant_id NOT NULL`` via :class:`TenantScopedMixin` + RLS,
added by migration 0061). An alert rule is a tenant's own config — a tenant
manages and is alerted on ONLY its own rules / its own agents' statistics, so it
is a plain tenant-isolated table like ``guardrail_alert_rules``, with the
canonical FOR ALL tenant-isolation RLS policy. Tenant A's agents can NEVER alert
tenant B (the evaluator aggregates only the rule's own ``tenant_id`` executions
under RLS, and dispatch is scoped to that tenant). The outlier dashboards /
stats are likewise tenant-scoped — cross-tenant comparison is the separate,
System-Admin-only task_14_15.

**Debounce** (a breach must not spam): each rule records ``last_fired_at``.
After an alert fires, the rule is suppressed from firing again until a full
``window_days`` (expressed in seconds) has elapsed since ``last_fired_at`` — so
a sustained outlier produces at most ONE alert per rule per window, mirroring
the guardrail-alert / drift-alert debounce.

Defaults for new rules come from named constants (:data:`DEFAULT_WINDOW_DAYS` /
:data:`DEFAULT_STDDEV_K` / :data:`DEFAULT_SUCCESS_RATE_FLOOR` /
:data:`DEFAULT_MIN_RUNS`), never inline literals.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class OutlierMetric(enum.StrEnum):
    """Which per-agent statistic an outlier rule watches.

    - ``success_rate``: the fraction of the agent's executions that ended
      ``done`` over the window. Watched against an absolute FLOOR (a LOWER
      bound) — an agent below the floor is flaqueando.
    - ``cost``: the agent's mean cost per run (USD) over the window. Watched
      as a statistical deviation ABOVE the tenant mean (an UPPER bound).
    - ``latency``: the agent's mean run duration (ms) over the window. Watched
      as a statistical deviation ABOVE the tenant mean (an UPPER bound).
    """

    SUCCESS_RATE = "success_rate"
    COST = "cost"
    LATENCY = "latency"


# Default rule shape for a freshly-created outlier rule (no magic literals in
# the service / endpoint layer — they reference these named constants).
DEFAULT_WINDOW_DAYS = 30
# k standard deviations ABOVE the tenant mean that flags a cost/latency
# outlier. 2.0 ≈ the top ~2.3% of a normal distribution.
DEFAULT_STDDEV_K: Decimal = Decimal("2.0")
# Default success-rate floor (a fraction in [0, 1]); the plan's example is 70%.
DEFAULT_SUCCESS_RATE_FLOOR: Decimal = Decimal("0.7")
# Minimum runs an agent must have in the window before it can be flagged — a
# guard against alerting on a statistically meaningless sample.
DEFAULT_MIN_RUNS = 5

# Bounds the API validates a rule against (platform invariants of the contract,
# not per-tenant tunables — an out-of-range value is a clean 422, never a
# silent clamp).
MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 730
MIN_STDDEV_K: Decimal = Decimal("0")
MAX_STDDEV_K: Decimal = Decimal("10")
MIN_MIN_RUNS = 1


class OutlierAlertRule(
    Base,
    UUIDPrimaryKeyMixin,
    TenantScopedMixin,
    TimestampMixin,
    SoftDeleteMixin,
):
    """One tenant-configurable agent-outlier alert rule (task_14_13).

    Tenant-owned (``tenant_id`` NOT NULL + RLS). A Tenant Admin CRUDs the rules
    for their tenant; the evaluator aggregates the tenant's ``executions`` over
    the trailing ``window_days`` per agent and fires an alert (via the Plan 10
    notifier) for each agent that breaches the rule, debounced by
    ``last_fired_at``.

    A rule watches ONE :class:`OutlierMetric`. For ``success_rate`` it sets
    ``success_rate_floor`` (an agent below it is an outlier); for ``cost`` /
    ``latency`` it sets ``stddev_k`` (an agent more than that many standard
    deviations ABOVE the tenant mean is an outlier). The unused threshold is
    NULL. The pairing is enforced by a CHECK constraint + the API.
    """

    __tablename__ = "outlier_alert_rules"
    __table_args__ = (
        # The evaluator's primary lookup: a tenant's enabled, live rules.
        Index(
            "ix_outlier_alert_rules_tenant_enabled",
            "tenant_id",
            "enabled",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # window / min_runs must be sane (a NOT NULL + positive contract at the
        # DB on top of the API's 422 validation).
        CheckConstraint(
            "window_days >= 1",
            name="ck_outlier_alert_rules_window_positive",
        ),
        CheckConstraint(
            "min_runs >= 1",
            name="ck_outlier_alert_rules_min_runs_positive",
        ),
        # A success-rate floor is a fraction in [0, 1] when set.
        CheckConstraint(
            "success_rate_floor IS NULL "
            "OR (success_rate_floor >= 0 AND success_rate_floor <= 1)",
            name="ck_outlier_alert_rules_floor_unit_range",
        ),
        # The standard-deviation multiplier is non-negative when set.
        CheckConstraint(
            "stddev_k IS NULL OR stddev_k >= 0",
            name="ck_outlier_alert_rules_stddev_k_non_negative",
        ),
        # The metric ↔ threshold pairing: a success_rate rule carries a floor
        # (and no stddev_k); a cost/latency rule carries a stddev_k (and no
        # floor). Enforced at the DB on top of the API so a malformed row can
        # never reach the evaluator.
        CheckConstraint(
            "(metric = 'success_rate' AND success_rate_floor IS NOT NULL "
            "AND stddev_k IS NULL) "
            "OR (metric IN ('cost', 'latency') AND stddev_k IS NOT NULL "
            "AND success_rate_floor IS NULL)",
            name="ck_outlier_alert_rules_metric_threshold_pairing",
        ),
    )

    # --- human-facing identity ----------------------------------------------
    # A short label the Tenant Admin gives the rule ("Backend success floor",
    # "Cost spikes"). Free-form, bounded.
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    # --- the configurable trigger -------------------------------------------
    # Which per-agent statistic this rule watches (values of OutlierMetric).
    metric: Mapped[str] = mapped_column(String(16), nullable=False)
    # The trailing window (in days) the per-agent statistics are aggregated
    # over.
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    # Minimum runs an agent must have in the window before it can be flagged.
    min_runs: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))

    # --- thresholds (exactly one set, per the metric) -----------------------
    # The success-rate floor (a fraction in [0, 1]); set iff metric is
    # ``success_rate``. An agent below it is an outlier.
    success_rate_floor: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=4, scale=3), nullable=True
    )
    # The standard-deviation multiplier; set iff metric is ``cost`` /
    # ``latency``. An agent more than this many stddevs ABOVE the tenant mean
    # is an outlier.
    stddev_k: Mapped[Decimal | None] = mapped_column(Numeric(precision=5, scale=2), nullable=True)

    # --- lifecycle -----------------------------------------------------------
    # A disabled rule is never evaluated (kept for re-enabling without losing
    # its config / history).
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Debounce anchor: when the rule last fired an alert. The evaluator
    # suppresses a re-fire until a full window has elapsed since this, so a
    # sustained outlier yields at most ONE alert per rule per window. NULL =
    # never fired.
    last_fired_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"OutlierAlertRule(id={self.id!r}, tenant={self.tenant_id!r}, "
            f"name={self.name!r}, metric={self.metric!r}, "
            f"window_days={self.window_days!r}, "
            f"success_rate_floor={self.success_rate_floor!r}, "
            f"stddev_k={self.stddev_k!r}, enabled={self.enabled!r})"
        )


__all__ = [
    "DEFAULT_MIN_RUNS",
    "DEFAULT_STDDEV_K",
    "DEFAULT_SUCCESS_RATE_FLOOR",
    "DEFAULT_WINDOW_DAYS",
    "MAX_STDDEV_K",
    "MAX_WINDOW_DAYS",
    "MIN_MIN_RUNS",
    "MIN_STDDEV_K",
    "MIN_WINDOW_DAYS",
    "OutlierAlertRule",
    "OutlierMetric",
]
