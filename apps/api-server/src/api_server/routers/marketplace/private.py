"""Publicar, editar y retirar el catalogo PRIVADO del propio tenant.

Un listing privado cuelga de un `marketplace_sources` de tipo `private`
propio del tenant, que `_ensure_private_source` crea a demanda. Las tres
rutas son escrituras de tenant-admin.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
)
from api_server.db.marketplace import (
    ListingReviewStatus,
    MarketplaceAuditAction,
    MarketplaceAuditEntry,
    MarketplaceListing,
    MarketplaceListingKind,
    MarketplaceTrustLevel,
)
from api_server.marketplace.listing_versions import (
    snapshot_version,
)
from api_server.marketplace.private_listing import (
    PrivateListingFormatError,
    parse_private_listing,
)
from api_server.marketplace.review import (
    submit_for_review,
)
from api_server.routers._helpers import require_tenant_id
from api_server.routers.marketplace.common import (
    _actor,
    _ensure_private_source,
    _load_private_listing,
)
from api_server.schemas.marketplace import (
    MarketplaceListingResponse,
    PrivateListingPublishRequest,
    PrivateListingUpdateRequest,
    to_listing_response,
)

router = APIRouter()


# ===========================================================================
# POST /marketplace/private/listings — publish an own PRIVATE listing
# ===========================================================================
@router.post(
    "/private/listings",
    response_model=MarketplaceListingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def publish_private_listing(
    payload: PrivateListingPublishRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> MarketplaceListingResponse:
    """Publish the caller tenant's OWN internal skill/tool as a PRIVATE listing.

    The submitted manifest is validated by the Phase C parsers (SKILL.md for
    a ``skill``, the YAML tool manifest for a ``tool`` / ``mcp_server``); a
    malformed manifest is a 422 and NO row is written. On success a
    ``marketplace_listings`` row is created stamped with ``tenant_id`` = the
    caller tenant — a PRIVATE listing. RLS WITH CHECK enforces that the row
    carries the caller's tenant_id, so a tenant can ONLY ever publish into
    its own private scope (never a global/another-tenant row). The listing's
    trust level is the private source's default (``community``), never taken
    from the wire.

    ``name`` / ``version`` come from the parsed manifest; re-publishing the
    same (kind, name, version) is a 409 (the
    ``uq_marketplace_listings_source_tenant_name_version`` constraint) — bump
    the version or use the update endpoint.

    RBAC: ``tenant_admin`` (this repo's tenant-publisher role). RLS scopes the
    write to the caller's tenant.
    """
    tenant_id = require_tenant_id(principal)

    try:
        parsed = parse_private_listing(kind=payload.kind, manifest_text=payload.manifest)
    except PrivateListingFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    source = await _ensure_private_source(session, tenant_id)

    listing = MarketplaceListing(
        source_id=source.id,
        # Stamp the caller tenant -> a PRIVATE listing. RLS WITH CHECK
        # rejects any other tenant_id, so this can never become a global row.
        tenant_id=tenant_id,
        kind=parsed.kind.value,
        name=parsed.name,
        version=parsed.version,
        description=parsed.description,
        author=payload.author,
        # A tenant's own internal listing is community-trust (not
        # platform-verified); never honour a wire-supplied trust level.
        trust_level=MarketplaceTrustLevel.COMMUNITY.value,
        # ADR 0142 D6 — el punto entero de la fase 3: publicar NO publica, deja
        # el listing en la cola del system admin. Explícito y no heredado del
        # `server_default` de la columna, que vale `'published'` para que el
        # catálogo curado por la plataforma no se vacíe (ver migración 0129).
        review_status=ListingReviewStatus.PENDING_REVIEW.value,
        manifest=parsed.manifest,
        requested_permissions=parsed.requested_permissions,
        # Private listings are unsigned — signing is the platform team's
        # prerogative for verified global listings.
        signature=None,
    )
    session.add(listing)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"a private listing {parsed.name!r} version {parsed.version!r} "
                "already exists; bump the version or update it"
            ),
        ) from exc

    # ADR 0142 D7: el histórico nace con la publicación, no con el primer
    # despliegue. Es lo que hace que un rollback tenga a dónde volver.
    await snapshot_version(
        session,
        listing=listing,
        changelog=payload.changelog,
        published_by=principal.user_id,
    )

    # Append-only audit: who published which private listing.
    session.add(
        MarketplaceAuditEntry(
            tenant_id=tenant_id,
            actor=_actor(principal),
            action=MarketplaceAuditAction.SHARE.value,
            listing_id=listing.id,
            installation_id=None,
            detail={
                "event": "private_publish",
                "kind": parsed.kind.value,
                "name": parsed.name,
                "version": parsed.version,
                "review_status": listing.review_status,
            },
        )
    )
    await session.flush()
    await session.refresh(listing)
    return to_listing_response(listing)


# ===========================================================================
# PUT /marketplace/private/listings/{id} — update an own PRIVATE listing
# ===========================================================================
@router.put(
    "/private/listings/{listing_id}",
    response_model=MarketplaceListingResponse,
)
async def update_private_listing(
    listing_id: UUID,
    payload: PrivateListingUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> MarketplaceListingResponse:
    """Re-publish (update) the caller tenant's OWN private listing.

    Re-parses the submitted manifest (same validation as publish; a bad
    manifest is a 422) and refreshes the row's ``name`` / ``version`` /
    ``description`` / ``manifest`` / ``requested_permissions``. The listing's
    ``kind`` is immutable — a manifest whose kind disagrees with the existing
    listing's kind is a 422. RLS + the explicit own-tenant filter scope this
    to the caller tenant's private listing; another tenant's listing (or a
    global one) is a clean 404.
    """
    tenant_id = require_tenant_id(principal)
    listing = await _load_private_listing(session, listing_id, tenant_id)

    existing_kind = MarketplaceListingKind(listing.kind)
    try:
        parsed = parse_private_listing(kind=existing_kind, manifest_text=payload.manifest)
    except PrivateListingFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    listing.name = parsed.name
    listing.version = parsed.version
    listing.description = parsed.description
    listing.author = payload.author
    listing.manifest = parsed.manifest
    listing.requested_permissions = parsed.requested_permissions

    # ADR 0142 D6: re-publicar devuelve el listing a la cola. Una versión nueva
    # de algo ya aprobado NO hereda la aprobación de la anterior — si la
    # heredase, el primer listing aprobado sería un pase permanente para
    # publicar cualquier cosa después.
    #
    # La excepción es quedarse quieto: un listing que YA está en la cola sigue
    # en la cola (`pending_review → pending_review` no es una arista del grafo,
    # y tratar la corrección de un borrador como una transición sería inventar
    # una que no existe). El autor puede corregir mientras espera.
    if listing.review_status != ListingReviewStatus.PENDING_REVIEW.value:
        submit_for_review(
            session,
            listing=listing,
            actor=_actor(principal),
            actor_user_id=principal.user_id,
        )
    await snapshot_version(
        session,
        listing=listing,
        changelog=payload.changelog,
        published_by=principal.user_id,
    )

    session.add(
        MarketplaceAuditEntry(
            tenant_id=tenant_id,
            actor=_actor(principal),
            action=MarketplaceAuditAction.SHARE.value,
            listing_id=listing.id,
            installation_id=None,
            detail={
                "event": "private_update",
                "kind": parsed.kind.value,
                "name": parsed.name,
                "version": parsed.version,
                "review_status": listing.review_status,
            },
        )
    )
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"a private listing {parsed.name!r} version {parsed.version!r} already exists"),
        ) from exc
    await session.refresh(listing)
    return to_listing_response(listing)


# ===========================================================================
# DELETE /marketplace/private/listings/{id} — unpublish an own PRIVATE listing
# ===========================================================================
@router.delete("/private/listings/{listing_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unpublish_private_listing(
    listing_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Unpublish (soft-delete) the caller tenant's OWN private listing.

    Soft-deletes the row (``deleted_at`` set) so it drops out of browse /
    detail while the immutable audit trail and any installations' FK survive.
    RLS + the own-tenant filter scope this to the caller tenant's private
    listing; another tenant's listing (or a global one) is a 404.
    """
    tenant_id = require_tenant_id(principal)
    listing = await _load_private_listing(session, listing_id, tenant_id)

    listing.deleted_at = datetime.now(tz=UTC)
    session.add(
        MarketplaceAuditEntry(
            tenant_id=tenant_id,
            actor=_actor(principal),
            action=MarketplaceAuditAction.SHARE.value,
            listing_id=listing.id,
            installation_id=None,
            detail={
                "event": "private_unpublish",
                "name": listing.name,
                "version": listing.version,
            },
        )
    )
    await session.flush()
