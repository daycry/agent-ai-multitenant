"""Pydantic schemas for /projects/{id}/tasks endpoints (task_01_08).

Tasks are the Kanban unit. Status transitions go through the standard
PUT endpoint -- there is no separate "move" endpoint; Plan 01 keeps the
state machine permissive (any status -> any status). Plan 02 adds the
orchestrator that enforces valid transitions.

Dependencies are exposed inline (`depends_on: list[UUID]`). POST/PUT
accept the full list and the router rewrites the junction rows
atomically. Cycle detection is the orchestrator's job (Plan 02); the
DB only blocks self-loops via the ck_task_dependencies_no_self_loop
constraint.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api_server.db.domain import Task, TaskComplexity, TaskPriority, TaskStatus

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
class TaskCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    status: TaskStatus = TaskStatus.BACKLOG
    priority: TaskPriority = TaskPriority.MEDIUM
    plan_id: UUID | None = None
    assigned_agent_id: UUID | None = None
    reviewer_agent_id: UUID | None = None
    acceptance_criteria: list[Any] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    estimated_complexity: TaskComplexity | None = None
    max_retries: int = Field(default=3, ge=0, le=20)
    depends_on: list[UUID] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
class TaskUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    plan_id: UUID | None = None
    assigned_agent_id: UUID | None = None
    reviewer_agent_id: UUID | None = None
    acceptance_criteria: list[Any] | None = None
    inputs: dict[str, Any] | None = None
    estimated_complexity: TaskComplexity | None = None
    max_retries: int | None = Field(default=None, ge=0, le=20)
    # When None the router leaves dependencies untouched. Empty list
    # explicitly removes them all. Otherwise the list replaces the
    # current set.
    depends_on: list[UUID] | None = None


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------
class TaskResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    project_id: UUID
    plan_id: UUID | None

    title: str
    description: str | None
    status: str
    priority: str

    assigned_agent_id: UUID | None
    reviewer_agent_id: UUID | None

    acceptance_criteria: list[Any]
    inputs: dict[str, Any]
    estimated_complexity: str | None
    retry_count: int
    max_retries: int

    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    depends_on: list[UUID]


# ---------------------------------------------------------------------------
# Acceptance-criteria generation (LLM proposal — NOT persisted by this endpoint)
# ---------------------------------------------------------------------------
class GeneratedAcceptanceCriteria(BaseModel):
    """The LLM's proposed acceptance criteria for one task. The operator reviews
    (and, when the task already had criteria, confirms against a comparison)
    before saving them via the normal PUT — so generation never overwrites."""

    model_config = _BASE_CONFIG

    acceptance_criteria: list[str]


def to_task_response(t: Task, depends_on: list[UUID]) -> TaskResponse:
    return TaskResponse(
        id=t.id,
        tenant_id=t.tenant_id,
        project_id=t.project_id,
        plan_id=t.plan_id,
        title=t.title,
        description=t.description,
        status=t.status,
        priority=t.priority,
        assigned_agent_id=t.assigned_agent_id,
        reviewer_agent_id=t.reviewer_agent_id,
        acceptance_criteria=t.acceptance_criteria,
        inputs=t.inputs,
        estimated_complexity=t.estimated_complexity,
        retry_count=t.retry_count,
        max_retries=t.max_retries,
        started_at=t.started_at,
        completed_at=t.completed_at,
        created_at=t.created_at,
        updated_at=t.updated_at,
        depends_on=depends_on,
    )
