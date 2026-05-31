"""Pydantic schemas for the ``/human-agents`` gallery (Plan 16 task_16_07).

A Human Agent is an :class:`~api_server.db.domain.Agent` with
``agent_type='human'`` PLUS its 1:1
:class:`~api_server.db.domain.HumanAgentConfig` row (who the human is, their
rate, how to reach them, the acceptance timeout / escalation target, the
planning estimates). The gallery treats the two as ONE cohesive resource:
create/update accept the agent fields and the config fields together, and the
response folds them into a single object.

The MVP constrains ``assignment_mode`` to ``specific_user`` (Plan 16 Decisiones
Clave) — mirrored both by the DB CHECK ``ck_human_agent_config_assignment_mode``
and by the default here; the field is not even surfaced as editable.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api_server.db.domain import Agent, AssignmentMode, HumanAgentConfig

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# The config sub-payload (shared by create/update bodies).
# ---------------------------------------------------------------------------
class HumanAgentConfigFields(BaseModel):
    """The ``human_agent_config`` fields a tenant admin can set.

    All optional on update; on create the router supplies the defaults the DB
    would otherwise apply, so the row is always consistent. ``assignment_mode``
    is fixed to ``specific_user`` in the MVP and is therefore NOT exposed.
    """

    model_config = _BASE_CONFIG

    assigned_user_id: UUID | None = None
    # ISO-4217 currency code (3 letters). Mirrors organizations.hourly_rate.
    hourly_rate: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    hourly_rate_currency: str | None = Field(default=None, min_length=3, max_length=3)
    # Channel identifiers, e.g. ["email", "in_app"]. Free-form list (JSONB).
    notification_channels: list[str] = Field(default_factory=list)
    acceptance_timeout_hours: int = Field(default=24, ge=1, le=720)
    escalation_target_user_id: UUID | None = None
    expected_response_time_hours: int | None = Field(default=None, ge=0)
    expected_execution_time_hours: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# Create — agent identity + config in one body.
# ---------------------------------------------------------------------------
class HumanAgentCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    avatar_url: str | None = Field(default=None, max_length=500)
    # The Human Agent's role label — the same AgentRole free-text the AI agents
    # use (reviewer, security, …). Defaults to 'reviewer' (the most common
    # human-task shape: a human reviewing AI output).
    role: str = Field(default="reviewer", min_length=1, max_length=32)
    # System prompt is meaningless for a human but the agents table requires
    # one; default to a short human-readable note so the column is non-empty.
    system_prompt: str = Field(default="Human agent.", min_length=1)
    config: HumanAgentConfigFields = Field(default_factory=HumanAgentConfigFields)


# ---------------------------------------------------------------------------
# Update — every field optional; only sent values are touched.
# ---------------------------------------------------------------------------
class HumanAgentUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    avatar_url: str | None = Field(default=None, max_length=500)
    role: str | None = Field(default=None, min_length=1, max_length=32)
    system_prompt: str | None = Field(default=None, min_length=1)
    # Partial config: a missing key is left alone, an explicit value overwrites.
    config: HumanAgentConfigUpdate | None = None


class HumanAgentConfigUpdate(BaseModel):
    """All-optional variant for PATCH-style updates of the config row."""

    model_config = _BASE_CONFIG

    assigned_user_id: UUID | None = None
    hourly_rate: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    hourly_rate_currency: str | None = Field(default=None, min_length=3, max_length=3)
    notification_channels: list[str] | None = None
    acceptance_timeout_hours: int | None = Field(default=None, ge=1, le=720)
    escalation_target_user_id: UUID | None = None
    expected_response_time_hours: int | None = Field(default=None, ge=0)
    expected_execution_time_hours: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# Clone-and-fork a global template into the tenant.
# ---------------------------------------------------------------------------
class HumanAgentForkRequest(BaseModel):
    """Copy a global Human-Agent template into the caller's tenant.

    Forking is mandatory (Plan 16 Decisiones Clave): the assignment to a
    concrete User is intrinsically tenant-scoped, so a global template can
    never be linked cross-tenant. The optional ``name`` lets the caller rename
    the fork; ``assigned_user_id`` optionally pre-assigns the User in one step.
    """

    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=120)
    assigned_user_id: UUID | None = None


# ---------------------------------------------------------------------------
# Responses.
# ---------------------------------------------------------------------------
class HumanAgentConfigResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    agent_id: UUID
    assignment_mode: str
    assigned_user_id: UUID | None
    hourly_rate: Decimal | None
    hourly_rate_currency: str | None
    notification_channels: list[Any]
    acceptance_timeout_hours: int
    escalation_target_user_id: UUID | None
    expected_response_time_hours: int | None
    expected_execution_time_hours: int | None


class HumanAgentResponse(BaseModel):
    """A Human Agent = its Agent row folded with its 1:1 config row.

    ``config`` is ``None`` ONLY for a template that has not yet been forked
    (global templates carry no tenant config — the assignment is tenant-
    intrinsic). For a tenant-owned Human Agent it is always present.
    """

    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    avatar_url: str | None
    agent_type: str
    role: str
    scope: str
    is_template: bool
    forked_from_agent_id: UUID | None
    config: HumanAgentConfigResponse | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class AssignableUserResponse(BaseModel):
    """A member of the caller's tenant, usable as ``assigned_user_id``."""

    model_config = _BASE_CONFIG

    user_id: UUID
    email: str
    full_name: str | None
    role: str


def to_config_response(c: HumanAgentConfig) -> HumanAgentConfigResponse:
    return HumanAgentConfigResponse(
        id=c.id,
        agent_id=c.agent_id,
        assignment_mode=c.assignment_mode,
        assigned_user_id=c.assigned_user_id,
        hourly_rate=c.hourly_rate,
        hourly_rate_currency=c.hourly_rate_currency,
        notification_channels=list(c.notification_channels or []),
        acceptance_timeout_hours=c.acceptance_timeout_hours,
        escalation_target_user_id=c.escalation_target_user_id,
        expected_response_time_hours=c.expected_response_time_hours,
        expected_execution_time_hours=c.expected_execution_time_hours,
    )


def to_human_agent_response(a: Agent, config: HumanAgentConfig | None) -> HumanAgentResponse:
    return HumanAgentResponse(
        id=a.id,
        tenant_id=a.tenant_id,
        name=a.name,
        description=a.description,
        avatar_url=a.avatar_url,
        agent_type=a.agent_type,
        role=a.role,
        scope=a.scope,
        is_template=a.is_template,
        forked_from_agent_id=a.forked_from_agent_id,
        config=to_config_response(config) if config is not None else None,
        created_at=a.created_at,
        updated_at=a.updated_at,
        deleted_at=a.deleted_at,
    )


# Default assignment_mode value (MVP-fixed). Re-exported so the router and the
# seed both reference the same constant.
DEFAULT_ASSIGNMENT_MODE: str = AssignmentMode.SPECIFIC_USER.value


HumanAgentUpdateRequest.model_rebuild()
