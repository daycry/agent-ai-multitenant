"""Pydantic schemas for the eval data foundation (Plan 14 Fase A).

Shapes the request / response bodies for:

  * promoting a real, APPROVED task into a tenant golden dataset as a dataset
    item (task_14_02) — copy the task's input + the approved execution's output
    as the reference, idempotently;
  * the full dataset / criteria / item CRUD (task_14_03): create / list / get /
    update / delete datasets, manage each dataset's judging criteria (rubric /
    weight / pass threshold consumed by the LLM-as-judge in Fase B) and its
    golden items.

Multi-tenancy (CLAUDE.md principle 1): every row these schemas project is
tenant-owned (``eval_datasets`` / ``eval_dataset_items`` / ``eval_criteria``
carry ``tenant_id`` NOT NULL + RLS). A tenant's golden dataset (its data AND
its criteria) is visible only to that tenant — the golden dataset is PER-TENANT
(Plan 14 Decisiones Clave).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api_server.db.evals import EvalDatasetKind


# ---------------------------------------------------------------------------
# Datasets — the minimal pick/create surface the Promote UI needs
# ---------------------------------------------------------------------------
class EvalDatasetCreateRequest(BaseModel):
    """Body for creating a per-tenant golden dataset (tenant_admin).

    The full dataset/criteria CRUD is task_14_03; this is the slimmer create
    the Promote dialog uses so an operator can mint a target dataset inline.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    kind: EvalDatasetKind = Field(default=EvalDatasetKind.GOLDEN)
    target_agent_id: UUID | None = Field(default=None)
    target_role: str | None = Field(default=None, max_length=32)


class EvalDatasetUpdateRequest(BaseModel):
    """Partial update of a golden dataset (task_14_03, tenant_admin).

    Every field is optional: only the keys the client actually sends are
    applied (``exclude_unset``). An explicit ``null`` clears the column
    (e.g. detach the target agent), a missing key leaves it untouched. The
    ``kind`` enum is remapped to its string value before assignment.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    kind: EvalDatasetKind | None = Field(default=None)
    target_agent_id: UUID | None = Field(default=None)
    target_role: str | None = Field(default=None, max_length=32)


class EvalDatasetResponse(BaseModel):
    """A golden dataset's metadata (NEVER another tenant's — RLS scoped)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    kind: str
    target_agent_id: UUID | None
    target_role: str | None
    item_count: int = 0
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Criteria — the judging rubric (weight / pass threshold) a dataset scores on
# ---------------------------------------------------------------------------
class EvalCriterionCreateRequest(BaseModel):
    """Body for adding a judging criterion to a dataset (tenant_admin).

    The criterion carries the rubric (``judge_instruction``) the LLM-as-judge
    follows in Fase B, a ``weight`` (>= 0; its relative contribution to the
    item's overall verdict) and a ``pass_threshold`` in [0, 1] (the minimum
    normalised score for the criterion to count as passed).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    judge_instruction: str = Field(min_length=1)
    weight: Decimal = Field(default=Decimal("1"), ge=0, max_digits=6, decimal_places=3)
    pass_threshold: Decimal = Field(
        default=Decimal("0.5"), ge=0, le=1, max_digits=4, decimal_places=3
    )


class EvalCriterionUpdateRequest(BaseModel):
    """Partial update of a judging criterion (task_14_03, tenant_admin)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    judge_instruction: str | None = Field(default=None, min_length=1)
    weight: Decimal | None = Field(default=None, ge=0, max_digits=6, decimal_places=3)
    pass_threshold: Decimal | None = Field(default=None, ge=0, le=1, max_digits=4, decimal_places=3)


