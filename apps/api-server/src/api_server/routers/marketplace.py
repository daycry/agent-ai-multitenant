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
from api_server.marketplace.consent import (
    ConsentError,
    apply_decisions,
    consent_required_for,
    summarize,
)
from api_server.routers._helpers import require_tenant_id
from api_server.routers._pagination import (
    apply_pagination,
    limit_query,
    offset_query,
)
from api_server.schemas.marketplace import (
    ConsentDecisionRequest,
    InstallationCreateRequest,
    InstallationPermissionsResponse,
    MarketplaceInstallationResponse,
    MarketplaceListingResponse,
    to_installation_response,
    to_listing_response,
    to_permissions_response,
)

router = APIRouter(prefix="/marketplace", tags=["marketplace"])

# The audit ``actor`` string format. Mirrors TaskAuditEvent / the
# marketplace migration seeds ("user:<uuid>") so the audit trail is
# greppable across subsystems.
_ACTOR_PREFIX = "user"


def _actor(principal: AuthPrincipal) -> str:
    """Render the audit actor string for the authenticated principal."""
    return f"{_ACTOR_PREFIX}:{principal.user_id}"


async def _load_installation_with_listing(
    session: AsyncSession, installation_id: UUID
) -> tuple[MarketplaceInstallation, MarketplaceListing]:
    """Load a tenant-owned installation + its listing, or 404.

    RLS scopes the installation to the caller's tenant, so another tenant's
    install is a clean 404 (no cross-tenant leak). The listing is resolved
    separately by id: a global (NULL-tenant) listing is visible, and a
    tenant's own private listing is too — either way the install could only
    exist for a listing this tenant can see. Soft-deleted (revoked)
    installs are included here so consent state is still inspectable; the
    write path rejects deciding on a revoked install.
    """
    inst_result = await session.execute(
        select(MarketplaceInstallation).where(
            MarketplaceInstallation.id == installation_id,
        )
    )
    installation = inst_result.scalar_one_or_none()
    if installation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="installation not found")

    listing_result = await session.execute(
        select(MarketplaceListing).where(
            MarketplaceListing.id == installation.listing_id,
        )
    )
    listing = listing_result.scalar_one_or_none()
    if listing is None:  # pragma: no cover - FK + ondelete CASCADE keeps these paired
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="listing not found")
    return installation, listing


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

    # TODO(Plan 09 Fase B/C): run the pre-install static analysis
    # (Bandit/semgrep) and the post-install sandbox probe before persisting.

    # Trust-level consent gate (plan decisions (a)+(b), task_09_07).
    # community / experimental listings ALWAYS require explicit
    # per-permission consent from the project owner: such an install lands
    # DISABLED with NO granted permissions and cannot be enabled until every
    # requested permission is granted via POST .../consent. A verified
    # listing needs no per-permission consent (minimal friction, decision
    # (d)) so it installs ENABLED, honouring the granted permissions from
    # the request verbatim (Phase A behaviour preserved).
    needs_consent = consent_required_for(listing.trust_level)
    if needs_consent:
        initial_status = InstallationStatus.DISABLED.value
        granted: list[object] = []
    else:
        initial_status = InstallationStatus.ENABLED.value
        granted = list(payload.granted_permissions)

    installation = MarketplaceInstallation(
        tenant_id=tenant_id,
        listing_id=listing.id,
        project_id=payload.project_id,
        # The resolved version is the listing's current version (semver
        # re-pointing on update is task_09_12).
        version=listing.version,
        status=initial_status,
        granted_permissions=granted,
        denied_permissions=[],
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
                "consent_required": needs_consent,
                "status": initial_status,
                "granted_permissions": granted,
                "project_id": (str(payload.project_id) if payload.project_id else None),
            },
        )
    )
    await session.flush()
    await session.refresh(installation)
    return to_installation_response(installation)


