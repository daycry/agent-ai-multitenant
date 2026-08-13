"""Tenant-configurable guardrail ALERT RULE ORM (Plan 11 Fase E, task_11_21).

A single tenant-scoped table — ``guardrail_alert_rules`` — that lets a
Tenant Admin say "alert me when guardrail violations spike". When the count
of matching ``guardrail_events`` (task_11_20) in a trailing window crosses
a configured threshold, the platform fires ONE alert through the Plan 10
notification system (an event → notification to the tenant's Tenant Admins).

Tenancy decision (CLAUDE.md principle 1 — multi-tenancy from day one):
**tenant-owned** (``tenant_id NOT NULL`` via :class:`TenantScopedMixin` +
RLS, added by migration 0053). An alert rule is a tenant's own config — a
tenant manages and is alerted on ONLY its own rules / its own violations,
so it is a plain tenant-isolated table like ``notification_preferences``,
with the canonical FOR ALL tenant-isolation RLS policy. Tenant A's
violations can NEVER alert tenant B (the evaluator counts only the rule's
own ``tenant_id`` events under RLS, and dispatch is scoped to that tenant).

The rule is **configurable** — there are no hardcoded magic numbers:

  - ``threshold`` — how many matching violations trip the alert.
  - ``window_seconds`` — the trailing window the count is measured over.
  - ``guardrail_type`` (optional) — scope to ONE guardrail type
    (``pii`` / ``secret_leakage`` / …); NULL = any type.
  - ``min_severity`` (optional) — only count events at/above this severity
    on the engine's ordered scale (info < low < medium < high < critical);
    NULL = any severity.

Defaults for new rules come from named constants
(:data:`DEFAULT_THRESHOLD` / :data:`DEFAULT_WINDOW_SECONDS`), never inline
literals.

**Debounce** (a breach must not spam): each rule records ``last_fired_at``.
After an alert fires, the rule is suppressed from firing again until a full
``window_seconds`` has elapsed since ``last_fired_at`` — so a sustained
breach produces at most ONE alert per rule per window.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
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

# Default rule shape for a freshly-created alert rule (no magic literals in
# the service / endpoint layer — they reference these named constants).
DEFAULT_THRESHOLD = 10
DEFAULT_WINDOW_SECONDS = 3_600  # one hour ("X violations / hour")

# Bounds the API validates a rule against (a platform invariant of the
# contract, not a per-tenant tunable — so an out-of-range value is a clean
# 422, never a silent clamp). A window of at least one minute and at most 30
# days; a threshold of at least one.
MIN_THRESHOLD = 1
MIN_WINDOW_SECONDS = 60
MAX_WINDOW_SECONDS = 30 * 24 * 3_600  # 30 days


# The engine's severity scale, ordered low→high. Used to resolve a rule's
# ``min_severity`` to the set of severities at/above it (the count filter).
# Mirrors ``shared_guardrails.types.Severity`` / ``GuardrailEventSeverity``.
SEVERITY_ORDER: tuple[str, ...] = ("info", "low", "medium", "high", "critical")


def severities_at_or_above(min_severity: str) -> list[str]:
    """Return the severities at/above ``min_severity`` on the ordered scale.

    Used by the evaluator to build the ``severity IN (...)`` count filter.
    An unknown severity (defensive) yields just itself, so a future
    severity the catalogue grows still counts something rather than
    silently matching nothing.
    """
    if min_severity not in SEVERITY_ORDER:
        return [min_severity]
    start = SEVERITY_ORDER.index(min_severity)
    return list(SEVERITY_ORDER[start:])


class GuardrailAlertRule(
    Base,
    UUIDPrimaryKeyMixin,
    TenantScopedMixin,
    TimestampMixin,
    SoftDeleteMixin,
):
    """One tenant-configurable guardrail alert rule (task_11_21).

    Tenant-owned (``tenant_id`` NOT NULL + RLS). A Tenant Admin CRUDs the
    rules for their tenant; the evaluator counts matching
    ``guardrail_events`` in the trailing ``window_seconds`` and fires an
    alert (via the Plan 10 notifier) when the count crosses ``threshold``,
    debounced by ``last_fired_at``.
    """

    __tablename__ = "guardrail_alert_rules"
    __table_args__ = (
        # The evaluator's primary lookup: a tenant's enabled, live rules.
        Index(
            "ix_guardrail_alert_rules_tenant_enabled",
            "tenant_id",
            "enabled",
        ),
        # Threshold / window must be sane (a NOT NULL + positive contract at
        # the DB on top of the API's 422 validation).
        CheckConstraint("threshold >= 1", name="ck_guardrail_alert_rules_threshold_positive"),
        CheckConstraint(
            "window_seconds >= 1",
            name="ck_guardrail_alert_rules_window_positive",
        ),
    )

    # --- human-facing identity ----------------------------------------------
    # A short label the Tenant Admin gives the rule ("PII spike", "secret
    # leaks / hour"). Free-form, bounded.
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    # --- the configurable trigger -------------------------------------------
    # How many matching violations within the window trip the alert.
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    # The trailing window (in seconds) the count is measured over.
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- optional scoping ----------------------------------------------------
    # Restrict the count to ONE guardrail type (TEXT so the type catalogue
    # evolves migration-free). NULL = count any type.
    guardrail_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Restrict the count to events at/above this severity (info / low /
    # medium / high / critical). NULL = count any severity.
    min_severity: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # --- lifecycle -----------------------------------------------------------
    # A disabled rule is never evaluated (kept for re-enabling without
    # losing its config / history).
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Debounce anchor: when the rule last fired an alert. The evaluator
    # suppresses a re-fire until a full window has elapsed since this, so a
    # sustained breach yields at most ONE alert per rule per window. NULL =
    # never fired.
    last_fired_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"GuardrailAlertRule(id={self.id!r}, tenant={self.tenant_id!r}, "
            f"name={self.name!r}, threshold={self.threshold!r}, "
            f"window_seconds={self.window_seconds!r}, "
            f"guardrail_type={self.guardrail_type!r}, "
            f"min_severity={self.min_severity!r}, enabled={self.enabled!r})"
        )


__all__ = [
    "DEFAULT_THRESHOLD",
    "DEFAULT_WINDOW_SECONDS",
    "MAX_WINDOW_SECONDS",
    "MIN_THRESHOLD",
    "MIN_WINDOW_SECONDS",
    "SEVERITY_ORDER",
    "GuardrailAlertRule",
    "severities_at_or_above",
]
