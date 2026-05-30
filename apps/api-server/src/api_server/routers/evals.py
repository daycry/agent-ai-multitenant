"""Eval datasets + promote-to-golden (Plan 14 Fase A, task_14_02).

Two surfaces back the "Promote to dataset" flow:

  * ``GET/POST /eval-datasets`` — the minimal pick/create surface the Promote
    dialog needs to choose or mint the target golden dataset. The full
    dataset/criteria CRUD is task_14_03.
  * ``POST /tasks/{task_id}/promote-to-dataset`` — promote a real, APPROVED
    task into a tenant golden dataset as a dataset item: copy the task's input
    and the approved execution's output as the reference, idempotently.

RBAC + multi-tenancy (CLAUDE.md principle 1): every endpoint is
JWT-authenticated and gated on ``tenant_admin`` (the project-management gate
used across the codebase — the plan's "tenant_admin / project_owner" maps to
it, there is no distinct project_owner role) and runs on a tenant-scoped RLS
session, so an operator only ever sees / mutates eval data of their OWN tenant.
The golden dataset is PER-TENANT (Plan 14 Decisiones Clave): tenant A's task
can never be promoted into tenant B's dataset — the dataset is loaded under
A's RLS scope and 404s otherwise. The ``@pytest.mark.cross_tenant`` test pins
this.

Idempotency: a dataset item carries provenance (``source_task_id``); a partial
UNIQUE on ``(dataset_id, source_task_id)`` makes a second promote of the same
task into the same dataset a no-op (the existing item is returned with
``created=false``) rather than a duplicate.

Approval gate: a task that is not ``done`` is rejected (422) unless the caller
sets ``allow_unapproved`` — promoting an unapproved task is always a deliberate
opt-in.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_admin
from api_server.db.domain import Execution, ExecutionStatus, Task, TaskStatus
from api_server.db.evals import EvalDataset, EvalDatasetItem
from api_server.routers._helpers import get_writable_or_404, require_tenant_id
from api_server.schemas.evals import (
    EvalDatasetCreateRequest,
    EvalDatasetResponse,
    PromoteToDatasetRequest,
    PromoteToDatasetResponse,
)

router = APIRouter(tags=["evals"])


def _dataset_to_response(dataset: EvalDataset, *, item_count: int = 0) -> EvalDatasetResponse:
    return EvalDatasetResponse(
        id=dataset.id,
        name=dataset.name,
        description=dataset.description,
        kind=dataset.kind,
        target_agent_id=dataset.target_agent_id,
        target_role=dataset.target_role,
        item_count=item_count,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


# ---------------------------------------------------------------------------
# Datasets — minimal pick/create surface for the Promote UI
# ---------------------------------------------------------------------------
@router.get("/eval-datasets", response_model=list[EvalDatasetResponse])
async def list_eval_datasets(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[EvalDatasetResponse]:
    """List the tenant's golden datasets (newest first). tenant_admin.

    RLS scopes the listing to the caller's tenant — another tenant's datasets
    are invisible (the golden dataset is per-tenant). Soft-deleted datasets are
    excluded.
    """
    require_tenant_id(principal)
    result = await session.execute(
        select(EvalDataset)
        .where(EvalDataset.deleted_at.is_(None))
        .order_by(EvalDataset.created_at.desc())
    )
    datasets = list(result.scalars().all())
    counts = await _item_counts(session, [d.id for d in datasets])
    return [_dataset_to_response(d, item_count=counts.get(d.id, 0)) for d in datasets]


@router.post(
    "/eval-datasets",
    response_model=EvalDatasetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_eval_dataset(
    payload: EvalDatasetCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> EvalDatasetResponse:
    """Create a per-tenant golden dataset. tenant_admin.

    The Promote dialog uses this to mint a target dataset inline. The row is
    stamped with the caller's ``tenant_id`` and only ever visible under that
    tenant's RLS scope.
    """
    tenant_id = require_tenant_id(principal)
    dataset = EvalDataset(
        id=uuid7(),
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        kind=payload.kind.value,
        target_agent_id=payload.target_agent_id,
        target_role=payload.target_role,
    )
    session.add(dataset)
    await session.flush()
    await session.refresh(dataset)
    return _dataset_to_response(dataset, item_count=0)


# ---------------------------------------------------------------------------
# Promote a real APPROVED task into a dataset as a golden item
# ---------------------------------------------------------------------------
@router.post(
    "/tasks/{task_id}/promote-to-dataset",
    response_model=PromoteToDatasetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def promote_task_to_dataset(
    task_id: UUID,
    payload: PromoteToDatasetRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> PromoteToDatasetResponse:
    """Promote an APPROVED task into a golden dataset as an item. tenant_admin.

    Copies the task's input (title / description / acceptance criteria /
    inputs) and the approved execution's output (the reference the judge later
    compares against) into a new ``eval_dataset_items`` row, with provenance
    back to the real task/execution.

    Multi-tenancy: the task AND the dataset are both resolved under the
    caller's tenant RLS scope (404 otherwise), so a tenant can never promote
    its task into another tenant's dataset, nor promote another tenant's task.

    Idempotent: a second promote of the SAME task into the SAME dataset returns
    the existing item (``created=false``) — the partial UNIQUE on
    ``(dataset_id, source_task_id)`` prevents a duplicate.

    Approval gate: a task that is not ``done`` is a 422 unless the request sets
    ``allow_unapproved`` (an explicit, deliberate opt-in).
    """
    require_tenant_id(principal)

    # Resolve the source task under the caller's tenant RLS scope. Task uses
    # terminal statuses, not soft-delete, so disable soft_delete_aware.
    task = await get_writable_or_404(
        session,
        Task,
        task_id,
        principal,
        not_found_detail="task not found",
        soft_delete_aware=False,
    )

    # Approval gate: only an approved (done) task is a golden reference unless
    # the caller deliberately opts in.
    if task.status != TaskStatus.DONE.value and not payload.allow_unapproved:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "task is not approved (status != 'done'); "
                "set allow_unapproved=true to promote anyway"
            ),
        )

    # Resolve the target dataset under the SAME tenant RLS scope (per-tenant
    # golden dataset). A dataset of another tenant 404s.
    dataset = await get_writable_or_404(
        session,
        EvalDataset,
        payload.dataset_id,
        principal,
        not_found_detail="dataset not found",
    )

    execution = await _resolve_source_execution(
        session, task_id=task_id, execution_id=payload.execution_id
    )

    # Idempotency: return the existing item for this (dataset, task) pair.
    existing = await session.execute(
        select(EvalDatasetItem).where(
            EvalDatasetItem.dataset_id == dataset.id,
            EvalDatasetItem.source_task_id == task_id,
            EvalDatasetItem.deleted_at.is_(None),
        )
    )
    found = existing.scalar_one_or_none()
    if found is not None:
        return _item_to_response(found, created=False)

    item = EvalDatasetItem(
        id=uuid7(),
        tenant_id=dataset.tenant_id,
        dataset_id=dataset.id,
        input=_build_input(task),
        expected_output=execution.output if execution is not None else None,
        reference_metadata=_build_reference_metadata(task, execution),
        source_task_id=task_id,
        source_execution_id=execution.id if execution is not None else None,
    )
    session.add(item)
    await session.flush()
    await session.refresh(item)
    return _item_to_response(item, created=True)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
async def _item_counts(session: AsyncSession, dataset_ids: list[UUID]) -> dict[UUID, int]:
    """Live item counts per dataset (one grouped query). Empty for no ids."""
    if not dataset_ids:
        return {}
    result = await session.execute(
        select(EvalDatasetItem.dataset_id, func.count())
        .where(
            EvalDatasetItem.dataset_id.in_(dataset_ids),
            EvalDatasetItem.deleted_at.is_(None),
        )
        .group_by(EvalDatasetItem.dataset_id)
    )
    return {row[0]: row[1] for row in result.all()}


async def _resolve_source_execution(
    session: AsyncSession, *, task_id: UUID, execution_id: UUID | None
) -> Execution | None:
    """The execution whose output becomes the golden reference.

    With ``execution_id`` set, pin that exact execution (must belong to the
    task, under RLS, or 404). Otherwise use the task's latest ``done``
    execution; None when the task has no successful run (the reference output
    is then left NULL — a criteria-only golden item).
    """
    if execution_id is not None:
        result = await session.execute(
            select(Execution).where(
                Execution.id == execution_id,
                Execution.task_id == task_id,
            )
        )
        execution = result.scalar_one_or_none()
        if execution is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="execution not found for this task",
            )
        return execution

    result = await session.execute(
        select(Execution)
        .where(
            Execution.task_id == task_id,
            Execution.status == ExecutionStatus.DONE.value,
        )
        .order_by(Execution.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _build_input(task: Task) -> dict[str, object]:
    """The golden input copied from the real task (the prompt + its inputs)."""
    return {
        "title": task.title,
        "description": task.description,
        "acceptance_criteria": list(task.acceptance_criteria),
        "inputs": dict(task.inputs),
    }


def _build_reference_metadata(task: Task, execution: Execution | None) -> dict[str, object]:
    """Non-reference provenance kept alongside the golden item."""
    meta: dict[str, object] = {
        "promoted_from_task_status": task.status,
        "project_id": str(task.project_id),
    }
    if execution is not None:
        meta["execution_status"] = execution.status
    return meta


def _item_to_response(item: EvalDatasetItem, *, created: bool) -> PromoteToDatasetResponse:
    return PromoteToDatasetResponse(
        id=item.id,
        dataset_id=item.dataset_id,
        created=created,
        input=dict(item.input),
        expected_output=item.expected_output,
        source_task_id=item.source_task_id,
        source_execution_id=item.source_execution_id,
        created_at=item.created_at,
    )


__all__ = ["router"]
