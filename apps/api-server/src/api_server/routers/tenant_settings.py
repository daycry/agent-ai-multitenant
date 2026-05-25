"""`/tenant-settings/...` endpoints (Plan 03 task_03_26).

Per-tenant configuration knobs a `tenant_admin` operator owns. The
first one to land is the hourly rate the human-cost calculator uses
(`compute_human_cost`, Plan 03 task_03_22). NULL on the
``organizations`` row means "use the platform default".

Authorization model:
  - GET: any authenticated user with an active tenant_id in their JWT.
  - PUT: the JWT user must hold `tenant_admin` (or higher) on the
    target tenant, OR be a `is_system_admin`. We resolve the role
    via the `user_org_memberships` table.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_principal,
    get_tenant_session,
)
from api_server.db.models import (
    Organization,
    User,
    UserOrganizationMembership,
    UserRole,
)
from api_server.routers._helpers import require_tenant_id

router = APIRouter(prefix="/tenant-settings", tags=["tenant-settings"])


_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class HourlyRateResponse(BaseModel):
    model_config = _BASE_CONFIG

    hourly_rate: Decimal | None
    hourly_rate_currency: str | None


class HourlyRateUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    hourly_rate: Decimal | None = Field(default=None, ge=0, le=10_000)
    hourly_rate_currency: str | None = Field(default=None, min_length=3, max_length=3)


async def _load_org(session: AsyncSession, tenant_id: Any) -> Organization:
    result = await session.execute(
        select(Organization).where(Organization.id == tenant_id, Organization.deleted_at.is_(None))
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    return org


async def _require_tenant_admin(
    session: AsyncSession, principal: AuthPrincipal, tenant_id: Any
) -> None:
    """The JWT user must be a tenant_admin on that tenant — or a
    platform-level system_admin. Anything else returns 403."""
    if principal.user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    user = await session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if user.is_system_admin:
        return
    membership_q = await session.execute(
        select(UserOrganizationMembership).where(
            UserOrganizationMembership.user_id == principal.user_id,
            UserOrganizationMembership.tenant_id == tenant_id,
            UserOrganizationMembership.is_active.is_(True),
            UserOrganizationMembership.deleted_at.is_(None),
        )
    )
    membership = membership_q.scalar_one_or_none()
    if membership is None or membership.role != UserRole.TENANT_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_admin role required",
        )


@router.get("/hourly-rate", response_model=HourlyRateResponse)
async def get_tenant_hourly_rate(
    principal: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> HourlyRateResponse:
    tenant_id = require_tenant_id(principal)
    org = await _load_org(session, tenant_id)
    return HourlyRateResponse(
        hourly_rate=org.hourly_rate,
        hourly_rate_currency=org.hourly_rate_currency,
    )


@router.put("/hourly-rate", response_model=HourlyRateResponse)
async def update_tenant_hourly_rate(
    payload: HourlyRateUpdateRequest,
    principal: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> HourlyRateResponse:
    tenant_id = require_tenant_id(principal)
    await _require_tenant_admin(session, principal, tenant_id)
    org = await _load_org(session, tenant_id)

    # We accept either field independently. Setting both to None is the
    # "clear the override, fall back to the platform default" operation.
    if payload.hourly_rate is not None:
        org.hourly_rate = payload.hourly_rate
    if payload.hourly_rate_currency is not None:
        org.hourly_rate_currency = payload.hourly_rate_currency.upper()

    await session.flush()
    await session.refresh(org)
    return HourlyRateResponse(
        hourly_rate=org.hourly_rate,
        hourly_rate_currency=org.hourly_rate_currency,
    )


__all__ = ["router"]
