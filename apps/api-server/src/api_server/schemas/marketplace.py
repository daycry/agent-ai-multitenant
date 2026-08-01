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
    MarketplaceListingVersion,
    MarketplaceShare,
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
    # ADR 0142 D6. Se expone porque el autor tiene que poder distinguir «lo
    # mandé» de «está publicado» — la UI de publicación decía «publicado» donde
    # ahora dice «pendiente de revisión», y sin este campo no podría.
    review_status: str
    reviewed_at: datetime | None = None
    #: Presente solo cuando ``review_status == 'rejected'``. Es lo que el autor
    #: necesita para corregir; ocultarlo convertiría el rechazo en un muro.
    rejection_reason: str | None = None
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
        review_status=listing.review_status,
        reviewed_at=listing.reviewed_at,
        rejection_reason=listing.rejection_reason,
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
# Private tenant marketplace (task_09_16)
# =============================================================================
class PrivateListingPublishRequest(BaseModel):
    """Publish a tenant's OWN internal skill/tool as a PRIVATE listing.

    The manifest is the raw SKILL.md (``kind=skill``) or YAML tool-manifest
    (``kind=tool`` / ``mcp_server``) text — validated server-side by the
    Phase C parsers (a malformed manifest is a 422 and NO row is written).
    ``tenant_id`` (= the caller tenant), the private source, and the trust
    level are ALL server-derived: a publisher never sets the tenancy scope
    or the trust tier from the wire, so a private listing can never be
    forged as a global/verified one. ``name`` / ``version`` / ``description``
    come from the parsed manifest, not the request.
    """

    model_config = _BASE_CONFIG

    kind: MarketplaceListingKind
    manifest: str = Field(min_length=1)
    author: str | None = None
    # ADR 0142 D7: qué cambia en esta versión, en prosa. Va a la fila de
    # `marketplace_listing_versions`, que es lo que el revisor lee y lo que la
    # ficha de la instalación enseña junto al diff de permisos.
    changelog: str | None = None


class PrivateListingUpdateRequest(BaseModel):
    """Re-publish (update) an existing PRIVATE listing from a new manifest.

    Same validation as publish: the manifest is re-parsed and the row's
    ``name`` / ``version`` / ``description`` / ``manifest`` /
    ``requested_permissions`` are refreshed. The ``kind`` and the tenancy
    scope are immutable — the update is RLS-scoped to the caller tenant's own
    private listing, so another tenant's listing is a clean 404.
    """

    model_config = _BASE_CONFIG

    manifest: str = Field(min_length=1)
    author: str | None = None
    changelog: str | None = None


# =============================================================================
# Review queue (ADR 0142 D6 — task_mkt2_09 / task_mkt2_10)
# =============================================================================
class ListingRejectRequest(BaseModel):
    """Rechazar un listing en revisión. El motivo NO es opcional.

    ``min_length=1`` más el `str_strip_whitespace` de la config base hacen que
    ``"   "`` sea un 422 en la frontera, y :func:`review.reject_listing` lo
    vuelve a comprobar por dentro. Dos guardas para lo mismo a propósito: la de
    fuera da el error bonito, la de dentro protege a los llamantes que no pasan
    por HTTP.
    """

    model_config = _BASE_CONFIG

    reason: str = Field(min_length=1, max_length=2000)


class ListingApproveRequest(BaseModel):
    """Aprobar. ``promote=True`` además lo sube a ``verified`` de una vez."""

    model_config = _BASE_CONFIG

    promote: bool = False


class ListingPromoteRequest(BaseModel):
    """Cambiar el nivel de confianza de un listing YA publicado.

    Admite bajar además de subir: un ``verified`` que se estropea vuelve a
    ``community`` sin tener que despublicarlo (despublicar rompería las
    instalaciones vivas).
    """

    model_config = _BASE_CONFIG

    trust_level: MarketplaceTrustLevel = MarketplaceTrustLevel.VERIFIED


class ListingVersionResponse(BaseModel):
    """Una fila del histórico de versiones (ADR 0142 D7)."""

    model_config = _BASE_CONFIG

    id: UUID
    listing_id: UUID
    version: str
    changelog: str | None
    config_schema: dict[str, Any] | None
    requested_permissions: list[Any]
    published_by: UUID | None
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    created_at: datetime


