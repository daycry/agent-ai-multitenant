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
from sqlalchemy import or_, select
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
    MarketplaceListing,
    MarketplaceShare,
)
from api_server.db.models import Organization
from api_server.db.session import get_admin_sessionmaker
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
    TenantDirectoryEntry,
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
    shares = list((await session.execute(stmt)).scalars().all())
    # `task_mk_23` (UI-06): nombres junto a los UUID. Los listings son los PROPIOS
    # del tenant (RLS los deja ver); los nombres de los tenants destino NO —
    # `organizations` sólo enseña la fila propia—, así que se resuelven con la
    # sesión admin, acotada a los ids que ya están en los grants del llamante.
    listing_names: dict[UUID, str] = {}
    if shares:
        found = await session.execute(
            select(MarketplaceListing.id, MarketplaceListing.name).where(
                MarketplaceListing.id.in_({s.listing_id for s in shares})
            )
        )
        for listing_id, listing_name in found.all():
            listing_names[listing_id] = listing_name
    tenant_names = await _tenant_names({s.target_tenant_id for s in shares})
    return [
        to_share_response(
            s,
            listing_name=listing_names.get(s.listing_id),
            target_tenant_name=tenant_names.get(s.target_tenant_id),
        )
        for s in shares
    ]


async def _tenant_names(tenant_ids: set[UUID]) -> dict[UUID, str]:
    """`{tenant_id: name}` de los tenants pedidos, leído con la sesión BYPASSRLS.

    Sólo lectura y sólo para ids que el llamante ya conoce (están en SUS grants):
    no enumera nada que no tuviera ya en pantalla como UUID.
    """
    if not tenant_ids:
        return {}
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session:
        rows = await admin_session.execute(
            select(Organization.id, Organization.name).where(Organization.id.in_(tenant_ids))
        )
        names: dict[UUID, str] = {}
        for tenant_id, name in rows.all():
            names[tenant_id] = name
        return names


# ===========================================================================
# GET /marketplace/shares/tenant-directory — a quién se puede compartir
# ===========================================================================
TENANT_DIRECTORY_LIMIT = 20


@router.get("/shares/tenant-directory", response_model=list[TenantDirectoryEntry])
async def tenant_directory(
    q: str = Query(
        min_length=2,
        max_length=64,
        description="Fragmento del nombre o del slug del tenant (mínimo 2 caracteres).",
    ),
    principal: AuthPrincipal = Depends(require_tenant_admin),
) -> list[TenantDirectoryEntry]:
    """Los tenants ACTIVOS cuyo nombre o slug contiene ``q``, sin el propio.

    `task_mk_23` (UI-06): compartir pedía teclear el UUID del tenant destino, que
    nadie tiene a mano. Este directorio existe para el buscador del diálogo de
    compartir y está acotado a lo que ese diálogo necesita: `tenant_admin`, un
    fragmento de al menos dos caracteres, veinte resultados como mucho, sólo
    id/nombre/slug. Sustituye la regla anterior de «no revelar qué tenants
    existen» del POST —que sigue sin revelar nada distinto en su 409—: en una
    plataforma departamental (no SaaS masivo) el nombre de un departamento no es
    un secreto, y el operador pidió el buscador en el plan.

    Lee `organizations` con la sesión BYPASSRLS porque RLS sólo enseña la fila
    propia; el resto de la petición no toca la base.
    """
    own = require_tenant_id(principal)
    needle = f"%{q.strip()}%"
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session:
        rows = await admin_session.execute(
            select(Organization.id, Organization.name, Organization.slug)
            .where(
                Organization.id != own,
                Organization.is_active.is_(True),
                or_(Organization.name.ilike(needle), Organization.slug.ilike(needle)),
            )
            .order_by(Organization.name, Organization.id)
            .limit(TENANT_DIRECTORY_LIMIT)
        )
        return [
            TenantDirectoryEntry(id=tenant_id, name=name, slug=slug)
            for tenant_id, name, slug in rows.all()
        ]


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
