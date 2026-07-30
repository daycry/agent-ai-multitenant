"""Schemas del historial de curiosidad del córtex ("lo que está aprendiendo").

ADR 0078: cada persecución autónoma de curiosidad deja una fila de auditoría en
``cortex_curiosity_pursuits``; este schema la expone al Panel de Mente. Copy
honesto: comportamiento programado del bucle de curiosidad, no curiosidad
consciente.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CortexPursuitItem(BaseModel):
    """Una persecución de curiosidad del owner (auditoría + surfacing)."""

    id: UUID
    topic: str
    status: str
    created_at: datetime
    surfaced_at: datetime | None
    learning_memory_id: UUID | None
    search_count: int


__all__ = ["CortexPursuitItem"]
