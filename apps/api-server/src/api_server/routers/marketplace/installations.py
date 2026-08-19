"""Instalar, desinstalar, revocar y listar instalaciones.

`uninstall` y `revoke` hacen lo mismo y se diferencian en la INTENCION que
queda escrita en la auditoria; el desmontaje compartido es
`common._revoke_installation`. El consentimiento por permiso NO esta aqui:
vive en :mod:`.consent`.
"""

from __future__ import annotations

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
)
from api_server.marketplace.consent import (
    consent_required_for,
)
from api_server.marketplace.install import InstallError, InstallOrchestrator
from api_server.routers._helpers import require_tenant_id
from api_server.routers._pagination import (
    apply_pagination,
    limit_query,
    offset_query,
)
from api_server.routers.marketplace.common import (
    _actor,
    _revoke_installation,
    get_install_orchestrator,
)
from api_server.schemas.marketplace import (
    InstallationCreateRequest,
    MarketplaceInstallationResponse,
    to_installation_response,
)

router = APIRouter()


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
            "Filter by installation status (enabled / disabled / revoked). 422 on an unknown value."
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
