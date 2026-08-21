"""Instalar, desinstalar, revocar y listar instalaciones.

`uninstall` y `revoke` hacen lo mismo y se diferencian en la INTENCION que
queda escrita en la auditoria; el desmontaje compartido es
`common._revoke_installation`. El consentimiento por permiso NO esta aqui:
vive en :mod:`.consent`.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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
    MarketplaceInstallation,
    MarketplaceListing,
)
from api_server.marketplace.async_gates import queue_install_gates
from api_server.marketplace.finalize import finalize_installation
from api_server.marketplace.install import InstallError, InstallOrchestrator
from api_server.marketplace.materialize import MaterializeError
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


def _pending_installation(
    payload: InstallationCreateRequest,
    listing: MarketplaceListing,
    principal: AuthPrincipal,
    *,
    tenant_id: UUID,
) -> MarketplaceInstallation:
    """La fila de instalación recién nacida, sin veredicto todavía.

    Nace `analyzing` en los DOS caminos, y el estado definitivo lo pone
    `finalize_installation` (aquí mismo, dentro de la transacción del request) o
    el worker de la cola `marketplace` cuando termina las puertas. `version` es la
    versión actual del listing; el re-apuntado semver al actualizar es
    `task_09_12`.
    """
    return MarketplaceInstallation(
        tenant_id=tenant_id,
        listing_id=listing.id,
        project_id=payload.project_id,
        version=listing.version,
        status=InstallationStatus.ANALYZING.value,
        granted_permissions=[],
        denied_permissions=[],
        installed_by=principal.user_id,
    )


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
    response: Response,
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

    # ---------------------------------------------------------------------
    # prod-13 task_prod13_01 — la rama que NO analiza dentro del request
    # ---------------------------------------------------------------------
    # Se decide ANTES del análisis, que es justo lo que hay que no hacer aquí.
    if payload.async_gates:
        installation = _pending_installation(payload, listing, principal, tenant_id=tenant_id)
        session.add(installation)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="listing already installed for this tenant/project",
            ) from exc
        await queue_install_gates(
            session,
            installation=installation,
            listing=listing,
            actor=_actor(principal),
            requested_permissions=payload.granted_permissions,
            artifact_expected=orchestrator.artifact_expected(listing),
        )
        # 202 = «aceptada, todavía no hecha». El cuerpo es la instalación en
        # `analyzing`, o sea el recurso de estado mismo, y `Location` dice dónde
        # consultarlo — un 202 sin recurso al que apuntar obliga al cliente a
        # sondear a ciegas la lista entera.
        response.status_code = status.HTTP_202_ACCEPTED
        response.headers["Location"] = f"/marketplace/installations/{installation.id}"
        return to_installation_response(installation)

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

    # El gate de consentimiento por nivel de confianza (decisiones (a)+(b) de
    # `task_09_07`) y la materialización del ADR 0100 viven ahora en
    # `marketplace/finalize.py`, con su razonamiento: la fila nace en el estado
    # transitorio y el finalizador decide el definitivo. El intermedio no lo ve
    # nadie (misma transacción) y a cambio los DOS caminos —éste y el del worker
    # de la cola `marketplace`— cierran la instalación con la MISMA política.
    # Duplicarla era garantizar que divergieran al primer cambio.
    installation = _pending_installation(payload, listing, principal, tenant_id=tenant_id)
    session.add(installation)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="listing already installed for this tenant/project",
        ) from exc

    try:
        await finalize_installation(
            session,
            installation=installation,
            listing=listing,
            requested_permissions=payload.granted_permissions,
            actor=_actor(principal),
            gates=analysis_gates,
        )
    except MaterializeError as exc:
        # ADR 0100: un manifest que no puede materializar nunca deja un ENABLED a
        # medias. Aquí la fila no ha comiteado, así que el rollback la borra.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"install cannot materialise its capability: {exc}",
        ) from exc
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
# GET /marketplace/installations/{id} — el recurso de estado del 202
# ===========================================================================
@router.get(
    "/installations/{installation_id}",
    response_model=MarketplaceInstallationResponse,
)
async def get_installation(
    installation_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> MarketplaceInstallationResponse:
    """Una instalación por id — el recurso que consulta quien recibió un 202.

    prod-13 `task_prod13_01`. Faltaba: sólo existía el listado, así que un cliente
    que hubiera aceptado un 202 tendría que sondear `GET /installations?limit=100`
    y buscar la suya dentro, lo que además rompe en cuanto el tenant pasa de 100
    instalaciones. Un 202 cuyo recurso de estado no se puede leer por su URL no es
    un 202, es un «vuelve luego».

    Los estados transitorios que puede devolver son los de
    :class:`InstallationStatus`: `analyzing` mientras las puertas están en cola o
    corriendo, `blocked` si una puerta rechazó el artefacto (el motivo está en el
    audit row que escribió el aborto), y `enabled`/`disabled` cuando la
    instalación se cerró según el consentimiento que exige su nivel de confianza.

    RLS acota a la instalación al tenant del llamante: la de otro tenant es un 404
    limpio, nunca un 403 que confirmaría que existe. `tenant_member` y no
    `tenant_admin` porque es una LECTURA — quien está esperando el veredicto de
    una instalación no tiene por qué poder instalar.
    """
    # `require_tenant_id` no es decorativo aquí: `open_tenant_session` le da al
    # System Admin SIN tenant elegido la sesión BYPASSRLS, y esta consulta no
    # lleva filtro de tenant propio porque confía en RLS. Sin esta línea, ese
    # caso leería la instalación de cualquier tenant.
    require_tenant_id(principal)
    result = await session.execute(
        select(MarketplaceInstallation).where(
            MarketplaceInstallation.id == installation_id,
            MarketplaceInstallation.deleted_at.is_(None),
        )
    )
    installation = result.scalar_one_or_none()
    if installation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="installation not found")
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
