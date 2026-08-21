"""`/marketplace/…/deployments` + `/projects/{id}/marketplace/available` (ADR 0142).

Fichero NUEVO, y no por gusto: `routers/marketplace.py` tiene ~1.500 líneas y es
la superficie de las fases A-E del plan 09. El despliegue es un dominio distinto
(qué proyecto recibe qué, con qué valores y para qué roles) y merece su propio
módulo en vez de engordar el que ya nadie lee entero.

Cuatro rutas (matriz completa en `docs/04-reference/rbac.md`):

* `POST /marketplace/installations/{id}/deployments` — `tenant_admin`: desplegar.
* `GET  /marketplace/installations/{id}/deployments` — `tenant_user`: «¿dónde
  está desplegado esto?».
* `POST /marketplace/deployments/{id}/retire` — `tenant_admin`: retirar (exacto,
  ADR 0142 §5).
* `GET  /projects/{id}/marketplace/available` — `tenant_user`: lo instalado que
  este proyecto todavía no tiene.

RBAC: mutaciones `require_tenant_admin`, lecturas `require_tenant_member` —
exactamente el reparto de `routers/marketplace.py`, del que esto es la
continuación natural. Documentado en `docs/04-reference/rbac.md`.

Tenencia: todo corre sobre `get_tenant_session`, así que la RLS de
`marketplace_deployments` (ENABLE + FORCE + `tenant_isolation`) hace el trabajo.
Un id de otro tenant no da 403 sino **404**: un 403 confirmaría que existe.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.db.domain import Project
from api_server.db.marketplace import (
    DeploymentStatus,
    InstallationStatus,
    MarketplaceDeployment,
    MarketplaceInstallation,
    MarketplaceListing,
    MarketplaceListingVersion,
)
from api_server.marketplace.deploy import (
    DeployError,
    deploy_installation,
    retire_deployment,
)
from api_server.schemas.marketplace_deployments import (
    AvailableCapabilityResponse,
    DeploymentCreateRequest,
    DeploymentCreateResponse,
    DeploymentResponse,
    RetireResponse,
    to_deployment_response,
)

router = APIRouter(prefix="/marketplace", tags=["marketplace", "deployments"])

# El lado PROYECTO de la misma entidad. Router aparte solo por el prefijo: es la
# MISMA tabla que lee la ficha de la instalación, que es justamente lo que
# impide que las dos superficies de UI diverjan (decisión D4).
project_router = APIRouter(prefix="/projects", tags=["projects", "marketplace"])


def _actor(principal: AuthPrincipal) -> str:
    """La cadena de auditoría, con el MISMO formato que el resto del subsistema."""
    return f"user:{principal.user_id}"


def _http_error(exc: DeployError) -> HTTPException:
    """Traduce el error tipado del servicio al código que le corresponde.

    El servicio ya decidió el código (404 / 409 / 422) porque es quien sabe por
    qué falló; el router no re-adivina. Los errores de validación viajan en
    `detail.errors` para que el formulario guiado los pinte todos a la vez.
    """
    detail: Any = str(exc)
    if exc.errors:
        detail = {"message": str(exc), "errors": exc.errors}
    return HTTPException(status_code=exc.status_code, detail=detail)


async def _load_installation(
    session: AsyncSession, installation_id: UUID
) -> MarketplaceInstallation:
    row = (
        await session.execute(
            select(MarketplaceInstallation).where(
                MarketplaceInstallation.id == installation_id,
                MarketplaceInstallation.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="installation not found")
    return row


# ---------------------------------------------------------------------------
# POST /marketplace/installations/{id}/deployments
# ---------------------------------------------------------------------------
@router.post(
    "/installations/{installation_id}/deployments",
    response_model=DeploymentCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_deployment(
    installation_id: UUID,
    payload: DeploymentCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> DeploymentCreateResponse:
    """Despliega la instalación en un proyecto: comprar pasa a ser recibir.

    Devuelve **201 también cuando es un no-op idempotente** (`already_deployed`),
    porque el estado final es el pedido: la instalación está desplegada en ese
    proyecto. Lo que cambia es el aviso, que el cliente debe enseñar.
    """
    try:
        result = await deploy_installation(
            session,
            installation_id=installation_id,
            project_id=payload.project_id,
            config=dict(payload.config or {}),
            role_map=payload.role_map,
            actor=_actor(principal),
            actor_user_id=principal.user_id,
        )
    except DeployError as exc:
        raise _http_error(exc) from exc
    except IntegrityError as exc:
        # El UNIQUE parcial `uq_marketplace_deployments_active` bajo concurrencia:
        # dos despliegues simultáneos del mismo par. El perdedor recibe un 409
        # honesto en vez de un 500.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ya existe un despliegue activo de esta instalación en ese proyecto",
        ) from exc

    row = (
        await session.execute(
            select(MarketplaceDeployment).where(MarketplaceDeployment.id == result.deployment_id)
        )
    ).scalar_one()
    return DeploymentCreateResponse(
        deployment=to_deployment_response(row),
        already_deployed=result.already_deployed,
        warnings=list(result.warnings),
        oauth_pending=result.oauth_pending,
    )


# ---------------------------------------------------------------------------
# GET /marketplace/installations/{id}/deployments
# ---------------------------------------------------------------------------
@router.get(
    "/installations/{installation_id}/deployments",
    response_model=list[DeploymentResponse],
)
async def list_deployments(
    installation_id: UUID,
    _principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[DeploymentResponse]:
    """«¿Dónde está desplegado esto?» — un SELECT, que es la promesa del ADR 0142.

    Incluye los `retired`: la ficha enseña historial, no solo lo vivo. El estado
    va en la respuesta para que la UI los distinga.
    """
    await _load_installation(session, installation_id)
    rows = (
        (
            await session.execute(
                select(MarketplaceDeployment)
                .where(MarketplaceDeployment.installation_id == installation_id)
                .order_by(MarketplaceDeployment.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [to_deployment_response(row) for row in rows]


# ---------------------------------------------------------------------------
# POST /marketplace/deployments/{id}/retire
# ---------------------------------------------------------------------------
@router.post("/deployments/{deployment_id}/retire", response_model=RetireResponse)
async def retire(
    deployment_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> RetireResponse:
    """Deshace EXACTAMENTE lo que el despliegue creó. La fila se conserva."""
    try:
        removed = await retire_deployment(
            session,
            deployment_id=deployment_id,
            actor=_actor(principal),
            actor_user_id=principal.user_id,
        )
    except DeployError as exc:
        raise _http_error(exc) from exc
    return RetireResponse(
        deployment_id=deployment_id,
        status=DeploymentStatus.RETIRED.value,
        removed_refs=removed,
    )


# ---------------------------------------------------------------------------
# GET /projects/{id}/marketplace/available
# ---------------------------------------------------------------------------
@project_router.get(
    "/{project_id}/marketplace/available",
    response_model=list[AvailableCapabilityResponse],
)
async def list_available_for_project(
    project_id: UUID,
    _principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[AvailableCapabilityResponse]:
    """Lo instalado en el tenant que este proyecto AÚN no tiene desplegado.

    Con su `config_schema` y sus `targets` para que la UI pinte el formulario sin
    una segunda vuelta. El `config_schema` sale de la versión PINADA cuando la
    hay (es lo que el tenant consintió) y del manifest vigente del listing si la
    instalación aún no está pinada.
    """
    project = (
        await session.execute(
            select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    deployed = set(
        (
            await session.execute(
                select(MarketplaceDeployment.installation_id).where(
                    MarketplaceDeployment.project_id == project_id,
                    MarketplaceDeployment.status == DeploymentStatus.ACTIVE.value,
                )
            )
        )
        .scalars()
        .all()
    )

    rows = (
        await session.execute(
            select(MarketplaceInstallation, MarketplaceListing, MarketplaceListingVersion)
            .join(MarketplaceListing, MarketplaceListing.id == MarketplaceInstallation.listing_id)
            .outerjoin(
                MarketplaceListingVersion,
                MarketplaceListingVersion.id == MarketplaceInstallation.pinned_version_id,
            )
            .where(
                MarketplaceInstallation.deleted_at.is_(None),
                MarketplaceInstallation.status == InstallationStatus.ENABLED.value,
            )
            .order_by(MarketplaceListing.name)
        )
    ).all()

    out: list[AvailableCapabilityResponse] = []
    for installation, listing, version in rows:
        if installation.id in deployed:
            continue
        manifest: dict[str, Any] = dict(
            (version.manifest if version is not None else None) or listing.manifest or {}
        )
        schema = version.config_schema if version is not None else None
        if schema is None:
            raw = manifest.get("config_schema")
            schema = dict(raw) if isinstance(raw, dict) else None
        targets = [t for t in (manifest.get("targets") or []) if isinstance(t, str)]
        out.append(
            AvailableCapabilityResponse(
                installation_id=installation.id,
                listing_id=listing.id,
                kind=listing.kind,
                name=listing.name,
                version=installation.version,
                description=listing.description,
                trust_level=listing.trust_level,
                config_schema=schema,
                targets=targets,
            )
        )
    return out


__all__ = ["project_router", "router"]
