"""Pydantic schemas for the `/marketplace` endpoints (Plan 09 task_09_03).

The marketplace REST surface has two read shapes and two write shapes:

  - :class:`MarketplaceListingResponse` — a browseable catalog entry
    (skill / tool / MCP server). It echoes the public manifest +
    requested permissions but **never** the cryptographic ``signature``
    (a secret-adjacent artifact verified server-side; exposing it would
    let a client forge a "verified" badge). The DB ``signature`` column
    is surfaced only as a boolean ``is_signed`` flag.

  - :class:`MarketplaceInstallationResponse` — an installation record for
    the caller's tenant. It echoes the *granted* permissions (what the
    project owner consented to) but not the listing's full secret
    payload.

  - :class:`InstallationCreateRequest` — install a listing into the
    caller's tenant (optionally a project). Phase A persists + audits the
    install; the trust / static-analysis / sandbox / consent gates are
    Phase B-C and are stubbed in the router.

All response models use ``from_attributes`` so they map straight off the
ORM rows. No request model accepts ``tenant_id`` / ``installed_by`` /
``status`` — those are server-derived from the authenticated principal,
never honoured from the wire (mirrors the skills/tools routers).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api_server.db.marketplace import (
    InstallationStatus,
    MarketplaceInstallation,
    MarketplaceListing,
    MarketplaceListingKind,
    MarketplaceTrustLevel,
)
from api_server.marketplace.consent import (
    ConsentState,
    ConsentSummary,
    PermissionDecision,
)

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


# =============================================================================
# Listing (browse the catalog)
# =============================================================================
class MarketplaceListingResponse(BaseModel):
    """A catalog entry as exposed to the browse/detail endpoints.

    ``is_signed`` replaces the raw ``signature`` column so a detached
    signature never crosses the wire. ``tenant_id`` is NULL for global
    catalog rows and set for a tenant's private listings.
    """

    model_config = _BASE_CONFIG

    id: UUID
    source_id: UUID
    tenant_id: UUID | None
    kind: str
    name: str
    version: str
    description: str | None
    author: str | None
    trust_level: str
    manifest: dict[str, Any]
    requested_permissions: list[Any]
    # Never echo the detached signature itself — only whether one exists.
    is_signed: bool
    created_at: datetime
    updated_at: datetime


def to_listing_response(listing: MarketplaceListing) -> MarketplaceListingResponse:
    return MarketplaceListingResponse(
        id=listing.id,
        source_id=listing.source_id,
        tenant_id=listing.tenant_id,
        kind=listing.kind,
        name=listing.name,
        version=listing.version,
        description=listing.description,
        author=listing.author,
        trust_level=listing.trust_level,
        manifest=listing.manifest or {},
        requested_permissions=listing.requested_permissions or [],
        is_signed=listing.signature is not None,
        created_at=listing.created_at,
        updated_at=listing.updated_at,
    )


# =============================================================================
# Installation (install / uninstall / list_installed)
# =============================================================================
class InstallationCreateRequest(BaseModel):
    """Install a listing into the caller's tenant.

    Only the listing to install, an optional project scope, and the
    subset of permissions the project owner consents to are accepted.
    Everything else (tenant_id, installed_by, status, resolved version)
    is server-derived. ``granted_permissions`` defaults to empty — in
    Phase A consent is a stub; Phase B-C wires the per-permission UI.
    """

    model_config = _BASE_CONFIG

    listing_id: UUID
    project_id: UUID | None = None
    granted_permissions: list[Any] = Field(default_factory=list)


class MarketplaceInstallationResponse(BaseModel):
    """An installation record for the caller's tenant."""

    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    listing_id: UUID
    project_id: UUID | None
    version: str
    status: str
    granted_permissions: list[Any]
    denied_permissions: list[Any]
    installed_by: UUID | None
    installed_at: datetime
    revoked_at: datetime | None
    revoked_by: UUID | None
    created_at: datetime
    updated_at: datetime


def to_installation_response(
    installation: MarketplaceInstallation,
) -> MarketplaceInstallationResponse:
    return MarketplaceInstallationResponse(
        id=installation.id,
        tenant_id=installation.tenant_id,
        listing_id=installation.listing_id,
        project_id=installation.project_id,
        version=installation.version,
        status=installation.status,
        granted_permissions=installation.granted_permissions or [],
        denied_permissions=installation.denied_permissions or [],
        installed_by=installation.installed_by,
        installed_at=installation.installed_at,
        revoked_at=installation.revoked_at,
        revoked_by=installation.revoked_by,
        created_at=installation.created_at,
        updated_at=installation.updated_at,
    )