# ===========================================================================
# GET /marketplace/installations/{id}/permissions — surface for consent
# ===========================================================================
@router.get(
    "/installations/{installation_id}/permissions",
    response_model=InstallationPermissionsResponse,
)
async def get_installation_permissions(
    installation_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> InstallationPermissionsResponse:
    """Surface an install's requested permissions + their consent state.

    The consent UI (task_09_07) lists every permission the listing requests
    (allowed_domains / allowed_paths / network_policy) tagged GRANTED /
    DENIED / PENDING, whether this install requires per-permission consent
    at all (trust-level driven), and whether it is currently enabled. RLS
    scopes the lookup to the caller's tenant — another tenant's install is a
    clean 404. Any member may read; only the owner may decide (POST).
    """
    installation, listing = await _load_installation_with_listing(session, installation_id)
    summary = summarize(
        trust_level=listing.trust_level,
        requested_permissions=listing.requested_permissions or [],
        granted_permissions=installation.granted_permissions or [],
        denied_permissions=installation.denied_permissions or [],
    )
    return to_permissions_response(installation=installation, summary=summary)


# ===========================================================================
# POST /marketplace/installations/{id}/consent — record per-permission decisions
# ===========================================================================
@router.post(
    "/installations/{installation_id}/consent",
    response_model=InstallationPermissionsResponse,
)
async def decide_consent(
    installation_id: UUID,
    payload: ConsentDecisionRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> InstallationPermissionsResponse:
    """Record the project owner's grant/deny verdict on each permission.

    Granted permissions persist on the installation; denied ones persist in
    a parallel set. When EVERY requested permission is granted, a
    consent-gated install transitions to ``enabled`` and a ``consent`` audit
    row is written. If ANY required permission is denied (or still pending)
    the install stays ``disabled``; an explicit deny additionally writes a
    ``consent_denied`` audit row. A decision referencing a permission the
    listing never requested is a 422.

    RBAC: gated to ``tenant_admin`` (this repo's project-owner role, see the
    module docstring). RLS scopes the install to the caller's tenant — a
    non-owner / cross-tenant caller gets 403 / 404, never a cross-tenant
    write.
    """
    installation, listing = await _load_installation_with_listing(session, installation_id)

    # A revoked install is terminal — consent cannot be (re)decided on it.
    if installation.status == InstallationStatus.REVOKED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="installation is revoked",
        )

    decisions = {item.type: item.decision for item in payload.decisions}
    try:
        outcome = apply_decisions(
            trust_level=listing.trust_level,
            requested_permissions=listing.requested_permissions or [],
            existing_granted=installation.granted_permissions or [],
            existing_denied=installation.denied_permissions or [],
            decisions=decisions,
        )
    except ConsentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    installation.granted_permissions = outcome.granted
    installation.denied_permissions = outcome.denied
    # Enable only once every requested permission is granted (or consent is
    # not required); otherwise the install stays disabled.
    installation.status = (
        InstallationStatus.ENABLED.value if outcome.enable else InstallationStatus.DISABLED.value
    )

    actor = _actor(principal)
    detail = {
        "decisions": {ptype: decision.value for ptype, decision in decisions.items()},
        "granted_permissions": outcome.granted,
        "denied_permissions": outcome.denied,
        "enabled": outcome.enable,
    }
    # A grant batch is a CONSENT event; a deny additionally records the
    # immutable CONSENT_DENIED event (mandatory audit, plan decision).
    session.add(
        MarketplaceAuditEntry(
            tenant_id=installation.tenant_id,
            actor=actor,
            action=MarketplaceAuditAction.CONSENT.value,
            listing_id=listing.id,
            installation_id=installation.id,
            detail=detail,
        )
    )
    if outcome.any_denied:
        session.add(
            MarketplaceAuditEntry(
                tenant_id=installation.tenant_id,
                actor=actor,
                action=MarketplaceAuditAction.CONSENT_DENIED.value,
                listing_id=listing.id,
                installation_id=installation.id,
                detail=detail,
            )
        )
    await session.flush()
    await session.refresh(installation)

    summary = summarize(
        trust_level=listing.trust_level,
        requested_permissions=listing.requested_permissions or [],
        granted_permissions=installation.granted_permissions or [],
        denied_permissions=installation.denied_permissions or [],
    )
    return to_permissions_response(installation=installation, summary=summary)


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