def to_version_response(row: MarketplaceListingVersion) -> ListingVersionResponse:
    return ListingVersionResponse(
        id=row.id,
        listing_id=row.listing_id,
        version=row.version,
        changelog=row.changelog,
        config_schema=row.config_schema,
        requested_permissions=row.requested_permissions or [],
        published_by=row.published_by,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        created_at=row.created_at,
    )


# =============================================================================
# Cross-tenant sharing (task_09_17)
# =============================================================================
class ShareCreateRequest(BaseModel):
    """Share one of the caller tenant's PRIVATE listings with a target tenant.

    Opt-in by definition: the owner tenant explicitly names the single
    ``listing_id`` (which must be its OWN private listing) and the single
    ``target_tenant_id`` the listing is shared WITH. Everything else — the
    owner tenant (= the caller), who granted it — is server-derived from the
    authenticated principal, never honoured from the wire, so a share can
    never be forged on behalf of another owner.
    """

    model_config = _BASE_CONFIG

    listing_id: UUID
    target_tenant_id: UUID


class MarketplaceShareResponse(BaseModel):
    """A cross-tenant share grant as exposed to the owner / target / admin.

    Echoes the grant's coordinates (which private listing, owner tenant,
    target tenant) plus its lifecycle (granted / revoked). Carries no secret
    payload — a share names a listing, it does not embed it.
    """

    model_config = _BASE_CONFIG

    id: UUID
    listing_id: UUID
    owner_tenant_id: UUID
    target_tenant_id: UUID
    granted_by: UUID | None
    revoked_at: datetime | None
    revoked_by: UUID | None
    created_at: datetime
    updated_at: datetime


def to_share_response(share: MarketplaceShare) -> MarketplaceShareResponse:
    return MarketplaceShareResponse(
        id=share.id,
        listing_id=share.listing_id,
        owner_tenant_id=share.owner_tenant_id,
        target_tenant_id=share.target_tenant_id,
        granted_by=share.granted_by,
        revoked_at=share.revoked_at,
        revoked_by=share.revoked_by,
        created_at=share.created_at,
        updated_at=share.updated_at,
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
    # ADR 0142 D7: el delta de permisos entre la versión PINADA y la candidata.
    # Es lo que el banner de la ficha pinta en claro, y lo que decide si el
    # update va a exigir re-consentimiento antes de tocar nada.
    permission_delta: dict[str, Any] | None = None
    requires_consent: bool = False


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
    # -- ADR 0142 D7 -------------------------------------------------------
    #: Decisiones sobre el DELTA de permisos: `{tipo: "grant"|"deny"}`. Solo
    #: hacen falta los tipos que la versión nueva añade o ensancha — los ya
    #: concedidos NO se re-preguntan, que es el punto entero de D7: re-preguntar
    #: por todo enseña al operador a aceptar sin leer.
    consent: dict[str, str] | None = None
    #: El rollback. Sin esto, mover una instalación a una versión ANTERIOR es un
    #: 409 («no es más nueva»), que es la guarda correcta para una actualización
    #: accidental y el obstáculo equivocado para una vuelta atrás deliberada.
    allow_rollback: bool = False


class InstallationUpdateResponse(BaseModel):
    """The outcome of performing an update.

    Echoes the installation after the version re-point plus the version diff
    so the caller sees what moved.
    """

    model_config = _BASE_CONFIG

    installation: MarketplaceInstallationResponse
    from_version: str
    to_version: str
    #: Qué pasó con CADA despliegue de esta instalación (ADR 0142 D7). Un
    #: despliegue que no encaja en el esquema nuevo queda `disabled` con motivo
    #: y aparece aquí; los demás se actualizan igual.
    deployments: dict[str, Any] | None = None
    #: El delta de permisos que se re-consintió en esta llamada.
    permission_delta: dict[str, Any] | None = None


def to_update_check_response(
    *,
    installation: MarketplaceInstallation,
    name: str,
    assessment: Any,
    permission_delta: dict[str, Any] | None = None,
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
        permission_delta=permission_delta,
        requires_consent=bool(permission_delta and permission_delta.get("requires_consent")),
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
    "MarketplaceShareResponse",
    "MarketplaceTrustLevel",
    "PermissionDecision",
    "PermissionDecisionItem",
    "PermissionStateItem",
    "PrivateListingPublishRequest",
    "PrivateListingUpdateRequest",
    "ShareCreateRequest",
    "to_installation_response",
    "to_listing_response",
    "to_permissions_response",
    "to_share_response",
    "to_update_check_response",
]
