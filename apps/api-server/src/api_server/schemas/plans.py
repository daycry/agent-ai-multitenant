"""Pydantic schemas for /projects/{project_id}/plans (Plan 03 task_03_14).

A plan carries the canonical template the team builds during a
planning chat:

  - ``summary``     — title + scope + decisions + risks of the plan.
  - ``phases``      — ordered list of phases each with their own tasks.
  - ``tasks``       — flat list of task specs (titles, complexity,
                      acceptance, dependencies BY task id, estimated
                      hours, automated tests).
  - ``estimates``   — calendar / effort / cost breakdown.
  - ``metadata``    — template version, generator info, free-form.

We type the payload loosely (dict[str, Any]) on purpose — the team's
output evolves, and the inner shape is owned by the planning sub-graph
not by the REST contract. Validators enforce the basics: DAG sanity
(no self-loop, dependencies reference declared tasks).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api_server.db.domain import Plan, PlanStatus

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Specification shape (loose typing — see module docstring)
# ---------------------------------------------------------------------------
class PlanSpecification(BaseModel):
    """The canonical template body that lives in `plans.specification`."""

    model_config = _BASE_CONFIG

    summary: dict[str, Any] = Field(default_factory=dict)
    phases: list[dict[str, Any]] = Field(default_factory=list)
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    estimates: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_task_dependencies(self) -> PlanSpecification:
        """Basic referential integrity for the flat `tasks` list:

          - Every task must have an `id` (free-form string).
          - A task cannot depend on itself.
          - `depends_on` entries must reference declared task ids.

        The full DAG (cycle) check lives in `api_server.chat.dag` and
        runs in the router so we can return a focused 422 on cycles.
        """
        task_ids: list[str] = []
        for idx, task in enumerate(self.tasks):
            tid = task.get("id")
            if not tid or not isinstance(tid, str):
                raise ValueError(f"tasks[{idx}].id is required and must be a string")
            task_ids.append(tid)
        id_set = set(task_ids)
        if len(id_set) != len(task_ids):
            raise ValueError("tasks contain duplicate ids")
        for task in self.tasks:
            depends_on = task.get("depends_on") or []
            if not isinstance(depends_on, list):
                raise ValueError(f"tasks[{task['id']}].depends_on must be a list")
            if task["id"] in depends_on:
                raise ValueError(f"tasks[{task['id']}] cannot depend on itself")
            for dep in depends_on:
                if dep not in id_set:
                    raise ValueError(f"tasks[{task['id']}] depends on unknown task '{dep}'")
        return self


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
class PlanCreateRequest(BaseModel):
    """A plan can be created either inline (full specification body) or
    bootstrapped from a conversation. Both fields are optional — the
    minimum a caller has to provide is a `conversation_id` OR a
    `specification` (or neither, which makes an empty draft).
    """

    model_config = _BASE_CONFIG

    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    status: PlanStatus = PlanStatus.DRAFT
    conversation_id: UUID | None = None
    specification: PlanSpecification | None = None


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
class PlanUpdateRequest(BaseModel):
    """Partial update — only fields the caller sets are touched."""

    model_config = _BASE_CONFIG

    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    status: PlanStatus | None = None
    specification: PlanSpecification | None = None


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------
class PlanResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    project_id: UUID
    title: str
    description: str | None
    status: str
    conversation_id: UUID | None
    specification: dict[str, Any]
    created_by: UUID | None
    approved_by: UUID | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime


def to_plan_response(p: Plan) -> PlanResponse:
    return PlanResponse(
        id=p.id,
        tenant_id=p.tenant_id,
        project_id=p.project_id,
        title=p.title,
        description=p.description,
        status=p.status,
        conversation_id=p.conversation_id,
        specification=p.specification,
        created_by=p.created_by,
        approved_by=p.approved_by,
        approved_at=p.approved_at,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


__all__ = [
    "PlanCreateRequest",
    "PlanResponse",
    "PlanSpecification",
    "PlanUpdateRequest",
    "to_plan_response",
]
