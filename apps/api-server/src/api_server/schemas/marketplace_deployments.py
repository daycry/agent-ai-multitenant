"""Schemas de los endpoints de despliegue del marketplace (ADR 0142, `task_mkt2_04`).

Fichero aparte de `schemas/marketplace.py` por el mismo motivo por el que el
router es nuevo: ese módulo ya sostiene la superficie de las fases A-E del plan
09 y esto es un dominio distinto (el despliegue), no una variante de instalar.

Reglas que se repiten aquí porque son las de la casa: **ningún request acepta
`tenant_id`, `deployed_by` ni `status`** — se derivan del principal autenticado y
nunca se honran desde el cable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api_server.db.marketplace import MarketplaceDeployment

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class DeploymentCreateRequest(BaseModel):
    """`POST /marketplace/installations/{id}/deployments`.

    `role_map` acepta las tres formas que :func:`~api_server.marketplace.deploy.normalize_role_map`
    documenta (ausente → los `targets` del manifest; lista de roles; mapping
    `{capacidad: [roles]}`), porque las tres puertas de UI mandan lo natural en
    cada sitio y traducirlo en el cliente sería una cuarta oportunidad de
    divergir.
    """

    model_config = _BASE_CONFIG

    project_id: UUID
    config: dict[str, Any] = Field(default_factory=dict)
    role_map: dict[str, Any] | list[str] | None = None


class DeploymentResponse(BaseModel):
    """Una fila de `marketplace_deployments` tal cual la ve el tenant.

    Se echa `created_refs` a propósito: es la trazabilidad que el ADR 0142 vende
    («¿qué escribió esto exactamente?») y no contiene secretos — son ids de
    filas. `config`, en cambio, puede contener punteros a Vault; punteros, nunca
    valores (lo garantiza el validador, no la buena fe).
    """

    model_config = ConfigDict(**_BASE_CONFIG, from_attributes=True)

    id: UUID
    tenant_id: UUID
    installation_id: UUID
    project_id: UUID
    config: dict[str, Any]
    role_map: dict[str, Any]
    deployed_version: str
    status: str
    created_refs: dict[str, Any]
    deployed_by: UUID | None
    retired_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DeploymentCreateResponse(BaseModel):
    """Lo que devuelve un despliegue: la fila **y qué pasó de verdad**.

    `warnings` y `oauth_pending` no son adorno: un despliegue que no encontró
    agentes del rol destino, o cuyo servidor MCP exige «Conectar», tiene que
    decirlo. Devolver solo un 201 con la fila haría de un no-entregado un éxito
    aparente — el modo de fallo que este plan existe para cerrar.
    """

    model_config = _BASE_CONFIG

    deployment: DeploymentResponse
    already_deployed: bool
    warnings: list[str] = Field(default_factory=list)
    oauth_pending: bool = False


class AvailableCapabilityResponse(BaseModel):
    """Una instalación del tenant AÚN NO desplegada en este proyecto.

    Lo que la UI necesita para pintar el formulario sin una segunda vuelta:
    el `config_schema` de la versión pinada y los `targets` sugeridos.
    """

    model_config = _BASE_CONFIG

    installation_id: UUID
    listing_id: UUID
    kind: str
    name: str
    version: str
    description: str | None
    trust_level: str
    config_schema: dict[str, Any] | None
    targets: list[str] = Field(default_factory=list)


class RetireResponse(BaseModel):
    """Resultado de retirar: cuántas referencias se deshicieron, exactamente."""

    model_config = _BASE_CONFIG

    deployment_id: UUID
    status: str
    removed_refs: int


def to_deployment_response(row: MarketplaceDeployment) -> DeploymentResponse:
    """Construye la respuesta desde la fila ORM (un solo sitio que lo sepa)."""
    return DeploymentResponse.model_validate(row)


__all__ = [
    "AvailableCapabilityResponse",
    "DeploymentCreateRequest",
    "DeploymentCreateResponse",
    "DeploymentResponse",
    "RetireResponse",
    "to_deployment_response",
]
