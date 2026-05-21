"""`GET /approval-policies` (task_01_23 substrate).

Read-only catalog of the built-in human-approval policy presets seeded
under PLATFORM_TENANT_ID. RLS exposes them via the
`approval_policy_templates_builtin_read` policy (`is_builtin = true`),
so even sessions without a `tid` claim see them.

Writes (creating tenant-local presets, editing a project's policy) go
through the existing `/projects` PUT path; this router only surfaces
the catalog the picker needs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_principal, get_tenant_session
from api_server.db.domain import ApprovalPolicyTemplate
from api_server.schemas.approval_policies import (
    ApprovalPolicyResponse,
    to_approval_policy_response,
)

router = APIRouter(prefix="/approval-policies", tags=["approval-policies"])


@router.get("", response_model=list[ApprovalPolicyResponse])
async def list_approval_policies(
    builtin_only: bool = Query(default=False, alias="builtin_only"),
    _: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ApprovalPolicyResponse]:
    stmt = select(ApprovalPolicyTemplate).where(ApprovalPolicyTemplate.deleted_at.is_(None))
    if builtin_only:
        stmt = stmt.where(ApprovalPolicyTemplate.is_builtin.is_(True))
    # Stable insertion order so the four built-in presets render as
    # Sandbox → Desarrollo → Producción → Cliente Externo (least
    # restrictive to most restrictive), which is how the seed picks
    # them. created_at survives UPSERTs because only updated_at moves
    # on conflict.
    stmt = stmt.order_by(
        ApprovalPolicyTemplate.is_builtin.desc(),
        ApprovalPolicyTemplate.created_at,
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    return [to_approval_policy_response(r) for r in rows]
