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
from decimal import Decimal
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
    # ADR 0107: meta del ciclo de correcciones tras un rechazo humano —
    # ``{session_id, reason, task_ids, created_at, status}``. Sin campo
    # propio, un PUT con `specification` la perdería (pydantic descarta
    # las claves no declaradas).
    corrections: list[dict[str, Any]] = Field(default_factory=list)

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
    # Auto-PR result at plan close (P6): URL + branch of the opened PR, or the
    # failure reason. NULL until the plan closes / when the project has no remote.
    pr_url: str | None = None
    pr_branch: str | None = None
    pr_error: str | None = None
    conversation_id: UUID | None
    specification: dict[str, Any]
    created_by: UUID | None
    approved_by: UUID | None
    approved_at: datetime | None
    # First signature on a double-firma plan (task_03_25). NULL on
    # single-firma plans; the second/final signer lives in approved_*.
    first_approved_by: UUID | None
    first_approved_at: datetime | None
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
        pr_url=p.pr_url,
        pr_branch=p.pr_branch,
        pr_error=p.pr_error,
        conversation_id=p.conversation_id,
        specification=p.specification,
        created_by=p.created_by,
        approved_by=p.approved_by,
        approved_at=p.approved_at,
        first_approved_by=p.first_approved_by,
        first_approved_at=p.first_approved_at,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


__all__ = [
    "AICostBreakdownResponse",
    "CostBreakdownResponse",
    "HumanCostBreakdownResponse",
    "PlanAcceptCorrectionsRequest",
    "PlanCommentCreateRequest",
    "PlanCommentResponse",
    "PlanCreateRequest",
    "PlanResponse",
    "PlanSpecification",
    "PlanSyncRequest",
    "PlanSyncResponse",
    "PlanUpdateRequest",
    "TaskAICostResponse",
    "TaskHumanCostResponse",
    "to_plan_comment_response",
    "to_plan_response",
]


# ---------------------------------------------------------------------------
# Cost breakdown (task_03_24) — read-only response over the pure
# functions in `api_server.chat.cost`. The router recomputes on demand
# instead of snapshotting because the catalog and hourly rate change.
# ---------------------------------------------------------------------------
class TaskHumanCostResponse(BaseModel):
    model_config = _BASE_CONFIG

    task_id: str
    title: str
    hours: Decimal
    cost: Decimal


class HumanCostBreakdownResponse(BaseModel):
    model_config = _BASE_CONFIG

    currency: str
    hourly_rate: Decimal
    total_hours: Decimal
    total_cost: Decimal
    tasks: list[TaskHumanCostResponse]


class TaskAICostResponse(BaseModel):
    model_config = _BASE_CONFIG

    task_id: str
    title: str
    complexity: str
    model_id: str
    tokens_in_min: int
    tokens_in_max: int
    tokens_out_min: int
    tokens_out_max: int
    cost_min: Decimal
    cost_max: Decimal


class AICostBreakdownResponse(BaseModel):
    model_config = _BASE_CONFIG

    currency: str
    default_model_id: str
    cost_min: Decimal
    cost_max: Decimal
    tasks: list[TaskAICostResponse]
    missing_models: list[str]


class CostBreakdownResponse(BaseModel):
    """Combined human + AI cost breakdown for the plan detail page."""

    model_config = _BASE_CONFIG

    human: HumanCostBreakdownResponse
    ai: AICostBreakdownResponse


# ---------------------------------------------------------------------------
# Inline plan comments (task_03_21)
# ---------------------------------------------------------------------------
class PlanCommentCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    target_kind: str = Field(pattern="^(plan|phase|task)$")
    target_ref: str | None = Field(default=None, max_length=120)
    content: str = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def _target_ref_consistency(self) -> PlanCommentCreateRequest:
        if self.target_kind == "plan":
            if self.target_ref:
                raise ValueError("target_kind='plan' must not carry target_ref")
        elif not self.target_ref:
            raise ValueError(f"target_kind='{self.target_kind}' requires target_ref")
        return self


class PlanCommentResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    plan_id: UUID
    target_kind: str
    target_ref: str | None
    author_user_id: UUID | None
    content: str
    created_at: datetime


def to_plan_comment_response(c) -> PlanCommentResponse:  # type: ignore[no-untyped-def]
    return PlanCommentResponse(
        id=c.id,
        tenant_id=c.tenant_id,
        plan_id=c.plan_id,
        target_kind=c.target_kind,
        target_ref=c.target_ref,
        author_user_id=c.author_user_id,
        content=c.content,
        created_at=c.created_at,
    )


# ---------------------------------------------------------------------------
# Plan → Kanban sync (task_03_27, task_03_28, task_03_29)
# ---------------------------------------------------------------------------
class PlanSyncRequest(BaseModel):
    """Body of POST /plans/{plan_id}/sync-to-kanban.

    The three scopes mirror the UI options:
      - ``total``: every task in the spec.
      - ``phase``: tasks of one phase (``phase_index`` required).
      - ``selection``: an explicit list of spec task ids.
    """

    model_config = _BASE_CONFIG

    scope: str = Field(pattern="^(total|phase|selection)$")
    phase_index: int | None = Field(default=None, ge=0)
    task_ids: list[str] | None = None

    @model_validator(mode="after")
    def _scope_consistency(self) -> PlanSyncRequest:
        if self.scope == "phase" and self.phase_index is None:
            raise ValueError("scope='phase' requires phase_index")
        if self.scope == "selection" and not self.task_ids:
            raise ValueError("scope='selection' requires a non-empty task_ids")
        return self


class PlanAcceptCorrectionsRequest(BaseModel):
    """Body de POST /plans/{plan_id}/accept-corrections (ADR 0107):
    los spec-ids de las tareas correctivas que el validador acepta."""

    model_config = _BASE_CONFIG

    task_ids: list[str] = Field(min_length=1)


class PlanSyncResponse(BaseModel):
    """Outcome the UI uses to render the toast + reload the Kanban.

    Maps directly from :class:`api_server.chat.sync_to_kanban.SyncResult`:

      - ``created_task_ids``: spec id -> newly created Task.id
      - ``skipped_task_ids``: spec id -> existing Task.id (idempotency)
      - ``dependencies_created``: number of task_dependencies rows added
    """

    model_config = _BASE_CONFIG

    created_task_ids: dict[str, UUID] = Field(default_factory=dict)
    skipped_task_ids: dict[str, UUID] = Field(default_factory=dict)
    dependencies_created: int = 0
