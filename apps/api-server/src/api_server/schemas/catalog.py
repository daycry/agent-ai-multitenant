"""Pydantic schemas for /skills and /tools endpoints (task_01_05).

Both follow the same shape: tenant users CRUD their custom rows while
seeing platform-owned built-ins (is_builtin=true) read-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api_server.db.domain import Skill, Tool, ToolImplementationType, ToolSecurityLevel

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


# =============================================================================
# Skill
# =============================================================================
class SkillCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=64)
    description: str | None = None
    prompt_fragment: str = Field(min_length=1)
    required_tools: list[UUID] = Field(default_factory=list)


class SkillUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = None
    prompt_fragment: str | None = Field(default=None, min_length=1)
    required_tools: list[UUID] | None = None


class SkillResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    name: str
    category: str
    description: str | None
    prompt_fragment: str
    required_tools: list[UUID]
    is_builtin: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


def to_skill_response(s: Skill) -> SkillResponse:
    return SkillResponse(
        id=s.id,
        tenant_id=s.tenant_id,
        name=s.name,
        category=s.category,
        description=s.description,
        prompt_fragment=s.prompt_fragment,
        # required_tools is JSONB list of strings -- coerce to UUIDs for the API contract.
        required_tools=[UUID(str(t)) for t in (s.required_tools or [])],
        is_builtin=s.is_builtin,
        created_at=s.created_at,
        updated_at=s.updated_at,
        deleted_at=s.deleted_at,
    )


# =============================================================================
# Tool
# =============================================================================
class ToolCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    category: str = Field(min_length=1, max_length=64)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    implementation_type: ToolImplementationType
    implementation_ref: str | None = Field(default=None, max_length=500)
    security_level: ToolSecurityLevel = ToolSecurityLevel.SAFE
    timeout_seconds: int = Field(default=60, gt=0, le=3600)
    rate_limit_per_minute: int | None = Field(default=None, ge=1)


class ToolUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    category: str | None = Field(default=None, min_length=1, max_length=64)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    implementation_type: ToolImplementationType | None = None
    implementation_ref: str | None = Field(default=None, max_length=500)
    security_level: ToolSecurityLevel | None = None
    timeout_seconds: int | None = Field(default=None, gt=0, le=3600)
    rate_limit_per_minute: int | None = Field(default=None, ge=1)


class ToolResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    category: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    implementation_type: str
    implementation_ref: str | None
    security_level: str
    timeout_seconds: int
    rate_limit_per_minute: int | None
    is_builtin: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


def to_tool_response(t: Tool) -> ToolResponse:
    return ToolResponse(
        id=t.id,
        tenant_id=t.tenant_id,
        name=t.name,
        description=t.description,
        category=t.category,
        input_schema=t.input_schema,
        output_schema=t.output_schema,
        implementation_type=t.implementation_type,
        implementation_ref=t.implementation_ref,
        security_level=t.security_level,
        timeout_seconds=t.timeout_seconds,
        rate_limit_per_minute=t.rate_limit_per_minute,
        is_builtin=t.is_builtin,
        created_at=t.created_at,
        updated_at=t.updated_at,
        deleted_at=t.deleted_at,
    )
