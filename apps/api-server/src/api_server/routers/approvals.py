"""`/approvals/*` — the human-approval queue (task_02_25 / task_02_26).

`GET /approvals` is the in-app notification feed: every pending
approval request the reviewer must act on. `POST /approvals/{id}/resolve`
approves or rejects one. Both are tenant-scoped through the RLS session.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_principal, get_tenant_session
from api_server.db.approval_repo import (
    get_approval_request,
    list_pending_approvals,
    resolve_approval,
)
from api_server.db.domain import ApprovalRequestStatus
from api_server.schemas.approvals import (
    ApprovalRequestResponse,
    ApprovalResolveRequest,
    to_approval_response,
)

router = APIRouter(tags=["approvals"])


@router.get("/approvals", response_model=list[ApprovalRequestResponse])
async def list_approvals(
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ApprovalRequestResponse]:
    """Pending approval requests, oldest first — the notification feed."""
    return [to_approval_response(r) for r in await list_pending_approvals(session)]


@router.post("/approvals/{request_id}/resolve", response_model=ApprovalRequestResponse)
async def resolve_approval_request(
    request_id: UUID,
    payload: ApprovalResolveRequest,
    principal: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> ApprovalRequestResponse:
    """Approve or reject a pending request and resume its execution."""
    request = await get_approval_request(session, request_id)
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="approval request not found"
        )
    if request.status != ApprovalRequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"approval request already {request.status}",
        )
    resolved = await resolve_approval(
        session,
        request,
        approved=payload.approved,
        resolver_id=principal.user_id,
        reason=payload.reason,
    )
    return to_approval_response(resolved)
