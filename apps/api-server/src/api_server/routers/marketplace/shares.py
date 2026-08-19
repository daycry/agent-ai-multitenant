"""Compartir un listing privado propio con otro tenant.

Un share es la unica via por la que una fila de un tenant se hace visible a
otro, asi que las tres rutas son de tenant-admin y dejan auditoria. La vista
cross-tenant para el System Admin NO esta aqui: vive en :mod:`.admin`,
porque va en el otro router y sobre la sesion BYPASSRLS.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
)
from api_server.db.marketplace import (
    MarketplaceAuditAction,
    MarketplaceAuditEntry,
    MarketplaceShare,
)
from api_server.routers._helpers import require_tenant_id
from api_server.routers._pagination import (
    apply_pagination,
    limit_query,
    offset_query,
)
from api_server.routers.marketplace.common import (
    _actor,
    _load_private_listing,
)
from api_server.schemas.marketplace import (
    MarketplaceShareResponse,
    ShareCreateRequest,
    to_share_response,
)

router = APIRouter()


# ===========================================================================
# POST /marketplace/shares — share an OWN private listing with a target tenant
# ===========================================================================
@router.post(
    "/shares",
    response_model=MarketplaceShareResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_share(
    payload: ShareCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> MarketplaceShareResponse:
    """Opt in to share one of the caller tenant's PRIVATE listings with a
    target tenant (Plan 09 task_09_17).

    The grant is explicit and audited — never an implicit RLS bypass. The
    caller names its OWN private ``listing_id`` (resolved under RLS via
    :func:`_load_private_listing`, so a global listing or another tenant's
    private listing is a clean 404) and a single ``target_tenant_id``. We
    refuse to share with the caller's own tenant (a no-op) and require the
    target to be a real, distinct organization. On success a
    ``marketplace_shares`` row is created stamped with ``owner_tenant_id`` =
    the caller (RLS WITH CHECK rejects a forged owner) and a ``share`` audit
    entry is written in the same transaction.

    After this, the target tenant sees/installs the listing ONLY through the
    grant (the ``marketplace_listings_shared_read`` RLS policy); non-target
    tenants still see nothing. A duplicate LIVE share of the same listing to
    the same target is a 409.

    RBAC: ``tenant_admin``. RLS scopes the listing + the share write to the
    caller's tenant.
    """
    owner_tenant_id = require_tenant_id(principal)

    if payload.target_tenant_id == owner_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="cannot share a listing with your own tenant",
        )

    # Only the caller's OWN private listing may be shared. A global catalog
    # listing is already visible to every tenant (nothing to share); another
    # tenant's private listing is RLS-invisible -> 404.
    listing = await _load_private_listing(session, payload.listing_id, owner_tenant_id)

    # The target tenant existence is enforced by the ``target_tenant_id`` FK to
    # organizations: a non-existent tenant fails the INSERT and surfaces as the
    # 409 below (we never leak which tenant ids exist).
    share = MarketplaceShare(
        listing_id=listing.id,
        owner_tenant_id=owner_tenant_id,
        target_tenant_id=payload.target_tenant_id,
        granted_by=principal.user_id,
    )
    session.add(share)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        # Either the (listing, target) live-share unique index fired (already
        # shared) or the target_tenant_id FK to organizations failed (no such
        # tenant). Both surface as a clean 409 — we never leak which.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "could not create share: the listing is already shared with "
                "that tenant, or the target tenant does not exist"
            ),
        ) from exc

    # Append-only platform audit: who shared which listing with whom.
    session.add(
        MarketplaceAuditEntry(
            tenant_id=owner_tenant_id,
            actor=_actor(principal),
            action=MarketplaceAuditAction.SHARE.value,
            listing_id=listing.id,
            installation_id=None,
            detail={
                "event": "cross_tenant_share",
                "share_id": str(share.id),
                "target_tenant_id": str(payload.target_tenant_id),
                "listing_name": listing.name,
                "listing_version": listing.version,
            },
        )
    )
    await session.flush()
    await session.refresh(share)
    return to_share_response(share)


# ===========================================================================
# GET /marketplace/shares — list the grants the caller tenant created
# ===========================================================================
@router.get("/shares", response_model=list[MarketplaceShareResponse])
async def list_shares(
    include_revoked: bool = Query(
        default=False,
        description=(
            "Include revoked (soft-deleted) shares. Off by default so the "
            "owner sees only its live grants."
        ),
    ),
    limit: int = limit_query(),
    offset: int = offset_query(),
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[MarketplaceShareResponse]:
    """List the cross-tenant share grants the caller tenant OWNS.

    RLS (``marketplace_shares_owner_manage``) scopes the result to grants the
    caller tenant created; another tenant's grants are never returned. Revoked
    grants are excluded unless ``include_revoked=true``. RBAC: ``tenant_admin``.
    """
    stmt = select(MarketplaceShare).where(
        MarketplaceShare.owner_tenant_id == require_tenant_id(principal),
    )
    if not include_revoked:
        stmt = stmt.where(
            MarketplaceShare.deleted_at.is_(None),
            MarketplaceShare.revoked_at.is_(None),
        )
    stmt = stmt.order_by(MarketplaceShare.created_at, MarketplaceShare.id)
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    return [to_share_response(s) for s in result.scalars().all()]


# ===========================================================================
# DELETE /marketplace/shares/{id} — revoke a share grant
# ===========================================================================
@router.delete("/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share(
    share_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Revoke a cross-tenant share grant (opt-out + audited).

    Flips the grant to revoked (``revoked_at`` / ``revoked_by`` + soft-delete)
    so the ``marketplace_listings_shared_read`` policy no longer exposes the
    listing to the target tenant — visibility is removed immediately — and the
    live-share slot frees up for a future re-share. RLS scopes the lookup to
    the OWNER tenant's grants, so another tenant's share (and an
    already-revoked one) surfaces as a clean 404. The revoke and its mandatory
    ``share`` audit entry share one transaction.

    RBAC: ``tenant_admin`` (the owner tenant only).
    """
    owner_tenant_id = require_tenant_id(principal)
    result = await session.execute(
        select(MarketplaceShare).where(
            MarketplaceShare.id == share_id,
            MarketplaceShare.owner_tenant_id == owner_tenant_id,
            MarketplaceShare.deleted_at.is_(None),
            MarketplaceShare.revoked_at.is_(None),
        )
    )
    share = result.scalar_one_or_none()
    if share is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="share not found")

    now = datetime.now(tz=UTC)
    share.revoked_at = now
    share.revoked_by = principal.user_id
    share.deleted_at = now

    session.add(
        MarketplaceAuditEntry(
            tenant_id=owner_tenant_id,
            actor=_actor(principal),
            action=MarketplaceAuditAction.SHARE.value,
            listing_id=share.listing_id,
            installation_id=None,
            detail={
                "event": "cross_tenant_share_revoke",
                "share_id": str(share.id),
                "target_tenant_id": str(share.target_tenant_id),
            },
        )
    )
    await session.flush()
