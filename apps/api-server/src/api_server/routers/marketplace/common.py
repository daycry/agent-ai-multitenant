"""Lo que comparten los modulos de `/marketplace`. **Sin rutas.**

Dos familias: la carga de filas con su comprobacion de visibilidad
(`_load_installation_with_listing`, `_load_private_listing`,
`_sibling_listings`) y el desmontaje comun de uninstall/revoke
(`_revoke_installation`), que es donde vive la garantia de que SIEMPRE se
escribe la entrada de auditoria.

`get_install_orchestrator` vive aqui **y en ningun otro sitio**: es la diana
de `app.dependency_overrides` de dos integraciones, y FastAPI casa los
overrides por identidad del objeto. Una segunda copia dejaria esos tests en
verde corriendo contra el orquestador de verdad.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
)
from api_server.db.marketplace import (
    InstallationStatus,
    MarketplaceAuditAction,
    MarketplaceAuditEntry,
    MarketplaceInstallation,
    MarketplaceListing,
    MarketplaceSource,
    MarketplaceSourceType,
    MarketplaceTrustLevel,
)
from api_server.marketplace.install import InstallOrchestrator, LocalArtifactFetcher
from api_server.marketplace.install import default_artifact_root as _default_artifact_root

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
