"""Pydantic schemas for the tenant guardrail-events endpoints (Plan 11 task_11_20).

Two read shapes back the tenant guardrails dashboard:

  - :class:`GuardrailEventResponse` — one ``guardrail_events`` row as
    returned by the paginated list endpoint. It echoes the masked detail
    only (``detail`` + ``detail_payload``); the raw secret / PII that
    tripped the guardrail is NEVER present (the recorder masked it before
    persisting).

  - :class:`GuardrailDashboardResponse` — the aggregated dashboard:
    counts grouped by guardrail type, by severity, and a per-day time
    series, plus the most-recent events. Drives the tenant dashboard's
    charts / tables without the client having to aggregate client-side.

The list endpoint accepts ``limit`` / ``offset`` (``ge`` / ``le`` via the
shared pagination helper) and optional ``type`` / ``severity`` / time
filters; all are tenant-scoped (RLS) so a tenant only ever sees its own
events.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from api_server.db.guardrail_event import GuardrailEvent

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


# =============================================================================
# One event (list / recent)
# =============================================================================
class GuardrailEventResponse(BaseModel):
    """A guardrail-event row as exposed to the tenant dashboard.

    ``detail`` / ``detail_payload`` carry the MASKED summary only — never
    the raw value that triggered the guardrail.
    """

    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    guardrail_type: str
    hook_point: str
    severity: str
    action: str | None
    project_id: UUID | None
    agent_id: UUID | None
    execution_id: UUID | None
    agent_label: str | None
    detail: str
    detail_payload: dict[str, Any]
    created_at: datetime


def to_event_response(event: GuardrailEvent) -> GuardrailEventResponse:
    """Map an ORM ``GuardrailEvent`` to its response model."""
    return GuardrailEventResponse.model_validate(event, from_attributes=True)


# =============================================================================
# Dashboard aggregates
# =============================================================================
class GuardrailTypeCount(BaseModel):
    """Number of events of one guardrail type in the dashboard window."""

    model_config = _BASE_CONFIG

    guardrail_type: str
    count: int


class GuardrailSeverityCount(BaseModel):
    """Number of events of one severity in the dashboard window."""

    model_config = _BASE_CONFIG

    severity: str
    count: int


class GuardrailDayCount(BaseModel):
    """Number of events on one UTC day (the time-series bucket)."""

    model_config = _BASE_CONFIG

    day: str
    count: int


class GuardrailDashboardResponse(BaseModel):
    """Aggregated guardrail activity for a tenant over a time window.

    ``total`` is the event count in the window; ``by_type`` / ``by_severity``
    are the grouped counts (for the dashboard's bar/pie charts); ``by_day``
    is the daily time series (for the trend chart); ``recent`` is the latest
    events (masked) for the recent-activity table.
    """

    model_config = _BASE_CONFIG

    total: int
    window_days: int
    by_type: list[GuardrailTypeCount]
    by_severity: list[GuardrailSeverityCount]
    by_day: list[GuardrailDayCount]
    recent: list[GuardrailEventResponse]


__all__ = [
    "GuardrailDashboardResponse",
    "GuardrailDayCount",
    "GuardrailEventResponse",
    "GuardrailSeverityCount",
    "GuardrailTypeCount",
    "to_event_response",
]
