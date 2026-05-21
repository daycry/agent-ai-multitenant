"""Pydantic schemas for /agents endpoints.

Field-name note: the DB column is `model_config` (spec §3.1.3) but
Pydantic v2 reserves that name for the per-model BaseModel.model_config
class attribute. We expose it under the Python attribute `llm_config`
with an explicit alias so the JSON contract stays `model_config`.
Callers using Python objects pass `llm_config=...`; HTTP clients see
`model_config` in JSON, both incoming and outgoing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api_server.db.domain import (
    Agent,
    AgentRole,
    AgentScope,
    AgentType,
    MemoryScope,
)

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
class AgentCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    avatar_url: str | None = Field(default=None, max_length=500)
    agent_type: AgentType = AgentType.AI
    role: AgentRole
    system_prompt: str = Field(min_length=1)
    llm_config: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    memory_scope: MemoryScope = MemoryScope.PRIVATE
    review_capability: bool = False
    max_concurrent_tasks: int = Field(default=1, ge=1, le=64)
    is_template: bool = False

    # Linked-vs-forked (task_01_03) -- tenant users may only create
    # `project_local` or `global_tenant_template`. `global_builtin` is
    # rejected at the router layer (System-Admin-only path).
    scope: AgentScope = AgentScope.PROJECT_LOCAL
    project_id: UUID | None = None

    @model_validator(mode="after")
    def _scope_project_consistency(self) -> AgentCreateRequest:
        """Mirror the DB CHECK constraint so the API returns 422 instead
        of letting Postgres raise a constraint violation at INSERT time."""
        if self.scope == AgentScope.PROJECT_LOCAL and self.project_id is None:
            raise ValueError("project_id is required when scope='project_local'")
        if self.scope != AgentScope.PROJECT_LOCAL and self.project_id is not None:
            raise ValueError("project_id must be null for non-project_local scopes")
        return self


# ---------------------------------------------------------------------------
# Update — all fields optional; only sent values are touched
# ---------------------------------------------------------------------------
class AgentUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    avatar_url: str | None = Field(default=None, max_length=500)
    agent_type: AgentType | None = None
    role: AgentRole | None = None
    system_prompt: str | None = Field(default=None, min_length=1)
    llm_config: dict[str, Any] | None = Field(default=None, alias="model_config")
    memory_scope: MemoryScope | None = None
    review_capability: bool | None = None
    max_concurrent_tasks: int | None = Field(default=None, ge=1, le=64)
    is_template: bool | None = None
    # scope + project_id stay set-once. Re-scoping an agent would break
    # the linked-vs-forked invariants; do it via a separate "fork" endpoint
    # (task_01_15).
    anchored_version: str | None = Field(default=None, max_length=32)


# ---------------------------------------------------------------------------
# Fork
# ---------------------------------------------------------------------------
class AgentForkRequest(BaseModel):
    """Clone a visible agent into a project_local copy (spec §5.7).

    `project_id` is mandatory -- a fork always lands in a specific
    project of the calling tenant. Optional `name` and `system_prompt`
    let the caller customize the fork at creation; all other fields
    can be tweaked via PUT afterwards.
    """

    model_config = _BASE_CONFIG

    project_id: UUID
    name: str | None = Field(default=None, min_length=1, max_length=120)
    system_prompt: str | None = Field(default=None, min_length=1)


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------
class AgentResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    avatar_url: str | None
    agent_type: str
    role: str
    system_prompt: str
    llm_config: dict[str, Any] = Field(alias="model_config")
    memory_scope: str
    review_capability: bool
    max_concurrent_tasks: int
    is_template: bool
    scope: str
    project_id: UUID | None
    forked_from_agent_id: UUID | None
    forked_from_version: str | None
    anchored_version: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


# ---------------------------------------------------------------------------
# Fork diff
# ---------------------------------------------------------------------------
class AgentFieldDiff(BaseModel):
    model_config = _BASE_CONFIG

    fork: Any
    source: Any


class AgentMergeRequest(BaseModel):
    """Pull selected fields from the source into the fork (spec §5.7).

    `fields` is the list of column names the caller wants to absorb.
    Any field not listed stays untouched on the fork. After a successful
    merge, `anchored_version` is bumped to the source's current
    `updated_at` so subsequent diffs reflect the new baseline.
    """

    model_config = _BASE_CONFIG

    fields: list[str] = Field(min_length=1)


class AgentDiffResponse(BaseModel):
    """Field-by-field diff between a fork and its source agent.

    `source_moved` is true when the source has been updated since the
    fork point (captured in `forked_from_version`). UI can use this to
    decide whether to offer the "absorb upstream improvements" action.
    """

    model_config = _BASE_CONFIG

    fork_id: UUID
    source_id: UUID
    forked_from_version: str | None
    source_current_version: str | None
    source_moved: bool
    source_deleted: bool
    fields: dict[str, AgentFieldDiff]


def to_agent_response(a: Agent) -> AgentResponse:
    """ORM -> DTO with the `model_config` rename baked in.

    We go through `model_validate` with a dict because the field is
    aliased (`llm_config` Python name <-> `model_config` JSON key) and
    Pydantic's mypy plugin doesn't expose the field-name kwarg on the
    constructor when an alias is present. The alias is what the API
    contract uses, so the dict key matches the wire format.
    """
    payload: dict[str, Any] = {
        "id": a.id,
        "tenant_id": a.tenant_id,
        "name": a.name,
        "description": a.description,
        "avatar_url": a.avatar_url,
        "agent_type": a.agent_type,
        "role": a.role,
        "system_prompt": a.system_prompt,
        "model_config": a.model_config,
        "memory_scope": a.memory_scope,
        "review_capability": a.review_capability,
        "max_concurrent_tasks": a.max_concurrent_tasks,
        "is_template": a.is_template,
        "scope": a.scope,
        "project_id": a.project_id,
        "forked_from_agent_id": a.forked_from_agent_id,
        "forked_from_version": a.forked_from_version,
        "anchored_version": a.anchored_version,
        "created_at": a.created_at,
        "updated_at": a.updated_at,
        "deleted_at": a.deleted_at,
    }
    return AgentResponse.model_validate(payload)
