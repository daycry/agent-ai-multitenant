"""Eval datasets + criteria + items CRUD and promote-to-golden (Plan 14 Fase A).

The eval data-foundation REST surface:

  * ``GET/POST/GET{id}/PUT/DELETE /eval-datasets`` — full CRUD of a tenant's
    per-tenant golden datasets (task_14_03; the pick/create subset also backs
    the Promote dialog).
  * ``GET/POST /eval-datasets/{id}/criteria`` + ``GET/PUT/DELETE
    /eval-criteria/{id}`` — the judging criteria of a dataset. Each criterion
    carries its judge rubric + weight + pass threshold, consumed by the
    LLM-as-judge in Fase B.
  * ``GET/POST /eval-datasets/{id}/items`` + ``GET/PUT/DELETE
    /eval-dataset-items/{id}`` — the golden items (input + reference output) a
    run is graded against. Promotion (below) is the usual source; a
    tenant_admin can also author one directly.
  * ``POST /tasks/{task_id}/promote-to-dataset`` — promote a real, APPROVED
    task into a tenant golden dataset as a dataset item: copy the task's input
    and the approved execution's output as the reference, idempotently
    (task_14_02).

RBAC + multi-tenancy (CLAUDE.md principle 1): every endpoint is
JWT-authenticated and gated on ``tenant_admin`` (the project-management gate
used across the codebase — the plan's "tenant_admin / project_owner" maps to
it, there is no distinct project_owner role) and runs on a tenant-scoped RLS
session, so an operator only ever sees / mutates eval data of their OWN tenant.
The golden dataset (its data AND its criteria) is PER-TENANT (Plan 14
Decisiones Clave): a dataset / criterion / item of another tenant is invisible
(404) — both the listing (RLS-scoped) and every by-id lookup
(``get_writable_or_404`` filters on ``tenant_id``) enforce it. The
``@pytest.mark.cross_tenant`` test pins this.

List endpoints are paginated (``limit``/``offset``) like the rest of the
codebase. Deletes are soft (``deleted_at``); a dataset delete cascades to its
criteria/items at the DB level (FK ON DELETE CASCADE) only on a hard delete, so
the soft-deleted dataset simply stops listing — its children are filtered out
by the dataset scope.

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
from api_server.db.evals import EvalCriterion, EvalDataset, EvalDatasetItem, EvalRun
from api_server.routers._helpers import (
    apply_partial_update,
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
)
from api_server.routers._pagination import apply_pagination, limit_query, offset_query
from api_server.schemas.evals import (
    EvalCriterionCreateRequest,
    EvalCriterionResponse,
    EvalCriterionUpdateRequest,
    EvalDatasetCreateRequest,
    EvalDatasetItemCreateRequest,
    EvalDatasetItemResponse,
    EvalDatasetItemUpdateRequest,
    EvalDatasetResponse,
    EvalDatasetUpdateRequest,
    EvalRunResponse,
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


async def _get_dataset_or_404(
    session: AsyncSession, dataset_id: UUID, principal: AuthPrincipal
) -> EvalDataset:
    """Load a live, tenant-owned dataset for read or write (404 otherwise).

    The ``tenant_id`` filter inside :func:`get_writable_or_404` is the
    per-tenant guard: another tenant's dataset never resolves.
    """
    return await get_writable_or_404(
        session, EvalDataset, dataset_id, principal, not_found_detail="dataset not found"
    )


# ---------------------------------------------------------------------------
# Datasets — full CRUD (the GET/POST subset also backs the Promote UI)
# ---------------------------------------------------------------------------
@router.get("/eval-datasets", response_model=list[EvalDatasetResponse])
async def list_eval_datasets(
    limit: int = limit_query(),
    offset: int = offset_query(),
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[EvalDatasetResponse]:
    """List the tenant's golden datasets (newest first), paginated. tenant_admin.

    RLS scopes the listing to the caller's tenant — another tenant's datasets
    are invisible (the golden dataset is per-tenant). Soft-deleted datasets are
    excluded.
    """
    require_tenant_id(principal)
    stmt = (
        select(EvalDataset)
        .where(EvalDataset.deleted_at.is_(None))
        .order_by(EvalDataset.created_at.desc(), EvalDataset.id)
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
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


@router.get("/eval-datasets/{dataset_id}", response_model=EvalDatasetResponse)
async def get_eval_dataset(
    dataset_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> EvalDatasetResponse:
    """Get one golden dataset by id. tenant_admin. 404 for another tenant's."""
    require_tenant_id(principal)
    dataset = await _get_dataset_or_404(session, dataset_id, principal)
    counts = await _item_counts(session, [dataset.id])
    return _dataset_to_response(dataset, item_count=counts.get(dataset.id, 0))


