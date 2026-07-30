"""`/approvals/*` — the human-approval queue (task_02_25 / task_02_26).

`GET /approvals` is the in-app notification feed: every pending
approval request the reviewer must act on. `POST /approvals/{id}/resolve`
approves or rejects one. Both are tenant-scoped through the RLS session.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_redis,
    get_tenant_session,
    require_tenant_member,
)
from api_server.db.approval_repo import (
    get_approval_request,
    list_pending_approvals,
    resolve_approval,
)
from api_server.db.domain import ApprovalRequestStatus, Task, TaskStatus
from api_server.events import publish_task_status_changed
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
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    redis: Redis = Depends(get_redis),
) -> ApprovalRequestResponse:
    """Approve or reject a pending request (ADR 0020).

    Approve -> task back to `backlog`; reject -> task to `blocked`.
    `resolve_approval` does the DB moves; here we publish the task
    transition so the board reacts in real time.

    El 409 lo decide la ESCRITURA, no una lectura previa (prod-03
    task_prod03_04). La comprobación de abajo sigue estando porque da el mensaje
    concreto —«already approved»— y ahorra el UPDATE en el caso normal, pero ya
    NO es la guarda: dos revisores simultáneos la pasaban los dos. La guarda es
    el `UPDATE ... WHERE status='pending'` de `resolve_approval`; cuando afecta
    0 filas devuelve ``None`` y ese es el 409 honesto.
    """
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
    if resolved is None:
        # Otro revisor (o el job de caducidad) ganó la transición entre nuestra
        # lectura y nuestra escritura. Nada se ha mutado.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="approval request was already resolved by someone else",
        )
    # Tell the board about the task transition (best-effort).
    new_status = TaskStatus.BACKLOG if payload.approved else TaskStatus.BLOCKED
    task = await session.get(Task, resolved.task_id)
    if task is not None:
        await publish_task_status_changed(
            redis,
            task,
            old_status=TaskStatus.AWAITING_HUMAN_APPROVAL,
            new_status=new_status,
        )
    return to_approval_response(resolved)
