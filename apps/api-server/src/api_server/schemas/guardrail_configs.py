"""Schemas de las capas de guardrails (prod-03 task_prod03_08)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from api_server.db.guardrail_config import GuardrailConfig


class GuardrailLayerResponse(BaseModel):
    """Una capa tal cual está guardada."""

    id: UUID
    scope: str
    tenant_id: UUID | None
    project_id: UUID | None
    config: dict[str, Any]
    version: int
    updated_at: datetime


class GuardrailLayerUpdate(BaseModel):
    """Cuerpo de un PUT de capa. ``config`` vacío = capa sin reglas propias."""

    config: dict[str, Any] = Field(default_factory=dict)


class GuardrailProvenanceEntry(BaseModel):
    """Qué capa ganó un guardrail concreto, y si está bloqueado.

    Es lo que permite a la UI decir «este check lo puso la plataforma y no lo
    puedes tocar» en vez de enseñar una config plana que el tenant cree suya.
    """

    hook: str
    key: str
    type: str
    winning_layer: str
    locked: bool


class GuardrailRejectedOverride(BaseModel):
    """Un intento de saltarse un candado que se ignoró, con su porqué."""

    hook: str
    key: str
    attempted_by: str
    reason: str


class GuardrailEffectiveConfigResponse(BaseModel):
    """La config efectiva más el recibo de cómo se construyó."""

    config: dict[str, Any]
    provenance: list[GuardrailProvenanceEntry]
    rejected_overrides: list[GuardrailRejectedOverride]
    locked_keys: dict[str, list[str]]


def to_layer_response(row: GuardrailConfig) -> GuardrailLayerResponse:
    return GuardrailLayerResponse(
        id=row.id,
        scope=row.scope,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        config=dict(row.config or {}),
        version=row.version,
        updated_at=row.updated_at,
    )


__all__ = [
    "GuardrailEffectiveConfigResponse",
    "GuardrailLayerResponse",
    "GuardrailLayerUpdate",
    "GuardrailProvenanceEntry",
    "GuardrailRejectedOverride",
    "to_layer_response",
]