@router.put("/eval-datasets/{dataset_id}", response_model=EvalDatasetResponse)
async def update_eval_dataset(
    dataset_id: UUID,
    payload: EvalDatasetUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> EvalDatasetResponse:
    """Partial-update a golden dataset. tenant_admin. 404 for another tenant's."""
    require_tenant_id(principal)
    dataset = await _get_dataset_or_404(session, dataset_id, principal)
    apply_partial_update(dataset, payload, enum_fields=("kind",))
    await session.flush()
    await session.refresh(dataset)
    counts = await _item_counts(session, [dataset.id])
    return _dataset_to_response(dataset, item_count=counts.get(dataset.id, 0))


@router.delete("/eval-datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_eval_dataset(
    dataset_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Soft-delete a golden dataset. tenant_admin. 404 for another tenant's."""
    require_tenant_id(principal)
    dataset = await _get_dataset_or_404(session, dataset_id, principal)
    await soft_delete(session, dataset)


# ---------------------------------------------------------------------------
# Criteria — a dataset's judging rubric (weight / pass threshold for Fase B)
# ---------------------------------------------------------------------------
@router.get(
    "/eval-datasets/{dataset_id}/criteria",
    response_model=list[EvalCriterionResponse],
)
async def list_eval_criteria(
    dataset_id: UUID,
    limit: int = limit_query(),
    offset: int = offset_query(),
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[EvalCriterionResponse]:
    """List a dataset's judging criteria (oldest first), paginated. tenant_admin.

    The parent dataset is resolved under the caller's tenant RLS scope first
    (404 for another tenant's), so a tenant can never enumerate another
    tenant's criteria.
    """
    require_tenant_id(principal)
    await _get_dataset_or_404(session, dataset_id, principal)
    stmt = (
        select(EvalCriterion)
        .where(
            EvalCriterion.dataset_id == dataset_id,
            EvalCriterion.deleted_at.is_(None),
        )
        .order_by(EvalCriterion.created_at, EvalCriterion.id)
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    return [EvalCriterionResponse.model_validate(c) for c in result.scalars().all()]


@router.post(
    "/eval-datasets/{dataset_id}/criteria",
    response_model=EvalCriterionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_eval_criterion(
    dataset_id: UUID,
    payload: EvalCriterionCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> EvalCriterionResponse:
    """Add a judging criterion to a dataset. tenant_admin.

    The criterion inherits its parent dataset's ``tenant_id`` (resolved under
    the caller's RLS scope), so it is only ever visible to that tenant.
    """
    require_tenant_id(principal)
    dataset = await _get_dataset_or_404(session, dataset_id, principal)
    criterion = EvalCriterion(
        id=uuid7(),
        tenant_id=dataset.tenant_id,
        dataset_id=dataset.id,
        name=payload.name,
        description=payload.description,
        judge_instruction=payload.judge_instruction,
        weight=payload.weight,
        pass_threshold=payload.pass_threshold,
    )
    session.add(criterion)
    await session.flush()
    await session.refresh(criterion)
    return EvalCriterionResponse.model_validate(criterion)


@router.get("/eval-criteria/{criterion_id}", response_model=EvalCriterionResponse)
async def get_eval_criterion(
    criterion_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> EvalCriterionResponse:
    """Get one judging criterion by id. tenant_admin. 404 for another tenant's."""
    require_tenant_id(principal)
    criterion = await get_writable_or_404(
        session, EvalCriterion, criterion_id, principal, not_found_detail="criterion not found"
    )
    return EvalCriterionResponse.model_validate(criterion)


@router.put("/eval-criteria/{criterion_id}", response_model=EvalCriterionResponse)
async def update_eval_criterion(
    criterion_id: UUID,
    payload: EvalCriterionUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> EvalCriterionResponse:
    """Partial-update a criterion. tenant_admin. 404 for another tenant's."""
    require_tenant_id(principal)
    criterion = await get_writable_or_404(
        session, EvalCriterion, criterion_id, principal, not_found_detail="criterion not found"
    )
    apply_partial_update(criterion, payload)
    await session.flush()
    await session.refresh(criterion)
    return EvalCriterionResponse.model_validate(criterion)


@router.delete("/eval-criteria/{criterion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_eval_criterion(
    criterion_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Soft-delete a criterion. tenant_admin. 404 for another tenant's."""
    require_tenant_id(principal)
    criterion = await get_writable_or_404(
        session, EvalCriterion, criterion_id, principal, not_found_detail="criterion not found"
    )
    await soft_delete(session, criterion)


# ---------------------------------------------------------------------------
# Items — the golden rows (input + reference output) a run is graded against
# ---------------------------------------------------------------------------
@router.get(
    "/eval-datasets/{dataset_id}/items",
    response_model=list[EvalDatasetItemResponse],
)
async def list_eval_dataset_items(
    dataset_id: UUID,
    limit: int = limit_query(),
    offset: int = offset_query(),
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[EvalDatasetItemResponse]:
    """List a dataset's golden items (oldest first), paginated. tenant_admin.

    The parent dataset is resolved under the caller's tenant RLS scope first
    (404 for another tenant's).
    """
    require_tenant_id(principal)
    await _get_dataset_or_404(session, dataset_id, principal)
    stmt = (
        select(EvalDatasetItem)
        .where(
            EvalDatasetItem.dataset_id == dataset_id,
            EvalDatasetItem.deleted_at.is_(None),
        )
        .order_by(EvalDatasetItem.created_at, EvalDatasetItem.id)
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    return [EvalDatasetItemResponse.model_validate(i) for i in result.scalars().all()]


@router.post(
    "/eval-datasets/{dataset_id}/items",
    response_model=EvalDatasetItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_eval_dataset_item(
    dataset_id: UUID,
    payload: EvalDatasetItemCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> EvalDatasetItemResponse:
    """Add a hand-authored golden item to a dataset. tenant_admin.

    A hand-authored item has no ``source_task_id``; promotion (task_14_02) is
    the provenance-carrying path. The item inherits the dataset's ``tenant_id``.
    """
    require_tenant_id(principal)
    dataset = await _get_dataset_or_404(session, dataset_id, principal)
    item = EvalDatasetItem(
        id=uuid7(),
        tenant_id=dataset.tenant_id,
        dataset_id=dataset.id,
        input=payload.input,
        expected_output=payload.expected_output,
        reference_metadata=payload.reference_metadata,
    )
    session.add(item)
    await session.flush()
    await session.refresh(item)
    return EvalDatasetItemResponse.model_validate(item)


@router.get("/eval-dataset-items/{item_id}", response_model=EvalDatasetItemResponse)
async def get_eval_dataset_item(
    item_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> EvalDatasetItemResponse:
    """Get one golden item by id. tenant_admin. 404 for another tenant's."""
    require_tenant_id(principal)
    item = await get_writable_or_404(
        session, EvalDatasetItem, item_id, principal, not_found_detail="item not found"
    )
    return EvalDatasetItemResponse.model_validate(item)


@router.put("/eval-dataset-items/{item_id}", response_model=EvalDatasetItemResponse)
async def update_eval_dataset_item(
    item_id: UUID,
    payload: EvalDatasetItemUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> EvalDatasetItemResponse:
    """Partial-update a golden item. tenant_admin. 404 for another tenant's."""
    require_tenant_id(principal)
    item = await get_writable_or_404(
        session, EvalDatasetItem, item_id, principal, not_found_detail="item not found"
    )
    # ``input`` / ``reference_metadata`` are NOT NULL; a missing key leaves them
    # untouched. Only ``expected_output`` is nullable, so a partial update never
    # NULLs the JSONB columns — assign the sent fields explicitly.
    changes = payload.model_dump(exclude_unset=True)
    if "input" in changes and changes["input"] is not None:
        item.input = changes["input"]
    if "reference_metadata" in changes and changes["reference_metadata"] is not None:
        item.reference_metadata = changes["reference_metadata"]
    if "expected_output" in changes:
        item.expected_output = changes["expected_output"]
    await session.flush()
    await session.refresh(item)
    return EvalDatasetItemResponse.model_validate(item)


@router.delete("/eval-dataset-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_eval_dataset_item(
    item_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Soft-delete a golden item. tenant_admin. 404 for another tenant's."""
    require_tenant_id(principal)
    item = await get_writable_or_404(
        session, EvalDatasetItem, item_id, principal, not_found_detail="item not found"
    )
    await soft_delete(session, item)


# ---------------------------------------------------------------------------
# Eval runs — read view exposing the standard metrics (task_14_05)
# ---------------------------------------------------------------------------
@router.get("/eval-runs/{run_id}", response_model=EvalRunResponse)
async def get_eval_run(
    run_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> EvalRunResponse:
    """Get one eval run with its standard metrics. tenant_admin. 404 cross-tenant.

    Exposes the denormalised roll-up populated when the run completed
    (task_14_05): ``pass_rate`` / ``mean_latency_ms`` / ``mean_tokens`` /
    ``mean_cost_usd`` scalar columns plus the ``aggregate_metrics`` JSONB
    carrying p50/p95 latency + the per-metric counts. Runs are NOT
    soft-deleted (immutable measurement records), so the lookup is not
    soft-delete-aware; the ``tenant_id`` filter is the per-tenant guard — a run
    of another tenant 404s and metrics only ever reflect the caller's runs.
    """
    require_tenant_id(principal)
    run = await get_writable_or_404(
        session,
        EvalRun,
        run_id,
        principal,
        not_found_detail="run not found",
        soft_delete_aware=False,
    )
    return EvalRunResponse.model_validate(run)


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