class EvalCriterionResponse(BaseModel):
    """A judging criterion (NEVER another tenant's — RLS scoped)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dataset_id: UUID
    name: str
    description: str | None
    judge_instruction: str
    weight: Decimal
    pass_threshold: Decimal
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Items — the golden rows (input + reference output) a run is graded against
# ---------------------------------------------------------------------------
class EvalDatasetItemCreateRequest(BaseModel):
    """Body for adding a hand-authored golden item to a dataset (tenant_admin).

    Promotion (task_14_02) is the usual way items land in a dataset, but a
    tenant_admin can also author one directly: the ``input`` the subject is run
    against and an optional ``expected_output`` reference. A hand-authored item
    has no ``source_task_id`` (it never collides on the idempotency UNIQUE).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    input: dict[str, Any] = Field(default_factory=dict)
    expected_output: str | None = Field(default=None)
    reference_metadata: dict[str, Any] = Field(default_factory=dict)


class EvalDatasetItemUpdateRequest(BaseModel):
    """Partial update of a golden item (task_14_03, tenant_admin)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    input: dict[str, Any] | None = Field(default=None)
    expected_output: str | None = Field(default=None)
    reference_metadata: dict[str, Any] | None = Field(default=None)


class EvalDatasetItemResponse(BaseModel):
    """A golden dataset item (NEVER another tenant's — RLS scoped)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dataset_id: UUID
    input: dict[str, Any]
    expected_output: str | None
    reference_metadata: dict[str, Any]
    source_task_id: UUID | None
    source_execution_id: UUID | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Promote a real APPROVED task into a dataset as a golden item
# ---------------------------------------------------------------------------
class PromoteToDatasetRequest(BaseModel):
    """Body for promoting an approved task into a golden dataset (task_14_02).

    The caller picks an EXISTING dataset (``dataset_id``) — the dialog can
    create one first via the dataset create endpoint. ``execution_id`` pins a
    SPECIFIC approved execution to copy the reference output from; when omitted
    the latest ``done`` execution of the task is used. ``allow_unapproved``
    (default false) is the explicit escape hatch: a task that is not ``done``
    is rejected (422) UNLESS this flag is set, so promoting an unapproved task
    is always a deliberate, opt-in choice.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    dataset_id: UUID
    execution_id: UUID | None = Field(default=None)
    allow_unapproved: bool = Field(default=False)


class PromoteToDatasetResponse(BaseModel):
    """The outcome of promoting a task into a dataset (task_14_02).

    ``created`` is False when the task was ALREADY promoted into this dataset
    (idempotent — the existing item is returned, nothing is duplicated). The
    item carries the copied ``input`` and the ``expected_output`` reference plus
    provenance back to the real task/execution it came from.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dataset_id: UUID
    created: bool
    input: dict[str, Any]
    expected_output: str | None
    source_task_id: UUID | None
    source_execution_id: UUID | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Eval runs — read view exposing the standard metrics (task_14_05)
# ---------------------------------------------------------------------------
class EvalRunResponse(BaseModel):
    """One eval run with its denormalised standard metrics (NEVER another
    tenant's — RLS scoped).

    The scalar roll-up columns (``pass_rate`` / ``mean_*``) populated when the
    run completes (task_14_05), plus ``aggregate_metrics`` JSONB carrying the
    extras the columns lack (``p50_latency_ms`` / ``p95_latency_ms`` + the
    per-metric counts). All metrics are ``None`` until the run completes / when
    nothing reported a given measurement (no divide-by-zero).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dataset_id: UUID
    status: str
    subject_agent_id: UUID | None
    subject_prompt_version: str | None
    judge_model: str | None
    started_at: datetime | None
    finished_at: datetime | None
    total_items: int
    passed_items: int
    pass_rate: Decimal | None
    mean_latency_ms: Decimal | None
    mean_tokens: Decimal | None
    mean_cost_usd: Decimal | None
    aggregate_metrics: dict[str, Any]
    created_at: datetime
    updated_at: datetime


__all__ = [
    "EvalCriterionCreateRequest",
    "EvalCriterionResponse",
    "EvalCriterionUpdateRequest",
    "EvalDatasetCreateRequest",
    "EvalDatasetItemCreateRequest",
    "EvalDatasetItemResponse",
    "EvalDatasetItemUpdateRequest",
    "EvalDatasetResponse",
    "EvalDatasetUpdateRequest",
    "EvalRunResponse",
    "PromoteToDatasetRequest",
    "PromoteToDatasetResponse",
]
