"""Comprobar y aplicar la actualizacion de una instalacion.

Dos rutas y los dos helpers que deciden a que version se va: el delta contra
el pin y la resolucion del objetivo (ultima aprobada, respetando el pin y el
nivel de confianza).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
from api_server.marketplace.deploy import ensure_listing_version
from api_server.marketplace.deployment_refresh import refresh_installation_deployments
from api_server.marketplace.install import InstallError, InstallOrchestrator
from api_server.marketplace.listing_versions import (
    permission_diff,
    pinned_version,
)
from api_server.marketplace.update_consent import apply_update_consent
from api_server.marketplace.versioning import (
    VersioningError,
    is_major_bump,
    is_outdated,
    select_update_target,
)
from api_server.routers._helpers import require_tenant_id
from api_server.routers.marketplace.common import (
    _actor,
    _load_installation_with_listing,
    _sibling_listings,
    get_install_orchestrator,
)
from api_server.schemas.marketplace import (
    InstallationUpdateCheckResponse,
    InstallationUpdateRequest,
    InstallationUpdateResponse,
    to_installation_response,
    to_update_check_response,
)

router = APIRouter()


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

    # ADR 0142 D7: el delta de permisos contra la versión que se propone. Es lo
    # que el banner de la ficha pinta en claro ANTES de que nadie pulse nada —
    # enterarse de que una actualización pide más permisos DESPUÉS de aplicarla
    # es enterarse tarde.
    delta_payload: dict[str, Any] | None = None
    if assessment.target_version:
        target = next((s for s in siblings if s.version == assessment.target_version), None)
        if target is not None:
            delta_payload = await _delta_against_pin(
                session, installation=installation, target_listing=target
            )

    return to_update_check_response(
        installation=installation,
        name=listing.name,
        assessment=assessment,
        permission_delta=delta_payload,
    )


async def _delta_against_pin(
    session: AsyncSession,
    *,
    installation: MarketplaceInstallation,
    target_listing: MarketplaceListing,
) -> dict[str, Any]:
    """El delta de permisos entre lo que se consintió y lo que pide `target_listing`.

    La base de comparación es, por este orden:

    1. la **fila de versión pinada** — el registro de lo que se consintió, que
       es lo correcto porque el manifest del listing puede haberse movido;
    2. si no hay pin (instalación anterior al histórico, o listing global sin
       fila), los `granted_permissions` de la propia instalación.

    El segundo camino es una degradación honesta y no un atajo: sin snapshot no
    se puede saber qué pedía la versión vieja, pero sí qué se concedió — y para
    decidir «¿esto pide algo que no concediste?» eso basta.
    """
    pinned = await pinned_version(session, pinned_version_id=installation.pinned_version_id)
    baseline: Any
    if pinned is not None:
        baseline = list(pinned.requested_permissions or [])
    else:
        baseline = list(installation.granted_permissions or [])
    return permission_diff(baseline, list(target_listing.requested_permissions or [])).as_dict()


def _resolve_update_target(
    *,
    installed_version: str,
    by_version: dict[str, MarketplaceListing],
    payload: InstallationUpdateRequest,
    assessment: Any,
) -> str:
    """La versión concreta a la que se actualiza — o el 4xx que lo explica.

    Dos caminos: un `target_version` pineado por quien llama (que hay que
    validar) o el que `select_update_target` eligió solo. Vive fuera del
    endpoint porque juntos pasaban del límite de ramas de `ruff`, y el límite
    tenía razón: la selección de versión y el consentimiento del delta son dos
    decisiones distintas que se leen mejor por separado.
    """
    if payload.target_version is None:
        target_version = assessment.target_version or ""
        if target_version:
            return target_version
        # O ya está al día, o las únicas versiones nuevas son saltos de major
        # a los que quien llama no se ha apuntado.
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

    target_version = payload.target_version
    if target_version not in by_version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no listing version {target_version!r} available for this install",
        )
    try:
        newer = is_outdated(installed_version, target_version)
    except VersioningError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    # ADR 0142 D7: el rollback usa ESTE endpoint, apuntando a una versión
    # anterior del histórico. La guarda de «no es más nueva» sigue siendo
    # correcta para una actualización accidental, pero sería el obstáculo
    # equivocado para una vuelta atrás deliberada; de ahí el opt-in.
    if not newer and not payload.allow_rollback:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"target version {target_version!r} is not newer than the "
                f"installed version {installed_version!r}"
                " (set allow_rollback=true to go back on purpose)"
            ),
        )
    # Un pin a otro major sigue necesitando el opt-in explícito: una
    # actualización nunca cruza una frontera de major sin él.
    if newer and not payload.allow_major and is_major_bump(installed_version, target_version):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="target crosses a major version; set allow_major=true to opt in",
        )
    return target_version


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

    target_version = _resolve_update_target(
        installed_version=installation.version,
        by_version=by_version,
        payload=payload,
        assessment=assessment,
    )
    target_listing = by_version[target_version]
    from_version = installation.version

    # ADR 0142 D7 — el re-consentimiento del DELTA, antes de tocar nada. La
    # decisión vive en `marketplace/update_consent.py`, no aquí: este router ya
    # pasa de 1.700 líneas y el propio plan lo prohíbe expresamente.
    delta_payload = await _delta_against_pin(
        session, installation=installation, target_listing=target_listing
    )
    apply_update_consent(
        session,
        installation=installation,
        target_listing=target_listing,
        delta_payload=delta_payload,
        decisions=dict(payload.consent or {}),
        tenant_id=tenant_id,
        actor=_actor(principal),
        from_version=from_version,
        to_version=target_version,
    )

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

    # ---------------------------------------------------------------------
    # Re-pinar + refrescar los despliegues
    # ---------------------------------------------------------------------
    target_row = await ensure_listing_version(
        session, listing=target_listing, version=target_version
    )
    if target_row is not None:
        installation.pinned_version_id = target_row.id
    new_schema = target_row.config_schema if target_row is not None else None
    if new_schema is None:
        raw = (target_listing.manifest or {}).get("config_schema")
        new_schema = dict(raw) if isinstance(raw, dict) else None

    report = await refresh_installation_deployments(
        session,
        installation_id=installation.id,
        new_version=target_version,
        new_schema=new_schema,
    )
    session.add(
        MarketplaceAuditEntry(
            tenant_id=tenant_id,
            actor=_actor(principal),
            action=MarketplaceAuditAction.REFRESH.value,
            listing_id=target_listing.id,
            installation_id=installation.id,
            detail={
                "event": "deployments_refreshed",
                "from_version": from_version,
                "to_version": target_version,
                "rollback": not is_outdated(from_version, target_version),
                **report.as_dict(),
            },
        )
    )
    await session.flush()

    await session.refresh(installation)
    return InstallationUpdateResponse(
        installation=to_installation_response(installation),
        from_version=from_version,
        to_version=target_version,
        deployments=report.as_dict(),
        permission_delta=delta_payload,
    )
