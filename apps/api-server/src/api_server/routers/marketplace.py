"""`/marketplace` endpoints — browse the catalog, install, uninstall (Plan 09 task_09_03).

The marketplace REST surface, Phase A (the foundation). Five routes:

  - ``GET  /marketplace/listings``            browse the catalog (paginated,
                                              optional kind / trust_level filter)
  - ``GET  /marketplace/listings/{id}``       listing detail
  - ``POST /marketplace/installations``       install a listing into the caller's
                                              tenant (+ audit)
  - ``DELETE /marketplace/installations/{id}``  uninstall (tear down + audit)
  - ``POST /marketplace/installations/{id}/revoke``  revoke (security teardown + audit)
  - ``GET  /marketplace/installations``       list the caller-tenant installs

Uninstall vs. revoke (task_09_08): both flip the install to ``revoked``,
disable it for agents/projects (it is no longer "live" — the partial-unique
live index frees up), soft-delete the row, and ALWAYS write a marketplace
audit entry. They differ only in INTENT / audit action: ``DELETE`` is the
operator-driven teardown (``uninstall`` action), while ``POST .../revoke`` is
the explicit security revocation (``revoke`` action) — e.g. a community tool
flagged after install. The shared teardown lives in
:func:`_revoke_installation`; the audit is mandatory and append-only (the
``marketplace_audit_entries`` table enforces no-update/no-delete via RLS,
migration 0043), so an audit row can never be silently dropped.

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

import os
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    get_tenant_session,
    require_system_admin,
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
    MarketplaceShare,
    MarketplaceSource,
    MarketplaceSourceType,
    MarketplaceTrustLevel,
)
from api_server.marketplace.consent import (
    ConsentError,
    apply_decisions,
    consent_required_for,
    summarize,
)
from api_server.marketplace.install import InstallError, InstallOrchestrator, LocalArtifactFetcher
from api_server.marketplace.install import default_artifact_root as _default_artifact_root
from api_server.marketplace.private_listing import (
    PrivateListingFormatError,
    parse_private_listing,
)
from api_server.marketplace.versioning import (
    VersioningError,
    is_major_bump,
    is_outdated,
    select_update_target,
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
    InstallationUpdateCheckResponse,
    InstallationUpdateRequest,
    InstallationUpdateResponse,
    MarketplaceInstallationResponse,
    MarketplaceListingResponse,
    MarketplaceShareResponse,
    PrivateListingPublishRequest,
    PrivateListingUpdateRequest,
    ShareCreateRequest,
    to_installation_response,
    to_listing_response,
    to_permissions_response,
    to_share_response,
    to_update_check_response,
)

router = APIRouter(prefix="/marketplace", tags=["marketplace"])

# System-Admin cross-tenant audit surface. Mounted separately from the
# tenant-scoped ``/marketplace`` router so it runs on the BYPASSRLS admin
# session and is gated to the System Admin — it enumerates EVERY tenant's
# shares for audit (the platform-wide oversight the plan requires).
admin_router = APIRouter(prefix="/admin/marketplace", tags=["admin", "marketplace"])

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


async def _sibling_listings(
    session: AsyncSession, listing: MarketplaceListing
) -> list[MarketplaceListing]:
    """All visible listing rows that are the SAME logical listing as ``listing``.

    A logical marketplace listing is keyed by ``(source_id, tenant_id, kind,
    name)``; each published version is a distinct row (the
    ``uq_marketplace_listings_source_tenant_name_version`` constraint). The
    update flow needs every available version of the installed listing, so we
    match those four coordinates (including the SAME ``tenant_id`` — a global
    catalog listing and a tenant's private listing of the same name are
    distinct lines and must not cross-pollinate versions). RLS already scopes
    the visible set; soft-deleted rows are excluded.
    """
    tenant_filter = (
        MarketplaceListing.tenant_id == listing.tenant_id
        if listing.tenant_id is not None
        else MarketplaceListing.tenant_id.is_(None)
    )
    result = await session.execute(
        select(MarketplaceListing).where(
            MarketplaceListing.source_id == listing.source_id,
            tenant_filter,
            MarketplaceListing.kind == listing.kind,
            MarketplaceListing.name == listing.name,
            MarketplaceListing.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


def get_install_orchestrator() -> InstallOrchestrator:
    """FastAPI dependency: the install orchestrator for the update path.

    Reuses the Phase C pipeline (task_09_11): the official catalog's on-disk
    artifacts via :class:`LocalArtifactFetcher`. The platform signing key is
    read from the environment (``MARKETPLACE_SIGNING_PUBLIC_KEY``); a verified
    update with no key configured fails closed at the signature gate, exactly
    like a fresh install. Exposed as a dependency so the live registry
    fetcher / sandbox can be injected in tests (the on-disk artifact fetch +
    Docker sandbox are capability-gapped — see task_09_11) without touching
    the route.
    """
    public_key = os.environ.get("MARKETPLACE_SIGNING_PUBLIC_KEY")
    return InstallOrchestrator(
        fetcher=LocalArtifactFetcher(root_dir=_default_artifact_root()),
        public_key_pem=public_key.encode("utf-8") if public_key else None,
    )


async def _revoke_installation(
    session: AsyncSession,
    installation_id: UUID,
    principal: AuthPrincipal,
    *,
    action: MarketplaceAuditAction,
) -> None:
    """Flip a live installation to ``revoked`` and ALWAYS write an audit row.

    Shared teardown for the DELETE-uninstall and POST-revoke endpoints
    (task_09_08). The row is kept (``status=revoked`` + ``revoked_at`` /
    ``revoked_by`` + ``deleted_at``) rather than hard-deleted, so the
    immutable audit trail survives and the partial-unique live index
    (``uq_marketplace_installations_live``) frees the (tenant, listing,
    project) slot for a future re-install. RLS scopes the lookup to the
    caller's tenant, so another tenant's installation — and an already
    revoked one — surfaces as a clean 404 (no cross-tenant teardown, no
    double-revoke). The status flip and its audit entry share one
    transaction, so revocation can never commit without its mandatory audit
    record.
    """
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

    previous_status = installation.status
    now = datetime.now(tz=UTC)
    # Revoking disables the install for agents/projects: status=revoked drops
    # it out of the "live" set (the partial unique index excludes revoked),
    # and the soft-delete frees the live slot for a future re-install.
    installation.status = InstallationStatus.REVOKED.value
    installation.revoked_at = now
    installation.revoked_by = principal.user_id
    # ADR 0100 (pieza 2): la capacidad materializada cae CON su instalación,
    # en la misma transacción (test de no-orfandad).
    from api_server.marketplace.materialize import dematerialize_installation

    await dematerialize_installation(session, installation_id=installation.id)
    installation.deleted_at = now

    # Mandatory append-only audit (plan decision): same transaction as the
    # status flip, so the two commit atomically.
    session.add(
        MarketplaceAuditEntry(
            tenant_id=installation.tenant_id,
            actor=_actor(principal),
            action=action.value,
            listing_id=installation.listing_id,
            installation_id=installation.id,
            detail={"version": installation.version, "previous_status": previous_status},
        )
    )
    await session.flush()


# The well-known name of a tenant's private catalog source. The
# ``marketplace_sources.name`` column is globally unique, so the private
# source name carries the tenant id to keep one private source per tenant
# without colliding with another tenant's (or the official) source.
_PRIVATE_SOURCE_PREFIX = "private-catalog"


def _private_source_name(tenant_id: UUID) -> str:
    """The deterministic, globally-unique name of a tenant's private source."""
    return f"{_PRIVATE_SOURCE_PREFIX}:{tenant_id}"


