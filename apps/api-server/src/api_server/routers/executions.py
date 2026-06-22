"""`/executions/*` — read access to agent loop executions (Plan 02 Fase E).

The Timeline UI loads one execution and renders its `steps_log`. Reads
go through the tenant-scoped session, so RLS keeps an execution visible
only to its own tenant.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_member
from api_server.celery_client import revoke_execution_job
from api_server.db.execution_repo import (
    ExecutionNotCancellableError,
    get_execution,
    request_execution_cancel,
)
from api_server.schemas.executions import ExecutionResponse, to_execution_response

router = APIRouter(tags=["executions"])


@router.get("/executions/{execution_id}", response_model=ExecutionResponse)
async def read_execution(
    execution_id: UUID,
    session: AsyncSession = Depends(get_tenant_session),
) -> ExecutionResponse:
    """One execution with its full steps_log — the Timeline UI's data source."""
    execution = await get_execution(session, execution_id)
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="execution not found")
    return to_execution_response(execution)


class ExecutionCancelResponse(BaseModel):
    execution_id: UUID
    status: str


@router.post(
    "/executions/{execution_id}/cancel",
    response_model=ExecutionCancelResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_execution(
    execution_id: UUID,
    _principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> ExecutionCancelResponse:
    """Request cooperative cancellation of a *running* execution.

    Stamps ``cancel_requested_at`` (the worker polls it to kill the container and
    finalise the row as ``cancelled``) and revokes the Celery job so a queued/running
    one stops wasting LLM budget. RLS makes a cross-tenant id a 404; an
    already-terminal execution is a 409. Idempotent — a second cancel is a no-op.
    The revoke is best-effort (the DB flag is the source of truth).
    """
    try:
        execution = await request_execution_cancel(session, execution_id)
    except ExecutionNotCancellableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "not_cancellable", "status": exc.status},
        ) from exc
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="execution not found")
    if execution.celery_task_id:
        await revoke_execution_job(execution.celery_task_id)
    return ExecutionCancelResponse(execution_id=execution.id, status="cancel_requested")
