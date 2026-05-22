"""`/executions/*` — read access to agent loop executions (Plan 02 Fase E).

The Timeline UI loads one execution and renders its `steps_log`. Reads
go through the tenant-scoped session, so RLS keeps an execution visible
only to its own tenant.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import get_tenant_session
from api_server.db.execution_repo import get_execution
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