async def _ensure_private_source(session: AsyncSession, tenant_id: UUID) -> MarketplaceSource:
    """Get-or-create the caller tenant's PRIVATE catalog source.

    A private listing belongs to a ``source_type=private`` source whose
    ``owner_tenant_id`` is the caller tenant — the source layer's record of
    "this catalog is internal to tenant X". The source table is tenant-
    agnostic (no RLS), so the row is keyed by the deterministic, globally-
    unique :func:`_private_source_name` to keep exactly one private source
    per tenant. A private source publishes ``community`` listings by default
    and is NOT trusted / does NOT require a signature (a tenant's own
    internal skill is not platform-verified). Idempotent.
    """
    name = _private_source_name(tenant_id)
    result = await session.execute(select(MarketplaceSource).where(MarketplaceSource.name == name))
    source = result.scalar_one_or_none()
    if source is not None:
        return source
    source = MarketplaceSource(
        name=name,
        description="Private internal catalog for this tenant.",
        source_type=MarketplaceSourceType.PRIVATE.value,
        is_trusted=False,
        requires_signature=False,
        default_trust_level=MarketplaceTrustLevel.COMMUNITY.value,
        owner_tenant_id=tenant_id,
    )
    session.add(source)
    await session.flush()
    return source


async def _load_private_listing(
    session: AsyncSession, listing_id: UUID, tenant_id: UUID
) -> MarketplaceListing:
    """Load the caller tenant's OWN live private listing, or 404.

    RLS already restricts visible private rows to the caller tenant, but we
    additionally require ``tenant_id`` to be non-NULL (the caller's tenant)
    so the private-listing write endpoints can NEVER touch a global catalog
    row — those are platform-published and immutable from a tenant session.
    Another tenant's private listing (RLS-hidden) and a global listing both
    surface as a clean 404.
    """
    result = await session.execute(
        select(MarketplaceListing).where(
            MarketplaceListing.id == listing_id,
            MarketplaceListing.tenant_id == tenant_id,
            MarketplaceListing.deleted_at.is_(None),
        )
    )
    listing = result.scalar_one_or_none()
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="private listing not found"
        )
    return listing


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
    orchestrator: InstallOrchestrator = Depends(get_install_orchestrator),
) -> MarketplaceInstallationResponse:
    """Install a listing into the caller's tenant (optionally a project).

    Phase A persists the installation row stamped with the caller's
    tenant + installer, records an :class:`MarketplaceAuditEntry`, and
    returns the row. The duplicate-live-install guard (partial unique
    index ``uq_marketplace_installations_live``) surfaces as a 409.

    Static analysis (task_prod12_mkt_01) DOES run here: the SAME
    bandit/semgrep pipeline the update path uses
    (:meth:`InstallOrchestrator.analyze_for_install`) — a finding above the
    trust policy aborts with 422 + an audit row; a listing with no on-disk
    artifact records an honest skip and installs (pre-registry gap).

    STILL DEFERRED (ADR 0081): signature verification + the sandbox probe —
    blocked on the registry runtime + an out-of-process sandbox the
    api-server can invoke (it deliberately has no Docker socket,
    Principle 2). The per-permission consent gate IS enforced below
    (a non-verified listing lands ``DISABLED`` with no permissions). See
    ADR 0081 for the full Phase B/C plan.
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

    # Gate de análisis estático (task_prod12_mkt_01): el MISMO pipeline
    # bandit/semgrep que el update, vía el orchestrator. Un hallazgo por
    # encima de la política aborta (audit row + 422); un artefacto ausente
    # en disco se registra como skip honesto y la instalación sigue —
    # bloquear ahí cerraría en falso todo el catálogo pre-registry.
    # DEFERRED to Phase B/C (ADR 0081): signature + sandbox probe — blocked
    # on an out-of-process sandbox runner (the api-server has no Docker
    # socket) + the artifact registry.
    try:
        analysis_gates = await orchestrator.analyze_for_install(
            session=session,
            tenant_id=tenant_id,
            actor=_actor(principal),
            listing=listing,
        )
    except InstallError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"install blocked by static analysis: {exc}",
        ) from exc

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

    # ADR 0100 (pieza 2): una instalación que nace ENABLED (listing verified)
    # MATERIALIZA su capacidad nativa en la misma transacción — skill o tool
    # de red (mcp_tool/http_endpoint); python/docker quedan diferidos honestos
    # hasta el sandbox out-of-process (ADR 0081 B/C). Un manifest inválido
    # aborta el install entero (422): nunca un ENABLED a medias.
    materialize_summary: dict[str, object] | None = None
    if initial_status == InstallationStatus.ENABLED.value:
        from api_server.marketplace.materialize import (
            MaterializeError,
            materialize_installation,
        )

        try:
            materialize_summary = (
                await materialize_installation(session, installation=installation, listing=listing)
            ).as_dict()
        except MaterializeError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"install cannot materialise its capability: {exc}",
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
                # task_prod12_mkt_01: el informe del gate de análisis (o su
                # skip honesto) viaja en el mismo audit row del install.
                "gates": analysis_gates,
                # ADR 0100: qué materializó (o por qué se difirió).
                "materialization": materialize_summary,
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    installation.granted_permissions = outcome.granted
    installation.denied_permissions = outcome.denied
    # Enable only once every requested permission is granted (or consent is
    # not required); otherwise the install stays disabled.
    installation.status = (
        InstallationStatus.ENABLED.value if outcome.enable else InstallationStatus.DISABLED.value
    )

    # ADR 0100 (pieza 2): el flip de consent decide la capacidad viva en la
    # MISMA transacción — enable materializa (o re-materializa: re-enable
    # resucita la fila soft-borrada); quedarse disabled la retira (una
    # capacidad no sobrevive a su permiso). Manifest inválido → 422 y el
    # enable entero aborta.
    from api_server.marketplace.materialize import (
        MaterializeError,
        dematerialize_installation,
        materialize_installation,
    )

    if outcome.enable:
        try:
            await materialize_installation(session, installation=installation, listing=listing)
        except MaterializeError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"enable cannot materialise its capability: {exc}",
            ) from exc
    else:
        await dematerialize_installation(session, installation_id=installation.id)

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
# GET /marketplace/installations/{id}/update-check — is it outdated?
# ===========================================================================
@router.get(
    "/installations/{installation_id}/update-check",
    response_model=InstallationUpdateCheckResponse,
)
async def check_installation_update(
    installation_id: UUID,
    allow_major: bool = Query(
        default=False,
        description=(
            "Include a MAJOR-version bump as the proposed target. Off by "
            "default — an update never auto-jumps a major version (semver "
            "compatibility); the explicit opt-in is required."
        ),
    ),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> InstallationUpdateCheckResponse:
    """Report whether an installation is outdated + which version it can take.

    Compares the install's resolved version against every available version
    of the same logical listing (task_09_12). The proposed ``target_version``
    respects semver compatibility: a major-version bump is only proposed with
    ``allow_major=true``. RLS scopes the lookup to the caller's tenant — a
    cross-tenant install is a clean 404.
    """
    installation, listing = await _load_installation_with_listing(session, installation_id)
    siblings = await _sibling_listings(session, listing)
    candidate_versions = [s.version for s in siblings]
    try:
        assessment = select_update_target(
            installation.version, candidate_versions, allow_major=allow_major
        )
    except VersioningError as exc:
        # A stored listing version is not parseable semver — a data integrity
        # problem, not a client error.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"listing has an invalid version: {exc}",
        ) from exc
    return to_update_check_response(
        installation=installation, name=listing.name, assessment=assessment
    )


# ===========================================================================
# POST /marketplace/installations/{id}/update — perform the update (re-run gates)
# ===========================================================================
@router.post(
    "/installations/{installation_id}/update",
    response_model=InstallationUpdateResponse,
)
async def perform_installation_update(
    installation_id: UUID,
    payload: InstallationUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    orchestrator: InstallOrchestrator = Depends(get_install_orchestrator),
    session: AsyncSession = Depends(get_tenant_session),
) -> InstallationUpdateResponse:
    """Update an installation to a newer compatible version (re-runs the gates).

    Resolves the update target (task_09_12): the highest available version
    strictly newer than the installed one, within the same MAJOR unless
    ``allow_major`` is set (the explicit opt-in — an update never silently
    crosses a major boundary). A pinned ``target_version`` must itself be a
    visible, newer, compatibility-respecting version. The update then re-runs
    the FULL install pipeline gates (signature / static-analysis / sandbox per
    the target's trust level, task_09_11) against the new version's artifact
    and, on success, re-points the install + writes an ``update`` audit row.

    A gate failure aborts (typed :class:`InstallError` → 422) leaving the
    install on its old version. RBAC: ``tenant_admin``; RLS scopes the
    install + listings to the caller's tenant.
    """
    tenant_id = require_tenant_id(principal)
    installation, listing = await _load_installation_with_listing(session, installation_id)

    if installation.status == InstallationStatus.REVOKED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="installation is revoked")

    siblings = await _sibling_listings(session, listing)
    by_version = {s.version: s for s in siblings}
    try:
        assessment = select_update_target(
            installation.version, list(by_version), allow_major=payload.allow_major
        )
    except VersioningError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"listing has an invalid version: {exc}",
        ) from exc

    # Resolve the concrete target: a pinned version (validated for
    # newer-ness + compatibility) or the auto-selected highest eligible one.
    if payload.target_version is not None:
        target_version = payload.target_version
        if target_version not in by_version:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no listing version {target_version!r} available for this install",
            )
        try:
            newer = is_outdated(installation.version, target_version)
        except VersioningError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        if not newer:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"target version {target_version!r} is not newer than the "
                    f"installed version {installation.version!r}"
                ),
            )
        # A major-version pin still needs the explicit opt-in — an update
        # never crosses a major boundary without it (semver compatibility).
        if not payload.allow_major and is_major_bump(installation.version, target_version):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="target crosses a major version; set allow_major=true to opt in",
            )
    else:
        target_version = assessment.target_version or ""
        if not target_version:
            # Either already up to date, or the only newer versions are major
            # bumps the caller did not opt into.
            if assessment.outdated:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "a newer version exists but crosses a major boundary; "
                        "set allow_major=true to opt in"
                    ),
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="installation is already up to date",
            )

    target_listing = by_version[target_version]
    from_version = installation.version
    try:
        await orchestrator.update(
            session=session,
            tenant_id=tenant_id,
            actor=_actor(principal),
            installation=installation,
            target_listing=target_listing,
        )
    except InstallError as exc:
        # A gate failed (bad signature, blocking analysis, failed sandbox).
        # The orchestrator already committed the abort audit row; the install
        # stays on its old version. Surface a 422 with a sanitized message.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"update blocked by install gate: {exc}",
        ) from exc

    await session.refresh(installation)
    return InstallationUpdateResponse(
        installation=to_installation_response(installation),
        from_version=from_version,
        to_version=target_version,
    )


# ===========================================================================
# DELETE /marketplace/installations/{id} — uninstall (teardown + audit)
# ===========================================================================
@router.delete("/installations/{installation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_listing(
    installation_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Uninstall: mark the installation ``revoked`` and write an audit row.

    The operator-driven teardown. The row is kept (status=revoked +
    ``revoked_at`` / ``revoked_by`` + ``deleted_at``) rather than
    hard-deleted so the audit trail and the partial-unique live constraint
    both stay intact. Another tenant's installation (and an already-revoked
    one) surfaces as 404. Records the ``uninstall`` audit action; see
    :func:`revoke_installation` for the security-revocation variant.
    """
    require_tenant_id(principal)
    await _revoke_installation(
        session,
        installation_id,
        principal,
        action=MarketplaceAuditAction.UNINSTALL,
    )


# ===========================================================================
# POST /marketplace/installations/{id}/revoke — revoke (security teardown + audit)
# ===========================================================================
@router.post(
    "/installations/{installation_id}/revoke",
    response_model=MarketplaceInstallationResponse,
)
async def revoke_installation(
    installation_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> MarketplaceInstallationResponse:
    """Revoke an installation: flip it to ``revoked`` and write a ``revoke``
    audit row (mandatory, append-only — plan task_09_08).

    The explicit security-revocation path (distinct from the operator
    teardown that DELETE performs): same effect — status=revoked, disabled
    for agents/projects, soft-deleted so the live slot frees up — but logged
    under the ``revoke`` audit action so the trail distinguishes a security
    revocation from a routine uninstall. Returns the revoked installation
    row (200) so the caller sees the new state + ``revoked_at`` / by.

    RBAC: gated to ``tenant_admin`` (this repo's project-owner role). RLS
    scopes the install to the caller's tenant — a cross-tenant caller gets
    404, never a cross-tenant write; an already-revoked install also 404s.
    """
    require_tenant_id(principal)
    await _revoke_installation(
        session,
        installation_id,
        principal,
        action=MarketplaceAuditAction.REVOKE,
    )
    # Re-load the revoked row to echo the new state (still visible to its own
    # tenant under RLS even though it is soft-deleted).
    result = await session.execute(
        select(MarketplaceInstallation).where(MarketplaceInstallation.id == installation_id)
    )
    installation = result.scalar_one()
    return to_installation_response(installation)


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