# =============================================================================
# Per-permission consent (task_09_07)
# =============================================================================
class PermissionDecisionItem(BaseModel):
    """One project-owner verdict on a single requested permission.

    ``type`` is the canonical permission key (allowed_domains /
    allowed_paths / network_policy); ``decision`` is grant or deny. The
    router rejects (422) a ``type`` the listing did not request.
    """

    model_config = _BASE_CONFIG

    type: str
    decision: PermissionDecision


class ConsentDecisionRequest(BaseModel):
    """A batch of per-permission decisions from the project owner.

    The project owner approves/denies EACH requested permission. Granting
    every requested permission enables a consent-gated install; denying any
    keeps it disabled and writes a ``consent_denied`` audit row.
    """

    model_config = _BASE_CONFIG

    decisions: list[PermissionDecisionItem] = Field(min_length=1)


class PermissionStateItem(BaseModel):
    """A requested permission + its current consent state, for the UI."""

    model_config = _BASE_CONFIG

    type: str
    descriptor: dict[str, Any]
    state: ConsentState


class InstallationPermissionsResponse(BaseModel):
    """The full permission surface of an install, for the consent UI.

    Surfaces every requested permission (from the listing) with its current
    GRANTED / DENIED / PENDING state, whether this install requires
    per-permission consent at all (trust-level driven), and whether it is
    currently enabled.
    """

    model_config = _BASE_CONFIG

    installation_id: UUID
    listing_id: UUID
    status: str
    consent_required: bool
    all_granted: bool
    permissions: list[PermissionStateItem]


def to_permissions_response(
    *,
    installation: MarketplaceInstallation,
    summary: ConsentSummary,
) -> InstallationPermissionsResponse:
    return InstallationPermissionsResponse(
        installation_id=installation.id,
        listing_id=installation.listing_id,
        status=installation.status,
        consent_required=summary.consent_required,
        all_granted=summary.all_granted,
        permissions=[
            PermissionStateItem(type=p.type, descriptor=p.descriptor, state=p.state)
            for p in summary.permissions
        ],
    )


# =============================================================================
# Versioning + updates (task_09_12)
# =============================================================================
class InstallationUpdateCheckResponse(BaseModel):
    """Whether an installation is outdated and which version it can move to.

    The read shape of ``GET /installations/{id}/update-check``. Surfaces the
    currently-installed version, the single highest available listing
    version, the compatibility-respecting ``target_version`` an update would
    move to (``None`` when the only newer versions are major bumps and the
    caller did not opt in), and whether that highest version crosses a major
    boundary so the UI can prompt for the explicit opt-in.
    """

    model_config = _BASE_CONFIG

    installation_id: UUID
    listing_id: UUID
    name: str
    installed_version: str
    latest_version: str
    target_version: str | None
    outdated: bool
    update_available: bool
    latest_is_major_bump: bool


class InstallationUpdateRequest(BaseModel):
    """Perform an update of an installation to a newer compatible version.

    ``allow_major`` is the explicit opt-in the plan requires: an update never
    auto-jumps a MAJOR version unless the caller sets this true. An optional
    ``target_version`` pins the exact version to move to (it must be an
    available, newer, and — absent ``allow_major`` — same-major version);
    when omitted the server picks the highest eligible version.
    """

    model_config = _BASE_CONFIG

    allow_major: bool = False
    target_version: str | None = None


class InstallationUpdateResponse(BaseModel):
    """The outcome of performing an update.

    Echoes the installation after the version re-point plus the version diff
    so the caller sees what moved.
    """

    model_config = _BASE_CONFIG

    installation: MarketplaceInstallationResponse
    from_version: str
    to_version: str


def to_update_check_response(
    *,
    installation: MarketplaceInstallation,
    name: str,
    assessment: Any,
) -> InstallationUpdateCheckResponse:
    """Render a :class:`~api_server.marketplace.versioning.UpdateAssessment`."""
    return InstallationUpdateCheckResponse(
        installation_id=installation.id,
        listing_id=installation.listing_id,
        name=name,
        installed_version=assessment.installed_version,
        latest_version=assessment.latest_version,
        target_version=assessment.target_version,
        outdated=assessment.outdated,
        update_available=assessment.update_available,
        latest_is_major_bump=assessment.latest_is_major_bump,
    )


__all__ = [
    "ConsentDecisionRequest",
    "ConsentState",
    "InstallationCreateRequest",
    "InstallationPermissionsResponse",
    "InstallationStatus",
    "InstallationUpdateCheckResponse",
    "InstallationUpdateRequest",
    "InstallationUpdateResponse",
    "MarketplaceInstallationResponse",
    "MarketplaceListingKind",
    "MarketplaceListingResponse",
    "MarketplaceTrustLevel",
    "PermissionDecision",
    "PermissionDecisionItem",
    "PermissionStateItem",
    "to_installation_response",
    "to_listing_response",
    "to_permissions_response",
    "to_update_check_response",
]
