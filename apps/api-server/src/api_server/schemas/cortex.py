"""Pydantic schemas para los endpoints del córtex del System Owner (F1, ADR 0074).

Espejo de :mod:`api_server.schemas.assistant`, pero con dos diferencias de fondo:

  * El córtex tiene **hilo persistente** (el asistente de tenant no): el turno
    lleva/devuelve un ``conversation_id`` y hay schemas de listado de hilos y de
    turnos.
  * Devuelve metadatos de deliberación: ``rounds``, ``reasoning_effort`` y
    ``degraded`` (si se degradó del camino claude_sdk a otro proveedor).

Todos los endpoints están gated por ``require_system_owner`` (DB-authoritative);
estos schemas sólo dan forma a los payloads.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class CortexTurnRequest(BaseModel):
    """Un mensaje del owner. Sin ``conversation_id`` → se abre un hilo nuevo."""

    model_config = _BASE_CONFIG

    message: str = Field(min_length=1, max_length=8000)
    conversation_id: UUID | None = None


class CortexTurnResponse(BaseModel):
    """La respuesta del córtex a un turno + sus metadatos de deliberación."""

    model_config = _BASE_CONFIG

    conversation_id: UUID
    answer: str
    tools_called: list[str]
    rounds: int
    # Effort efectivo del turno (None = sin razonamiento explícito).
    reasoning_effort: str | None = None
    # True si el córtex degradó del camino claude_sdk a otro proveedor del catálogo.
    degraded: bool = False


class CortexConversationResponse(BaseModel):
    """Un hilo del córtex en el listado (más reciente primero)."""

    model_config = _BASE_CONFIG

    id: UUID
    title: str | None = None
    model_id: str | None = None
    created_at: datetime
    updated_at: datetime
    # Recorte del último turno del hilo, para el selector de la UI.
    last_turn_preview: str | None = None


class CortexTurnItem(BaseModel):
    """Un turno individual de un hilo (orden cronológico)."""

    model_config = _BASE_CONFIG

    id: UUID
    role: str
    content: str
    created_at: datetime
    model_id: str | None = None


__all__ = [
    "CortexConversationResponse",
    "CortexTurnItem",
    "CortexTurnRequest",
    "CortexTurnResponse",
]
