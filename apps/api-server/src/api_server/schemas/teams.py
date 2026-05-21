"""Pydantic schemas for /teams endpoints (task_01_06).

A team is a tenant-scoped container of agents. The agents themselves
keep living in /agents; TeamMember is the M:N junction carrying per-team
metadata (role_in_team, is_team_leader, assignment_priority).

Responses bundle members inline so the admin UI can render a team with
its agents in one round-trip. For larger teams this is still cheap --
each row is small and the join is on the composite PK.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api_server.db.domain import Team, TeamMember

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Member sub-schemas
# ---------------------------------------------------------------------------
class TeamMemberAddRequest(BaseModel):
    model_config = _BASE_CONFIG

    agent_id: UUID
    role_in_team: str | None = Field(default=None, max_length=64)
    is_team_leader: bool = False
    assignment_priority: int = Field(default=100, ge=0, le=1000)


class TeamMemberUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    role_in_team: str | None = Field(default=None, max_length=64)
    is_team_leader: bool | None = None
    assignment_priority: int | None = Field(default=None, ge=0, le=1000)


class TeamMemberResponse(BaseModel):
    model_config = _BASE_CONFIG

    agent_id: UUID
    role_in_team: str | None
    is_team_leader: bool
    assignment_priority: int
    created_at: datetime
    updated_at: datetime


def to_member_response(m: TeamMember) -> TeamMemberResponse:
    return TeamMemberResponse(
        agent_id=m.agent_id,
        role_in_team=m.role_in_team,
        is_team_leader=m.is_team_leader,
        assignment_priority=m.assignment_priority,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------
class TeamCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    default_workflow_template_id: UUID | None = None
    shared_memory_namespace: str | None = Field(default=None, max_length=120)


class TeamUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    default_workflow_template_id: UUID | None = None
    shared_memory_namespace: str | None = Field(default=None, max_length=120)


class TeamResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    default_workflow_template_id: UUID | None
    shared_memory_namespace: str | None
    is_builtin: bool
    members: list[TeamMemberResponse]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


def to_team_response(t: Team, members: list[TeamMember]) -> TeamResponse:
    return TeamResponse(
        id=t.id,
        tenant_id=t.tenant_id,
        name=t.name,
        description=t.description,
        default_workflow_template_id=t.default_workflow_template_id,
        shared_memory_namespace=t.shared_memory_namespace,
        is_builtin=t.is_builtin,
        members=[to_member_response(m) for m in members],
        created_at=t.created_at,
        updated_at=t.updated_at,
        deleted_at=t.deleted_at,
    )
