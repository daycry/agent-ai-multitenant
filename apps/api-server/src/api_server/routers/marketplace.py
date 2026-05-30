"""`/marketplace` endpoints — browse the catalog, install, uninstall (Plan 09 task_09_03).

The marketplace REST surface, Phase A (the foundation). Five routes:

  - ``GET  /marketplace/listings``            browse the catalog (paginated,
                                              optional kind / trust_level filter)
  - ``GET  /marketplace/listings/{id}``       listing detail
  - ``POST /marketplace/installations``       install a listing into the caller's
                                              tenant (+ audit)
  - ``DELETE /marketplace/installations/{id}``  uninstall (revoke + audit)
  - ``GET  /marketplace/installations``       list the caller-tenant installs

Tenancy: every route runs on :func:`get_tenant_session`, so PostgreSQL
RLS scopes the queries. Browsing a listing exposes the GLOBAL catalog
(``tenant_id IS NULL``, via the ``marketplace_listings_global_read``
SELECT policy) plus the caller's own private listings; an installation /
audit row is strictly tenant-owned, so tenant A can never list, install
over, or revoke tenant B's rows.

RBAC: browsing is any tenant member (:func:`require_tenant_member`);
install / uninstall are tenant-admin writes (:func:`require_tenant_admin`).
This repo has no per-membership ``project_owner`` role — project-scoped
writes are gated to ``tenant_admin`` exactly like ``/projects`` and the
skills/tools routers, so we reuse that helper. The per-permission consent
flow and the trust / static-analysis / sandbox gates are **Phase B-C**;
in Phase A the install simply persists the row and records an audit entry
(the ``# TODO(Plan 09 Fase B/C)`` markers point at where they hook in).
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
    require_tenant_member,
)
from api_server.db.marketplace import (
    InstallationStatus,
    MarketplaceAuditAction,
    MarketplaceAuditEntry,
    MarketplaceInstallation,
    MarketplaceListing,
    MarketplaceListingKind,
    MarketplaceTrustLevel,
)
from api_server.routers._helpers import require_tenant_id
from api_server.routers._pagination import (
    apply_pagination,
    limit_query,
    offset_query,
)
from api_server.schemas.marketplace import (
    InstallationCreateRequest,
    MarketplaceInstallationResponse,
    MarketplaceListingResponse,
    to_installation_response,
    to_listing_response,
)

router = APIRouter(prefix="/marketplace", tags=["marketplace"])

# The audit ``actor`` string format. Mirrors TaskAuditEvent / the
# marketplace migration seeds ("user:<uuid>") so the audit trail is
# greppable across subsystems.
_ACTOR_PREFIX = "user"


def _actor(principal: AuthPrincipal) -> str:
    """Render the audit actor string for the authenticated principal."""
    return f"{_ACTOR_PREFIX}:{principal.user_id}"


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
            "Filter by trust tier (verified / community / experimental). "
            "422 on an unknown value."
        ),
    ),
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[MarketplaceListingResponse]:
    """Browse the marketplace catalog.

    RLS exposes the global catalog (``tenant_id IS NULL``) plus the
    caller's own private listings; another tenant's private listings are
    never returned. Deterministic ordering (``created_at, id``) so
    ``offset`` paging is stable.
    """
    stmt = select(MarketplaceListing).where(MarketplaceListing.deleted_at.is_(None))
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
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> MarketplaceListingResponse:
    """Fetch a single listing (global or the caller's own private one).

    Another tenant's private listing surfaces as 404 (RLS filters it
    out) so we never leak that its id exists.
    """
    result = await session.execute(
        select(MarketplaceListing).where(
            MarketplaceListing.id == listing_id,
            MarketplaceListing.deleted_at.is_(None),
        )
    )
    listing = result.scalar_one_or_none()
    if listing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="listing not found")
    return to_listing_response(listing)


# ===========================================================================
# POST /marketplace/installations — install a listing
# ===========================================================================
@router.post(
    "/installations",
    response_model=MarketplaceInstallationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def install_listing(
    payload: InstallationCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> MarketplaceInstallationResponse:
    """Install a listing into the caller's tenant (optionally a project).

    Phase A persists the installation row stamped with the caller's
    tenant + installer, records an :class:`MarketplaceAuditEntry`, and
    returns the row. The duplicate-live-install guard (partial unique
    index ``uq_marketplace_installations_live``) surfaces as a 409.

    The trust / static-analysis / sandbox / per-permission consent gates
    are Phase B-C — see the TODO markers below.
    """
    tenant_id = require_tenant_id(principal)

    # Resolve the listing under RLS: a global catalog row or the caller's
    # own private listing. Another tenant's private listing is invisible
    # here, so installing it is a clean 404 (no cross-tenant leak).
    result = await session.execute(
        select(MarketplaceListing).where(
            MarketplaceListing.id == payload.listing_id,
            MarketplaceListing.deleted_at.is_(None),
        )
    )
    listing = result.scalar_one_or_none()
    if listing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="listing not found")

    # Reject a duplicate LIVE install of the same listing into the same
    # scope. The partial unique index ``uq_marketplace_installations_live``
    # backs this for project-scoped installs, but PostgreSQL treats NULLs
    # as distinct, so it does NOT dedupe tenant-wide installs
    # (project_id IS NULL). We therefore enforce it explicitly here for
    # both cases, returning a clean 409 rather than silently creating a
    # second row. RLS already scopes this lookup to the caller's tenant.
    project_filter = (
        MarketplaceInstallation.project_id == payload.project_id
        if payload.project_id is not None
        else MarketplaceInstallation.project_id.is_(None)
    )
    existing = await session.execute(
        select(MarketplaceInstallation.id).where(
            MarketplaceInstallation.listing_id == listing.id,
            project_filter,
            MarketplaceInstallation.deleted_at.is_(None),
            MarketplaceInstallation.status != InstallationStatus.REVOKED.value,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="listing already installed for this tenant/project",
        )

    # TODO(Plan 09 Fase B/C): apply trust-level guardrails, run the
    # pre-install static analysis (Bandit/semgrep), the post-install
    # sandbox probe, and the per-permission consent flow before
    # persisting. In Phase A the install is a record-and-audit stub: the
    # granted permissions are taken verbatim from the request.

    installation = MarketplaceInstallation(
        tenant_id=tenant_id,
        listing_id=listing.id,
        project_id=payload.project_id,
        # The resolved version is the listing's current version (semver
        # re-pointing on update is task_09_12).
        version=listing.version,
        status=InstallationStatus.ENABLED.value,
        granted_permissions=payload.granted_permissions,
        installed_by=principal.user_id,
    )
    session.add(installation)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="listing already installed for this tenant/project",
        ) from exc

    # Append-only audit: who installed what. Mandatory — the install and
    # its audit row live in the same transaction so they commit atomically.
    session.add(
        MarketplaceAuditEntry(
            tenant_id=tenant_id,
            actor=_actor(principal),
            action=MarketplaceAuditAction.INSTALL.value,
            listing_id=listing.id,
            installation_id=installation.id,
            detail={
                "version": listing.version,
                "trust_level": listing.trust_level,
                "granted_permissions": payload.granted_permissions,
                "project_id": (str(payload.project_id) if payload.project_id else None),
            },
        )
    )
    await session.flush()
    await session.refresh(installation)
    return to_installation_response(installation)


# ===========================================================================
# DELETE /marketplace/installations/{id} — uninstall (revoke + audit)
# ===========================================================================
@router.delete("/installations/{installation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_listing(
    installation_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Uninstall: mark the installation ``revoked`` and write an audit row.

    The row is kept (status=revoked + ``revoked_at``/``revoked_by`` +
    ``deleted_at``) rather than hard-deleted so the audit trail and the
    partial-unique live constraint both stay intact. Another tenant's
    installation (and an already-revoked one) surfaces as 404.
    """
    require_tenant_id(principal)
    result = await session.execute(
        select(MarketplaceInstallation).where(
            MarketplaceInstallation.id == installation_id,
            MarketplaceInstallation.deleted_at.is_(None),
            MarketplaceInstallation.status != InstallationStatus.REVOKED.value,
        )
    )
    installation = result.scalar_one_or_none()
    if installation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="installation not found")

    now = datetime.now(tz=UTC)
    installation.status = InstallationStatus.REVOKED.value
    installation.revoked_at = now
    installation.revoked_by = principal.user_id
    # Soft-delete frees the (tenant, listing, project) live slot for a
    # future re-install while keeping the row for audit.
    installation.deleted_at = now

    session.add(
        MarketplaceAuditEntry(
            tenant_id=installation.tenant_id,
            actor=_actor(principal),
            action=MarketplaceAuditAction.UNINSTALL.value,
            listing_id=installation.listing_id,
            installation_id=installation.id,
            detail={"version": installation.version},
        )
    )
    await session.flush()


# ===========================================================================
# GET /marketplace/installations — list the caller-tenant installs
# ===========================================================================
@router.get("/installations", response_model=list[MarketplaceInstallationResponse])
async def list_installed(
    status_: InstallationStatus | None = Query(
        default=None,
        alias="status",
        description=(
            "Filter by installation status (enabled / disabled / revoked). "
            "422 on an unknown value."
        ),
    ),
    include_revoked: bool = Query(
        default=False,
        description=(
            "Include soft-deleted (revoked) installations. Off by default so "
            "operators see only live installs."
        ),
    ),
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[MarketplaceInstallationResponse]:
    """List the caller tenant's installations.

    RLS scopes the result to the caller's tenant — another tenant's
    installs are never returned. Soft-deleted (revoked) rows are excluded
    unless ``include_revoked=true``.
    """
    stmt = select(MarketplaceInstallation)
    if not include_revoked:
        stmt = stmt.where(MarketplaceInstallation.deleted_at.is_(None))
    if status_ is not None:
        stmt = stmt.where(MarketplaceInstallation.status == status_.value)
    stmt = stmt.order_by(
        MarketplaceInstallation.installed_at,
        MarketplaceInstallation.id,
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    return [to_installation_response(inst) for inst in result.scalars().all()]
