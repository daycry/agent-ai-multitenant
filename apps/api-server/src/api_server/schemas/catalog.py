"""Pydantic schemas for /skills and /tools endpoints (task_01_05).

Both follow the same shape: tenant users CRUD their custom rows while
seeing platform-owned built-ins (is_builtin=true) read-only.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from shared_domain.tool_names import is_runtime_wired as _name_is_runtime_wired

from api_server.db.domain import (
    Skill,
    Tool,
    ToolCategory,
    ToolImplementationType,
    ToolSecurityLevel,
)

# Implementation types the agent-runtime wires from a serialised ToolSpec
# (``register_tool_specs``): a custom tool of one of these executes regardless
# of its name. A ``builtin`` tool only executes if its canonical name is in the
# runtime's builtin registrable set (``RUNTIME_WIRED_TOOL_NAMES``).
_SPEC_WIRED_IMPL_TYPES: frozenset[str] = frozenset(
    {
        ToolImplementationType.HTTP_ENDPOINT.value,
        ToolImplementationType.PYTHON_FUNCTION.value,
        ToolImplementationType.DOCKER_COMMAND.value,
        ToolImplementationType.MCP_TOOL.value,
    }
)


def tool_is_runtime_wired(name: str, implementation_type: str) -> bool:
    """Whether a Tool is actually executable by the agent-runtime (ADR 0049).

    A typed row (``http_endpoint`` / ``python_function`` / ``docker_command`` /
    ``mcp_tool``) is wired by ``register_tool_specs`` whatever its name. A
    ``builtin`` row is wired only when its canonical name is in the runtime's
    builtin registrable set — so ``read_file`` / ``run_pytest`` /
    ``semantic_search`` (→ ``rag_search``) are wired, while ``apply_patch`` /
    ``search_code`` / ``summarize_text`` (and the retired ``git_*``) are not.
    """
    if implementation_type in _SPEC_WIRED_IMPL_TYPES:
        return True
    return bool(_name_is_runtime_wired(name))


_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

_SLUG_SEP_RE = re.compile(r"[\s\-]+")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9_.]")
_SLUG_COLLAPSE_RE = re.compile(r"_+")


def normalize_tool_name(name: str) -> str:
    """Normalise a tool name to slug-case (ADR 0049, task_06_18_04).

    Lower-cases, turns runs of whitespace/hyphens into a single ``_`` and drops
    any remaining character that is not ``[a-z0-9_.]`` — so ``"Read File"`` and
    ``read_file`` collapse to the same slug and cannot coexist as duplicates.
    The dot is preserved because MCP tools are namespaced ``<server>.<tool>``.
    """
    slug = _SLUG_SEP_RE.sub("_", name.strip().lower())
    slug = _SLUG_STRIP_RE.sub("", slug)
    slug = _SLUG_COLLAPSE_RE.sub("_", slug).strip("_")
    return slug


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
    category: ToolCategory
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    implementation_type: ToolImplementationType
    implementation_ref: str | None = Field(default=None, max_length=500)
    security_level: ToolSecurityLevel = ToolSecurityLevel.SAFE
    timeout_seconds: int = Field(default=60, gt=0, le=3600)
    rate_limit_per_minute: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def _slugify_name(cls, v: str) -> str:
        slug = normalize_tool_name(v)
        if not slug:
            raise ValueError("name must contain at least one slug-safe character")
        return slug


class ToolUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    category: ToolCategory | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    implementation_type: ToolImplementationType | None = None
    implementation_ref: str | None = Field(default=None, max_length=500)
    security_level: ToolSecurityLevel | None = None
    timeout_seconds: int | None = Field(default=None, gt=0, le=3600)
    rate_limit_per_minute: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def _slugify_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        slug = normalize_tool_name(v)
        if not slug:
            raise ValueError("name must contain at least one slug-safe character")
        return slug


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
    #: Derived (ADR 0049): whether the agent-runtime can actually execute this
    #: tool. The catalog uses it to mark "No disponible aún" and to refuse
    #: assigning a tool that would die as a silent ``unknown tool``.
    is_runtime_wired: bool
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
        is_runtime_wired=tool_is_runtime_wired(t.name, t.implementation_type),
        created_at=t.created_at,
        updated_at=t.updated_at,
        deleted_at=t.deleted_at,
    )
