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

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_admin
from api_server.db.domain import Execution, ExecutionStatus, Task, TaskStatus
from api_server.db.evals import EvalCriterion, EvalDataset, EvalDatasetItem, EvalResult, EvalRun
from api_server.evals.constants import MAX_SYNC_EVAL_CALLS as _MAX_SYNC_EVAL_CALLS
from api_server.evals.diff import DatasetMismatchError, RunDiff, diff_runs
from api_server.evals.judge import JudgeResponseError, SameModelJudgeError, run_eval
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
    EvalResultResponse,
    EvalRunDiffResponse,
    EvalRunResponse,
    ItemChangeResponse,
    MetricDeltaResponse,
    PromoteToDatasetRequest,
    PromoteToDatasetResponse,
)

router = APIRouter(tags=["evals"])

# Techo de llamadas a modelo de UNA corrida síncrona. Vive en
# `api_server.evals.constants` desde `task_gov_05`: el gate de edición de prompt
# corre el mismo tipo de corrida dentro de un `PUT`, y dos techos distintos para
# la misma limitación acabarían divergiendo. Se reexporta con este nombre porque
# `tests/integration/test_eval_run_endpoint.py` lo monkeypatchea aquí.
MAX_SYNC_EVAL_CALLS = _MAX_SYNC_EVAL_CALLS


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
# Eval-run diff — compare two runs of the same dataset (task_14_06)
#
# Declared BEFORE the ``/eval-runs/{run_id}`` route so the static ``/diff``
# path is matched first (otherwise "diff" would be parsed as a run_id and 422).
# ---------------------------------------------------------------------------
@router.get("/eval-runs/diff", response_model=EvalRunDiffResponse)
async def diff_eval_runs(
    base: UUID,
    candidate: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> EvalRunDiffResponse:
    """Diff two eval runs of the SAME dataset (base vs candidate). tenant_admin.

    The canonical use is an OLD prompt version (``base``) vs a NEW one
    (``candidate``) over the same golden dataset: per-metric deltas
    (pass_rate / latency / cost / tokens), the items that regressed
    (pass->fail) or improved (fail->pass), and an overall
    ``regressed`` / ``improved`` / ``unchanged`` verdict that feeds the
    Phase C merge-gate.

    Multi-tenancy: BOTH runs are resolved under the caller's tenant RLS scope
    (404 for another tenant's run), so a diff only ever spans the caller's own
    runs. A run of a different dataset is rejected (422) — a cross-dataset diff
    is meaningless. The comparison itself is a pure function (no provider, no
    cross-tenant read).
    """
    require_tenant_id(principal)
    base_run = await get_writable_or_404(
        session,
        EvalRun,
        base,
        principal,
        not_found_detail="run not found",
        soft_delete_aware=False,
    )
    candidate_run = await get_writable_or_404(
        session,
        EvalRun,
        candidate,
        principal,
        not_found_detail="run not found",
        soft_delete_aware=False,
    )
    base_results = await _load_run_results(session, base_run.id)
    candidate_results = await _load_run_results(session, candidate_run.id)

    try:
        diff = diff_runs(
            base_run,
            candidate_run,
            base_results,
            candidate_results,
            pass_rate_regression_threshold=Decimal("0"),
        )
    except DatasetMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    return _diff_to_response(
        diff,
        base_run_id=base_run.id,
        candidate_run_id=candidate_run.id,
        dataset_id=base_run.dataset_id,
    )


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


@router.get("/eval-runs/{run_id}/results", response_model=list[EvalResultResponse])
async def list_eval_run_results(
    run_id: UUID,
    limit: int = limit_query(),
    offset: int = offset_query(),
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[EvalResultResponse]:
    """El desglose por item de una corrida. tenant_admin. 404 cross-tenant.

    `task_wf_52b`: las filas `eval_results` se escribían desde el Plan 14 y
    ninguna ruta las leía. La corrida publicaba su `pass_rate` y **no había
    forma de ver qué item falló ni por qué** — un 60 % sin desglose dice que
    algo va mal y no deja arreglarlo.

    Se resuelve la corrida primero (404 si es de otro tenant) para no filtrar
    por la puerta de atrás la existencia de un run ajeno con una lista vacía.
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
    # Los fallos primero: es lo que se viene a mirar cuando una corrida baja de
    # nota, y en un dataset grande evita paginar buscándolos.
    stmt = (
        select(EvalResult)
        .where(EvalResult.run_id == run.id)
        .order_by(EvalResult.overall_score.asc().nullsfirst(), EvalResult.id)
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    rows = list((await session.execute(stmt)).scalars().all())
    names = await _criterion_names(session, run.dataset_id)
    return [_result_to_response(row, names) for row in rows]


async def _criterion_names(session: AsyncSession, dataset_id: UUID) -> dict[str, str]:
    """`criterion_id -> nombre` de los criterios del dataset."""
    rows = (
        await session.execute(
            select(EvalCriterion.id, EvalCriterion.name).where(
                EvalCriterion.dataset_id == dataset_id
            )
        )
    ).all()
    return {str(cid): name for cid, name in rows}


def _result_to_response(result: EvalResult, names: dict[str, str]) -> EvalResultResponse:
    """El resultado con el NOMBRE de cada criterio resuelto.

    `CriterionScore.to_json()` persiste `criterion_id`, no el nombre — correcto
    para la fila (el nombre puede cambiar y el histórico no debe mentir), pero
    ilegible en pantalla: sin esto el desglose sería una lista de filas
    idénticas tituladas con un UUID, y saber QUÉ criterio falló es justo para lo
    que se abre.
    """
    scores: list[Any] = []
    for entry in result.criterion_scores or []:
        if not isinstance(entry, dict):
            continue
        resolved = dict(entry)
        # Un criterio borrado del dataset después de la corrida ya no tiene
        # nombre que resolver; se dice así en vez de fingir uno.
        resolved["name"] = names.get(str(entry.get("criterion_id")), "(criterio retirado)")
        scores.append(resolved)
    response = EvalResultResponse.model_validate(result)
    return response.model_copy(update={"criterion_scores": scores})


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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
async def _load_run_results(session: AsyncSession, run_id: UUID) -> list[EvalResult]:
    """All result rows of a run under the caller's RLS scope (ordered by item).

    Tenant-scoped by the session RLS + the run already resolved under the
    caller's tenant; deterministic order so the diff is reproducible.
    """
    result = await session.execute(
        select(EvalResult)
        .where(EvalResult.run_id == run_id)
        .order_by(EvalResult.created_at, EvalResult.id)
    )
    return list(result.scalars().all())


def _delta_to_response(d: object) -> MetricDeltaResponse:
    return MetricDeltaResponse.model_validate(d)


def _diff_to_response(
    diff: RunDiff,
    *,
    base_run_id: UUID,
    candidate_run_id: UUID,
    dataset_id: UUID,
) -> EvalRunDiffResponse:
    return EvalRunDiffResponse(
        base_run_id=base_run_id,
        candidate_run_id=candidate_run_id,
        dataset_id=dataset_id,
        verdict=diff.verdict.value,
        pass_rate=_delta_to_response(diff.pass_rate),
        mean_latency_ms=_delta_to_response(diff.mean_latency_ms),
        mean_cost_usd=_delta_to_response(diff.mean_cost_usd),
        mean_tokens=_delta_to_response(diff.mean_tokens),
        regressions=[ItemChangeResponse.model_validate(c) for c in diff.regressions],
        improvements=[ItemChangeResponse.model_validate(c) for c in diff.improvements],
        pass_rate_regression_threshold=diff.pass_rate_regression_threshold,
    )


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


# ---------------------------------------------------------------------------
# Lanzar una corrida — el PRODUCTOR que faltaba (`task_wf_52b`)
# ---------------------------------------------------------------------------
class EvalRunCreateRequest(BaseModel):
    dataset_id: UUID
    # Modelo del SUJETO evaluado. Debe diferir del juez: un modelo juzgándose a
    # sí mismo se aprueba, y el motor lo rechaza con `SameModelJudgeError`.
    subject_model: str
    judge_model: str
    # Etiqueta del conjunto de prompts que produjo los outputs bajo juicio
    # (`task_wf_52`). Sin ella el dashboard agrupa bajo «(sin versión)» y la
    # corrida no se puede atribuir a un cambio, que es para lo que se mide.
    subject_prompt_version: str | None = None
    subject_agent_id: UUID | None = None


@router.post(
    "/eval-runs",
    response_model=EvalRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_eval_run(
    payload: EvalRunCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> EvalRunResponse:
    """Lanza una corrida contra un dataset dorado. tenant_admin.

    `task_wf_52b`: el subsistema de evals estaba construido entero —módulos,
    tablas, endpoints, dashboard— y **sus tablas vacías, porque no había
    ninguna vía de producirlas**. El router tenía CRUD de las entradas y solo
    lectura de las salidas: faltaba exactamente esto.

    Juez y sujeto son LLM reales de la capa de proveedores (`shared_llm`,
    ADR 0021), no dobles de test. El **sujeto produce** una salida para cada
    item y el **juez** la compara contra el `expected_output` del item —la
    referencia, que salió de un run REAL ya aprobado vía
    `POST /tasks/{id}/promote-to-dataset`—. Los items fijan el material, así
    que dos corridas distintas se comparan sobre exactamente lo mismo: es lo
    que permite medir si un cambio de prompt mejoró o empeoró.

    409 si juez y sujeto son el mismo modelo: un modelo juzgándose a sí mismo
    se aprueba.
    """
    require_tenant_id(principal)
    if payload.judge_model == payload.subject_model:
        # `run_eval` también lo comprueba; hacerlo aquí evita abrir proveedor y
        # crear la fila del run para nada.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "same_model_judge",
                "message": (
                    "el juez debe ser un modelo distinto del sujeto "
                    "(un modelo juzgándose a sí mismo se aprueba)"
                ),
            },
        )
    dataset = await get_writable_or_404(
        session,
        EvalDataset,
        payload.dataset_id,
        principal,
        not_found_detail="dataset not found",
    )

    items = list(
        (
            await session.execute(
                select(EvalDatasetItem).where(EvalDatasetItem.dataset_id == dataset.id)
            )
        )
        .scalars()
        .all()
    )
    if not items:
        # Una corrida sobre un dataset vacío daría un `pass_rate` de 100 % sin
        # haber juzgado nada — el peor dato posible: parece perfecto.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "empty_dataset", "message": "el dataset no tiene items que juzgar"},
        )

    # La corrida se ejecuta DENTRO del request, y son `items x (1 sujeto + N
    # criterios)` llamadas a LLM. Pasado cierto tamaño el request muere por
    # timeout a mitad de camino: el operador ve un 504, la transacción se
    # deshace y no queda ni el run ni una explicación. Decirlo por adelantado,
    # con el número concreto, es infinitamente mejor que morir a la mitad.
    criteria_count = int(
        (
            await session.execute(
                select(func.count(EvalCriterion.id)).where(
                    EvalCriterion.dataset_id == dataset.id,
                    EvalCriterion.deleted_at.is_(None),
                )
            )
        ).scalar_one()
    )
    planned_calls = len(items) * (1 + max(criteria_count, 1))
    if planned_calls > MAX_SYNC_EVAL_CALLS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "dataset_too_large",
                "message": (
                    f"esta corrida serían {planned_calls} llamadas a modelo "
                    f"({len(items)} items x {criteria_count} criterios) y se ejecuta dentro de "
                    f"la petición; el máximo son {MAX_SYNC_EVAL_CALLS}. Parte el dataset o "
                    "reduce criterios."
                ),
                "planned_calls": planned_calls,
                "max_calls": MAX_SYNC_EVAL_CALLS,
            },
        )

    # `task_gov_05`: cuando la corrida declara QUÉ agente evalúa, el sujeto corre
    # con el prompt de ese agente. Sin esto la corrida base y la candidata del
    # gate de edición no serían comparables — la base habría corrido sin prompt.
    subject_prompt = await _subject_prompt_of(session, payload.subject_agent_id)
    judge, subject = await _build_eval_seams(
        session,
        payload.judge_model,
        payload.subject_model,
        subject_system_prompt=subject_prompt,
    )
    run = EvalRun(
        id=uuid7(),
        tenant_id=principal.tenant_id,
        dataset_id=dataset.id,
        subject_agent_id=payload.subject_agent_id,
        subject_prompt_version=payload.subject_prompt_version,
    )
    session.add(run)
    await session.flush()

    try:
        # El SUJETO produce; el juez compara contra `expected_output`. Pasar la
        # referencia como salida del sujeto la compararía consigo misma: 100 %
        # de aciertos siempre, midiendo nada. Un eval que siempre pasa es peor
        # que no tenerlo, porque da confianza.
        await run_eval(
            session,
            run,
            judge=judge,
            subject_model=payload.subject_model,
            subject=subject,
        )
    except SameModelJudgeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "same_model_judge", "message": str(exc)},
        ) from exc
    except JudgeResponseError as exc:
        # El juez contestó algo que el motor no sabe puntuar (prosa en vez del
        # JSON del contrato: típico de un modelo pequeño). Sin esto sería un 500
        # mudo y el operador no sabría que el problema es SU elección de juez.
        # La transacción se deshace, así que no queda un run a medias.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "judge_unparseable",
                "message": (
                    f"el modelo juez «{payload.judge_model}» devolvió algo que no se puede "
                    f"puntuar ({exc}). Prueba con un modelo más capaz como juez."
                ),
            },
        ) from exc

    await session.refresh(run)
    return EvalRunResponse.model_validate(run)


async def _subject_prompt_of(session: AsyncSession, agent_id: UUID | None) -> str | None:
    """El prompt EFECTIVO del agente que esta corrida evalúa, o ``None``.

    El efectivo (`agent_persona.effective_prompt_text`) y no el campo plano: es
    el texto que el modelo vería de verdad, ya resuelto es→en y ya capado. Un
    agente que no existe (o de otro tenant, invisible por RLS) devuelve ``None``
    en vez de fallar: la corrida sigue siendo válida, sólo mide sin prompt.
    """
    if agent_id is None:
        return None
    from api_server.agent_persona import effective_prompt_text
    from api_server.db.domain import Agent

    agent = (await session.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    return effective_prompt_text(agent) if agent is not None else None


async def _build_eval_seams(
    session: AsyncSession,
    judge_model: str,
    subject_model: str,
    *,
    subject_system_prompt: str | None = None,
) -> tuple[Any, Any]:
    """El juez y el sujeto, sobre la capa de proveedores del sistema.

    Se resuelve por la MISMA vía que el chat (proveedor activo + credencial de
    Vault): un segundo camino de LLM que mantener es como acaban divergiendo
    las credenciales y el catálogo.

    ``subject_system_prompt`` es el prompt bajo evaluación (`task_gov_05`). Sin
    él el sujeto corría SIN prompt, así que dos corridas de prompts distintos
    salían iguales y medir un cambio de prompt era imposible — ver el docstring
    de :class:`~api_server.evals.llm_judge.LLMSubjectModel`.
    """
    from api_server.chat.responder import _resolve_chat_provider, resolve_chat_model_config
    from api_server.evals.llm_judge import LLMJudgeModel, LLMSubjectModel
    from api_server.routers.llm_providers import get_provider_vault_store

    effective = await resolve_chat_model_config(session, project=None)
    provider, _kind, api_model = await _resolve_chat_provider(
        session, effective, get_provider_vault_store()
    )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "no_llm_provider",
                "message": "no hay proveedor LLM activo para actuar de juez",
            },
        )
    return (
        LLMJudgeModel(provider=provider, model=judge_model or api_model),
        LLMSubjectModel(
            provider=provider,
            model=subject_model or api_model,
            system_prompt=subject_system_prompt,
        ),
    )
