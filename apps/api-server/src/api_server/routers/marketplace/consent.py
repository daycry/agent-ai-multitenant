"""El consentimiento por permiso de una instalacion.

Dos rutas: la superficie de permisos que pide un install y la decision del
humano sobre cada uno. Van juntas y aparte del alta porque son el ciclo de
consentimiento (`api_server.marketplace.consent`), no la instalacion --
de hecho pueden ocurrir dias despues, con otro humano delante.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
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
)
from api_server.marketplace.consent import (
    ConsentError,
    apply_decisions,
    summarize,
)
from api_server.routers.marketplace.common import (
    _actor,
    _load_installation_with_listing,
)
from api_server.schemas.marketplace import (
    ConsentDecisionRequest,
    InstallationPermissionsResponse,
    to_permissions_response,
)

router = APIRouter()


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
