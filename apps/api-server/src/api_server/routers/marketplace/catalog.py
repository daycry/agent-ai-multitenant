"""Navegar el catalogo: `GET /marketplace/listings` y el detalle.

Lo que ve el llamante es el catalogo GLOBAL (`tenant_id IS NULL`, por la
policy `marketplace_listings_global_read`) mas sus propios listings
privados. RLS hace el filtro; estas dos rutas solo paginan y ordenan.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_member,
)
from api_server.db.marketplace import (
    MarketplaceListing,
    MarketplaceListingKind,
    MarketplaceTrustLevel,
)
from api_server.marketplace.review import (
    catalog_visibility_clause,
)
from api_server.routers._pagination import (
    apply_pagination,
    limit_query,
    offset_query,
)
from api_server.schemas.marketplace import (
    MarketplaceListingResponse,
    to_listing_response,
)

router = APIRouter()


# ===========================================================================
# GET /marketplace/listings — browse the catalog
# ===========================================================================
@router.get("/listings", response_model=list[MarketplaceListingResponse])
async def list_listings(
    kind: MarketplaceListingKind | None = Query(
        default=None,
        description="Filter by listing kind (skill / tool / mcp_server). 422 on an unknown value.",
    ),
    trust_level: MarketplaceTrustLevel | None = Query(
        default=None,
        description=(
            "Filter by trust tier (verified / community / experimental). 422 on an unknown value."
        ),
    ),
    limit: int = limit_query(),
    offset: int = offset_query(),
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[MarketplaceListingResponse]:
    """Browse the marketplace catalog.

    RLS exposes the global catalog (``tenant_id IS NULL``) plus the
    caller's own private listings; another tenant's private listings are
    never returned. Deterministic ordering (``created_at, id``) so
    ``offset`` paging is stable.

    On top of RLS, ADR 0142 D6 filters by review state: only ``published``
    listings are catalog entries. A listing still in the queue (or rejected) is
    visible ONLY to the tenant that authored it.
    """
    stmt = select(MarketplaceListing).where(
        MarketplaceListing.deleted_at.is_(None),
        # ADR 0142 D6: la RLS ya decidió qué filas EXISTEN para esta sesión;
        # esto quita de ahí lo que todavía no ha pasado revisión. Lo propio se
        # sigue viendo en cualquier estado (el autor necesita leer su rechazo).
        catalog_visibility_clause(principal.tenant_id),
    )
    if kind is not None:
        stmt = stmt.where(MarketplaceListing.kind == kind.value)
    if trust_level is not None:
        stmt = stmt.where(MarketplaceListing.trust_level == trust_level.value)
    stmt = stmt.order_by(MarketplaceListing.created_at, MarketplaceListing.id)
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    return [to_listing_response(listing) for listing in result.scalars().all()]


# ===========================================================================
# GET /marketplace/listings/{id} — listing detail
# ===========================================================================
@router.get("/listings/{listing_id}", response_model=MarketplaceListingResponse)
async def get_listing(
    listing_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> MarketplaceListingResponse:
    """Fetch a single listing (global or the caller's own private one).

    Another tenant's private listing surfaces as 404 (RLS filters it
    out) so we never leak that its id exists. A listing that has not been
    approved yet (ADR 0142 D6) is a 404 too for anyone but its author — same
    reasoning: a 403 would confirm that the id exists.
    """
    result = await session.execute(
        select(MarketplaceListing).where(
            MarketplaceListing.id == listing_id,
            MarketplaceListing.deleted_at.is_(None),
            catalog_visibility_clause(principal.tenant_id),
        )
    )
    listing = result.scalar_one_or_none()
    if listing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="listing not found")
    return to_listing_response(listing)
