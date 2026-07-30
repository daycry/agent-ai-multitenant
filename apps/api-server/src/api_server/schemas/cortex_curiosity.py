"""Schemas del historial de curiosidad del córtex ("lo que está aprendiendo").

ADR 0078: cada persecución autónoma de curiosidad deja una fila de auditoría en
``cortex_curiosity_pursuits``; este schema la expone al Panel de Mente. Copy
honesto: comportamiento programado del bucle de curiosidad, no curiosidad
consciente.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CortexPursuitItem(BaseModel):
    """Una persecución de curiosidad del owner (auditoría + surfacing)."""

    id: UUID
    topic: str
    status: str
    created_at: datetime
    surfaced_at: datetime | None
    learning_memory_id: UUID | None
    search_count: int
    #: Veredicto del **owner-approval gate** (migración 0123). TRI-ESTADO, y los tres
    #: significan cosas distintas: ``None`` = propuesto y esperando decisión (el bucle
    #: no busca), ``True`` = aprobado, ``False`` = rechazado. La UI necesita
    #: exactamente esto para saber a QUÉ pursuit ponerle el botón Aprobar/Rechazar
    #: (``pursuitAwaitsApproval``: ``status==='selected' && approved===null``); sin el
    #: campo, el gate existía en la BD pero era invisible en pantalla.
    approved: bool | None = None
    #: Coste real de la pasada en USD (``Numeric(12,6)`` → float). Lo escribe el bucle
    #: al terminar de investigar; hasta entonces es 0. Se expone porque era un dato
    #: persistido que ninguna pantalla leía: sin él, el "coste de la curiosidad" del
    #: panel no se podía mostrar y el owner no veía qué le cuesta la autonomía.
    cost_usd: float = 0.0


class CortexPursuitDecisionRequest(BaseModel):
    """Decisión del owner en el gate (``POST .../pursuits/{id}/approve``).

    UN solo verbo para aprobar y rechazar, con el veredicto en el cuerpo (``approved``)
    en vez de dos rutas o un ``DELETE``: la acción del owner es la MISMA —dictar el
    veredicto de una propuesta— y partirla en dos endpoints obligaría a la UI a decidir
    la ruta y duplicaría el control de estados.

    ``extra='forbid'``: un campo de más (p. ej. ``status`` o ``approve``) es un cliente
    desalineado, y aceptarlo en silencio haría que una petición mal formada pareciese
    haber funcionado — en un gate de consentimiento eso es exactamente lo que no se
    puede permitir."""

    model_config = ConfigDict(extra="forbid")

    #: ``True`` aprueba (la siguiente pasada del bucle lo investiga); ``False`` rechaza
    #: (se cierra en ``skipped`` y no se reintenta). Obligatorio a propósito: no hay
    #: default, porque "no me dijiste qué decidías" no puede resolverse adivinando.
    approved: bool


__all__ = ["CortexPursuitDecisionRequest", "CortexPursuitItem"]
