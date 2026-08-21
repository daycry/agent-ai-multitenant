"""La superficie de System Admin del marketplace (`/admin/marketplace`).

**Este modulo es el unico dueno de `admin_router`, y eso no es cosmetico.**
`main._is_admin_surface` decide si un router lleva
`require_hardened_system_admin` mirando si TODOS sus caminos cuelgan de
`/admin`, y **lanza** ante un router que mezcle. Manteniendo las seis rutas
administrativas en un modulo aparte, la particion es visible en el arbol de
ficheros y no depende de que nadie se acuerde.

Corren sobre la sesion BYPASSRLS (`get_admin_session`): la cola de revision
y el enumerado de shares necesitan ver TODOS los tenants.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    require_system_admin,
)
from api_server.db.marketplace import (
    ListingReviewStatus,
    MarketplaceListing,
    MarketplaceListingVersion,
    MarketplaceShare,
)
from api_server.marketplace.review import (
    ReviewTransitionError,
    approve_listing,
    promote_listing,
    reject_listing,
)
from api_server.routers._pagination import (
    apply_pagination,
    limit_query,
    offset_query,
)
from api_server.routers.marketplace.common import (
    _actor,
)
from api_server.schemas.marketplace import (
    ListingApproveRequest,
    ListingPromoteRequest,
    ListingRejectRequest,
    ListingVersionResponse,
    MarketplaceListingResponse,
    MarketplaceShareResponse,
    to_listing_response,
    to_share_response,
    to_version_response,
)

# System-Admin cross-tenant audit surface. Mounted separately from the
# tenant-scoped ``/marketplace`` router so it runs on the BYPASSRLS admin
# session and is gated to the System Admin — it enumerates EVERY tenant's
# shares for audit (the platform-wide oversight the plan requires).
admin_router = APIRouter(prefix="/admin/marketplace", tags=["admin", "marketplace"])


# ===========================================================================
# GET /admin/marketplace/shares — System Admin enumerates ALL shares (audit)
# ===========================================================================
@admin_router.get("/shares", response_model=list[MarketplaceShareResponse])
async def admin_list_all_shares(
    include_revoked: bool = Query(
        default=True,
        description=(
            "Include revoked shares (default true — the audit view wants the "
            "full history). Set false to see only live grants."
        ),
    ),
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[MarketplaceShareResponse]:
    """Enumerate EVERY tenant's cross-tenant share grants — System Admin audit.

    Runs on the BYPASSRLS admin session, so it sees all shares across all
    tenants (the platform oversight the plan requires: every share is visible
    to the System Admin). Includes revoked grants by default so the audit view
    has the full history. Gated to ``require_system_admin``.
    """
    stmt = select(MarketplaceShare)
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
# La cola de revisión — System Admin (ADR 0142 D6, task_mkt2_09/10)
# ===========================================================================
async def _load_listing_for_review(session: AsyncSession, listing_id: UUID) -> MarketplaceListing:
    """El listing por id **sin filtro de visibilidad**: es lo que se revisa.

    Corre en la sesión BYPASSRLS del admin, así que ve los listings privados de
    cualquier tenant. Es exactamente el privilegio que la revisión necesita y la
    razón de que estas rutas estén gated a `require_system_admin`.
    """
    listing = (
        await session.execute(
            select(MarketplaceListing).where(
                MarketplaceListing.id == listing_id,
                MarketplaceListing.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if listing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="listing not found")
    return listing


@admin_router.get("/review-queue", response_model=list[MarketplaceListingResponse])
async def admin_review_queue(
    review_status: ListingReviewStatus | None = Query(
        default=ListingReviewStatus.PENDING_REVIEW,
        description=(
            "Estado a listar. Por defecto `pending_review` (la cola de trabajo). "
            "Pasa otro valor para auditar lo rechazado o lo ya publicado."
        ),
    ),
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[MarketplaceListingResponse]:
    """La cola de revisión: todo lo que espera ojos, de todos los tenants.

    Sobre la sesión BYPASSRLS porque revisar es, por definición, mirar lo de
    otro: un listing en `pending_review` es invisible para cualquier sesión de
    tenant que no sea la de su autor (esa es la mitad de D6 que vive en
    `catalog_visibility_clause`).
    """
    stmt = select(MarketplaceListing).where(MarketplaceListing.deleted_at.is_(None))
    if review_status is not None:
        stmt = stmt.where(MarketplaceListing.review_status == review_status.value)
    # Más viejo primero: una cola que se ordena por lo más reciente es una cola
    # en la que lo de abajo no se revisa nunca.
    stmt = stmt.order_by(MarketplaceListing.created_at, MarketplaceListing.id)
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    return [to_listing_response(listing) for listing in result.scalars().all()]


@admin_router.get("/listings/{listing_id}/versions", response_model=list[ListingVersionResponse])
async def admin_listing_versions(
    listing_id: UUID,
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[ListingVersionResponse]:
    """El histórico de versiones de un listing — lo que el revisor compara.

    Sin él la cola enseña el manifest de la versión candidata y nada con qué
    contrastarlo, que es revisar a ciegas.
    """
    await _load_listing_for_review(session, listing_id)
    rows = (
        (
            await session.execute(
                select(MarketplaceListingVersion)
                .where(MarketplaceListingVersion.listing_id == listing_id)
                .order_by(
                    MarketplaceListingVersion.created_at.desc(),
                    MarketplaceListingVersion.id,
                )
            )
        )
        .scalars()
        .all()
    )
    return [to_version_response(row) for row in rows]


@admin_router.post("/listings/{listing_id}/approve", response_model=MarketplaceListingResponse)
async def admin_approve_listing(
    listing_id: UUID,
    payload: ListingApproveRequest,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> MarketplaceListingResponse:
    """Aprueba un listing en revisión y lo mete en el catálogo."""
    listing = await _load_listing_for_review(session, listing_id)
    try:
        approve_listing(
            session,
            listing=listing,
            actor=_actor(principal),
            actor_user_id=principal.user_id,
            promote=payload.promote,
        )
    except ReviewTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    # El sello de la revisión viaja también a la fila de versión: quien mire el
    # histórico dentro de un año quiere saber quién aprobó ESA versión, no solo
    # el estado en que quedó el listing.
    await _stamp_reviewed_version(session, listing=listing, reviewer=principal.user_id)
    await session.flush()
    await session.refresh(listing)
    return to_listing_response(listing)


@admin_router.post("/listings/{listing_id}/reject", response_model=MarketplaceListingResponse)
async def admin_reject_listing(
    listing_id: UUID,
    payload: ListingRejectRequest,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> MarketplaceListingResponse:
    """Rechaza con motivo escrito. Sin motivo, 422 en la frontera."""
    listing = await _load_listing_for_review(session, listing_id)
    try:
        reject_listing(
            session,
            listing=listing,
            actor=_actor(principal),
            actor_user_id=principal.user_id,
            reason=payload.reason,
        )
    except ReviewTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await _stamp_reviewed_version(session, listing=listing, reviewer=principal.user_id)
    await session.flush()
    await session.refresh(listing)
    return to_listing_response(listing)


@admin_router.post("/listings/{listing_id}/promote", response_model=MarketplaceListingResponse)
async def admin_promote_listing(
    listing_id: UUID,
    payload: ListingPromoteRequest,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> MarketplaceListingResponse:
    """Sube (o baja) el nivel de confianza de un listing ya publicado."""
    listing = await _load_listing_for_review(session, listing_id)
    try:
        promote_listing(
            session,
            listing=listing,
            actor=_actor(principal),
            actor_user_id=principal.user_id,
            trust_level=payload.trust_level,
        )
    except ReviewTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.flush()
    await session.refresh(listing)
    return to_listing_response(listing)


async def _stamp_reviewed_version(
    session: AsyncSession, *, listing: MarketplaceListing, reviewer: UUID | None
) -> None:
    """Marca la fila de versión vigente como revisada por `reviewer`.

    Silenciosa si no hay fila (un listing anterior al histórico): el veredicto
    ya quedó en el listing y en la auditoría, y fabricar aquí una fila de
    versión inventaría un histórico que nadie publicó.
    """
    row = (
        await session.execute(
            select(MarketplaceListingVersion).where(
                MarketplaceListingVersion.listing_id == listing.id,
                MarketplaceListingVersion.version == listing.version,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return
    row.reviewed_by = reviewer
    row.reviewed_at = listing.reviewed_at
