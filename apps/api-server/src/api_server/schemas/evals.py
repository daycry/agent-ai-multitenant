"""Pydantic schemas for the eval data foundation (Plan 14 Fase A).

Shapes the request / response bodies for:

  * promoting a real, APPROVED task into a tenant golden dataset as a dataset
    item (task_14_02) — copy the task's input + the approved execution's output
    as the reference, idempotently;
  * the minimal dataset list / create surface the Promote UI needs to pick or
    create the target dataset (the full CRUD is task_14_03).

Multi-tenancy (CLAUDE.md principle 1): every row these schemas project is
tenant-owned (``eval_datasets`` / ``eval_dataset_items`` carry ``tenant_id``
NOT NULL + RLS). A tenant's golden dataset (its data AND its criteria) is
visible only to that tenant — the golden dataset is PER-TENANT (Plan 14
Decisiones Clave).
"""

from __future__ import annotations

from datetime import datetime
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


__all__ = [
    "EvalDatasetCreateRequest",
    "EvalDatasetResponse",
    "PromoteToDatasetRequest",
    "PromoteToDatasetResponse",
]
