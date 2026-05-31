"""Pydantic schemas for the tenant guardrail alert-rule CRUD (Plan 11 task_11_21).

The wire shapes a Tenant Admin uses to manage
:class:`~api_server.db.guardrail_alert_rule.GuardrailAlertRule` rows: create,
update (partial), and the response. All tenant-scoped (RLS) — a Tenant
Admin manages ONLY their own tenant's rules.

Validation pins the configurable trigger to sane bounds (named constants in
the ORM module, not magic numbers): ``threshold >= MIN_THRESHOLD`` and
``MIN_WINDOW_SECONDS <= window_seconds <= MAX_WINDOW_SECONDS`` — an
out-of-range value is a clean 422. The optional ``min_severity`` is an enum
of the engine's severity scale so an unknown value is a clean 422 too.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api_server.db.guardrail_alert_rule import (
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW_SECONDS,
    MAX_WINDOW_SECONDS,
    MIN_THRESHOLD,
    MIN_WINDOW_SECONDS,
    GuardrailAlertRule,
)
from api_server.db.guardrail_event import GuardrailEventSeverity

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class GuardrailAlertRuleCreateRequest(BaseModel):
    """Create one alert rule for the caller's tenant.

    ``threshold`` / ``window_seconds`` default to the named ORM constants so
    a minimal request ("X violations / hour") needs only a ``name``. The
    optional ``guardrail_type`` / ``min_severity`` scope which events count.
    """

    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=160)
    threshold: int = Field(default=DEFAULT_THRESHOLD, ge=MIN_THRESHOLD)
    window_seconds: int = Field(
        default=DEFAULT_WINDOW_SECONDS,
        ge=MIN_WINDOW_SECONDS,
        le=MAX_WINDOW_SECONDS,
    )
    guardrail_type: str | None = Field(default=None, max_length=64)
    min_severity: GuardrailEventSeverity | None = None
    enabled: bool = True


class GuardrailAlertRuleUpdateRequest(BaseModel):
    """Partially update an alert rule. Every field optional; an empty patch
    is a 422 (the endpoint enforces "at least one field")."""

    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=160)
    threshold: int | None = Field(default=None, ge=MIN_THRESHOLD)
    window_seconds: int | None = Field(
        default=None,
        ge=MIN_WINDOW_SECONDS,
        le=MAX_WINDOW_SECONDS,
    )
    # ``guardrail_type`` / ``min_severity`` can be cleared back to "any" — we
    # cannot distinguish "absent" from "set to null" with a plain Optional,
    # so the endpoint uses ``model_fields_set`` to apply only provided keys.
    guardrail_type: str | None = Field(default=None, max_length=64)
    min_severity: GuardrailEventSeverity | None = None
    enabled: bool | None = None


class GuardrailAlertRuleResponse(BaseModel):
    """One alert rule as returned by the CRUD endpoints."""

    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    name: str
    threshold: int
    window_seconds: int
    guardrail_type: str | None
    min_severity: str | None
    enabled: bool
    last_fired_at: datetime | None
    created_at: datetime
    updated_at: datetime


def to_rule_response(rule: GuardrailAlertRule) -> GuardrailAlertRuleResponse:
    """Map an ORM ``GuardrailAlertRule`` to its response model."""
    return GuardrailAlertRuleResponse.model_validate(rule, from_attributes=True)


__all__ = [
    "GuardrailAlertRuleCreateRequest",
    "GuardrailAlertRuleResponse",
    "GuardrailAlertRuleUpdateRequest",
    "to_rule_response",
]
