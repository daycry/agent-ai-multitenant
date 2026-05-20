"""`/admin/*` endpoints — System Admin only.

Every route depends on `require_system_admin` (the JWT must carry
`sys: true`) and uses the BYPASSRLS admin engine so it can read
across tenants and write `audit_log` rows with `tenant_id IS NULL`.

Tenant CRUD is the heart of the file; user listing and system-health
are read-only summaries.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.audit import write_audit_log
from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    get_client_ip,
    require_system_admin,
)
from api_server.db.models import AuditAction, Organization, User
from api_server.schemas.admin import (
    ServiceHealth,
    SystemHealthResponse,
    TenantCreateRequest,
    TenantResponse,
    TenantUpdateRequest,
    UserListItem,
)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_tenant_response(o: Organization) -> TenantResponse:
    return TenantResponse(
        id=o.id,
        name=o.name,
        slug=o.slug,
        is_active=o.is_active,
        created_at=o.created_at,
        updated_at=o.updated_at,
        deleted_at=o.deleted_at,
    )


async def _get_tenant_or_404(session: AsyncSession, tenant_id: UUID) -> Organization:
    result = await session.execute(select(Organization).where(Organization.id == tenant_id))
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    return org


# ---------------------------------------------------------------------------
# /admin/tenants — CRUD
# ---------------------------------------------------------------------------
@router.post(
    "/tenants",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant(
    payload: TenantCreateRequest,
    request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> TenantResponse:
    org = Organization(id=uuid7(), name=payload.name, slug=payload.slug)
    session.add(org)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="slug already exists",
        ) from exc

    await write_audit_log(
        session,
        action=AuditAction.TENANT_CREATED.value,
        actor_user_id=principal.user_id,
        tenant_id=org.id,
        resource_type="tenant",
        resource_id=org.id,
        changes={"name": payload.name, "slug": payload.slug},
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    await session.refresh(org)
    return _to_tenant_response(org)


@router.get("/tenants", response_model=list[TenantResponse])
async def list_tenants(
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[TenantResponse]:
    result = await session.execute(
        select(Organization)
        .where(Organization.deleted_at.is_(None))
        .order_by(Organization.created_at)
    )
    return [_to_tenant_response(o) for o in result.scalars().all()]


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: UUID,
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> TenantResponse:
    org = await _get_tenant_or_404(session, tenant_id)
    return _to_tenant_response(org)


@router.put("/tenants/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: UUID,
    payload: TenantUpdateRequest,
    request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> TenantResponse:
    org = await _get_tenant_or_404(session, tenant_id)

    changes: dict[str, object] = {}
    if payload.name is not None and payload.name != org.name:
        changes["name"] = {"from": org.name, "to": payload.name}
        org.name = payload.name
    if payload.is_active is not None and payload.is_active != org.is_active:
        changes["is_active"] = {"from": org.is_active, "to": payload.is_active}
        org.is_active = payload.is_active

    if changes:
        await write_audit_log(
            session,
            action=AuditAction.TENANT_UPDATED.value,
            actor_user_id=principal.user_id,
            tenant_id=org.id,
            resource_type="tenant",
            resource_id=org.id,
            changes=changes,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )

    await session.flush()
    await session.refresh(org)
    return _to_tenant_response(org)


@router.delete("/tenants/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(
    tenant_id: UUID,
    request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> None:
    """Soft-delete: stamps `deleted_at` rather than dropping the row."""
    from datetime import UTC, datetime

    org = await _get_tenant_or_404(session, tenant_id)
    if org.deleted_at is not None:
        return  # already deleted — idempotent

    org.deleted_at = datetime.now(tz=UTC)

    await write_audit_log(
        session,
        action=AuditAction.TENANT_DELETED.value,
        actor_user_id=principal.user_id,
        tenant_id=org.id,
        resource_type="tenant",
        resource_id=org.id,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await session.flush()


# ---------------------------------------------------------------------------
# /admin/users — cross-tenant listing
# ---------------------------------------------------------------------------
@router.get("/users", response_model=list[UserListItem])
async def list_users(
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[UserListItem]:
    """Cross-tenant user listing. `users` itself is un-RLSed, but this
    endpoint is BYPASSRLS-routed for symmetry with the rest of /admin."""
    result = await session.execute(
        select(User).where(User.deleted_at.is_(None)).order_by(User.created_at)
    )
    return [
        UserListItem(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            is_system_admin=u.is_system_admin,
            is_active=u.is_active,
        )
        for u in result.scalars().all()
    ]


# ---------------------------------------------------------------------------
# /admin/system-health — placeholder summary (phase 0)
# ---------------------------------------------------------------------------
@router.get("/system-health", response_model=SystemHealthResponse)
async def system_health(
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> SystemHealthResponse:
    """Lightweight health summary. Phase 0 only checks the DB — the
    watchdog (task_00_16) will expose the full container view."""
    services: list[ServiceHealth] = []
    overall = "ok"

    # PostgreSQL
    try:
        from sqlalchemy import text

        await session.execute(text("SELECT 1"))
        services.append(ServiceHealth(name="postgres", status="ok"))
    except Exception as exc:  # - we want to record any failure
        services.append(ServiceHealth(name="postgres", status="down", detail=str(exc)))
        overall = "degraded"

    return SystemHealthResponse(status=overall, services=services)
