"""Pydantic schemas for /projects endpoints (task_01_07).

Projects are the heaviest tenant-scoped entity: team assignment,
mcp / rag placeholders (typed loosely as JSONB until their own tables
land in Plans 04-05), repository config, human-approval policy
(structure firmed up by task_01_14), and budget controls (spec §28.7).

`paused_by_budget` is server-managed -- the budget evaluator flips it;
the API does not accept it from the request.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api_server.db.domain import BudgetPeriod, Project, ProjectStatus
from api_server.mcp.config import validate_mcp_servers_payload

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

# Free-form JSONB config blobs (`worker_config`, `repository_config`,
# `human_approval_policy`, `rag_knowledge_bases`) are typed loosely until
# their own validated schemas land in Plans 02-05. We do NOT lock their
# shape here (that would pre-empt unbuilt roadmap), but we DO cap their
# serialized size so a client can't stuff megabytes of arbitrary JSON into
# a project row that later gets folded into orchestrator context
# (api-routers-validation-6). 64 KiB is generous for config.
_MAX_JSON_CONFIG_BYTES = 65536


def _check_json_config_size(value: Any, field_name: str) -> None:
    """Reject a free-form JSON config blob over `_MAX_JSON_CONFIG_BYTES`.

    `default=str` keeps it from blowing up on stray non-serializable
    values — we only care about an order-of-magnitude size guard here.
    """
    if value is None:
        return
    size = len(json.dumps(value, default=str).encode("utf-8"))
    if size > _MAX_JSON_CONFIG_BYTES:
        raise ValueError(
            f"{field_name} is too large ({size} bytes); " f"max {_MAX_JSON_CONFIG_BYTES} bytes"
        )


def _validate_budget_invariants(self: BaseModel) -> BaseModel:
    """`custom` budget period must carry start_day + length_days; any
    other period must NOT. Currency required if amount is set."""
    period = getattr(self, "budget_period", None)
    amount = getattr(self, "budget_amount", None)
    start_day = getattr(self, "budget_period_start_day", None)
    length = getattr(self, "budget_period_length_days", None)
    currency = getattr(self, "budget_currency", None)

    if period == BudgetPeriod.CUSTOM:
        if start_day is None or length is None:
            raise ValueError(
                "budget_period='custom' requires "
                "budget_period_start_day and budget_period_length_days"
            )
    elif period is not None and (start_day is not None or length is not None):
        raise ValueError(
            "budget_period_start_day / budget_period_length_days only "
            "apply when budget_period='custom'"
        )

    if amount is not None and currency is None:
        raise ValueError("budget_currency is required when budget_amount is set")
    return self


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
class ProjectCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    team_id: UUID | None = None

    # Plan 06.13 task_06_13_03: optional project template to adopt. When
    # set, the new project is pre-granted the template's
    # `default_kb_grants` (built-in KB slugs → kb_projects rows). Absent
    # = a plain project with no auto-grants (backward-compatible).
    template_id: UUID | None = None

    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    rag_knowledge_bases: list[dict[str, Any]] = Field(default_factory=list)
    worker_config: dict[str, Any] = Field(default_factory=dict)
    repository_config: dict[str, Any] | None = None
    human_approval_policy: dict[str, Any] | None = None
    secrets_vault_id: UUID | None = None

    budget_amount: Decimal | None = Field(default=None, ge=0)
    budget_currency: str | None = Field(default=None, min_length=3, max_length=3)
    budget_period: BudgetPeriod | None = None
    budget_period_start_day: int | None = Field(default=None, ge=1, le=31)
    budget_period_length_days: int | None = Field(default=None, ge=1, le=366)

    @field_validator("mcp_servers", mode="after")
    @classmethod
    def _validate_mcp_servers(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return validate_mcp_servers_payload(value)

    @field_validator(
        "rag_knowledge_bases",
        "worker_config",
        "repository_config",
        "human_approval_policy",
        mode="after",
    )
    @classmethod
    def _cap_json_config(cls, value: Any, info: Any) -> Any:
        _check_json_config_size(value, info.field_name)
        return value

    @model_validator(mode="after")
    def _budget_invariants(self) -> ProjectCreateRequest:
        return _validate_budget_invariants(self)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
class ProjectUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: ProjectStatus | None = None
    team_id: UUID | None = None

    mcp_servers: list[dict[str, Any]] | None = None
    rag_knowledge_bases: list[dict[str, Any]] | None = None
    worker_config: dict[str, Any] | None = None
    repository_config: dict[str, Any] | None = None
    human_approval_policy: dict[str, Any] | None = None
    secrets_vault_id: UUID | None = None

    budget_amount: Decimal | None = Field(default=None, ge=0)
    budget_currency: str | None = Field(default=None, min_length=3, max_length=3)
    budget_period: BudgetPeriod | None = None
    budget_period_start_day: int | None = Field(default=None, ge=1, le=31)
    budget_period_length_days: int | None = Field(default=None, ge=1, le=366)

    @field_validator("mcp_servers", mode="after")
    @classmethod
    def _validate_mcp_servers(
        cls, value: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]] | None:
        if value is None:
            return None
        return validate_mcp_servers_payload(value)

    @field_validator(
        "rag_knowledge_bases",
        "worker_config",
        "repository_config",
        "human_approval_policy",
        mode="after",
    )
    @classmethod
    def _cap_json_config(cls, value: Any, info: Any) -> Any:
        _check_json_config_size(value, info.field_name)
        return value

    @model_validator(mode="after")
    def _budget_invariants(self) -> ProjectUpdateRequest:
        # Only enforce invariants when the caller is *changing* the
        # budget shape -- a no-op update with all None fields skips.
        if any(
            getattr(self, f) is not None
            for f in (
                "budget_amount",
                "budget_currency",
                "budget_period",
                "budget_period_start_day",
                "budget_period_length_days",
            )
        ):
            return _validate_budget_invariants(self)  # type: ignore[return-value]
        return self


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------
class ProjectResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    status: str
    team_id: UUID | None

    mcp_servers: list[dict[str, Any]]
    rag_knowledge_bases: list[dict[str, Any]]
    worker_config: dict[str, Any]
    repository_config: dict[str, Any] | None
    human_approval_policy: dict[str, Any] | None
    secrets_vault_id: UUID | None

    budget_amount: Decimal | None
    budget_currency: str | None
    budget_period: str | None
    budget_period_start_day: int | None
    budget_period_length_days: int | None
    paused_by_budget: bool
    is_template: bool

    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


def to_project_response(p: Project) -> ProjectResponse:
    return ProjectResponse(
        id=p.id,
        tenant_id=p.tenant_id,
        name=p.name,
        description=p.description,
        status=p.status,
        team_id=p.team_id,
        mcp_servers=p.mcp_servers,
        rag_knowledge_bases=p.rag_knowledge_bases,
        worker_config=p.worker_config,
        repository_config=p.repository_config,
        human_approval_policy=p.human_approval_policy,
        secrets_vault_id=p.secrets_vault_id,
        budget_amount=p.budget_amount,
        budget_currency=p.budget_currency,
        budget_period=p.budget_period,
        budget_period_start_day=p.budget_period_start_day,
        budget_period_length_days=p.budget_period_length_days,
        paused_by_budget=p.paused_by_budget,
        is_template=p.is_template,
        created_at=p.created_at,
        updated_at=p.updated_at,
        deleted_at=p.deleted_at,
    )
