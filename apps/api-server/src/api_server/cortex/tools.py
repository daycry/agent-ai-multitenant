"""Tools owner-scoped del córtex (F1) — espejo de ``assistant/tools.py``.

Dos tools, ambas sobre la sesión admin/BYPASSRLS del córtex con filtro
``owner_user_id`` explícito (no hay RLS):

  * ``cortex_remember`` — WRITE: persiste un recuerdo del córtex
    (:func:`cortex.memory.cortex_remember`, ``metadata_.cortex=true``). Capada a
    1/turno por el grafo (reusa ``_PER_TOOL_CALL_CAP`` del asistente).
  * ``cortex_recall_more`` — READ: recall híbrido bajo demanda
    (:func:`cortex.memory.cortex_recall`) cuando el córtex necesita traer más
    contexto del que ya se inyectó en el system prompt.

El :class:`CortexToolContext` lleva la sesión + el ``owner_user_id`` + el
``tenant_id`` (discriminante físico de la memoria, Decisión D1). El registro /
``run_cortex_tool`` / ``cortex_tool_schemas`` son el espejo exacto del catálogo
del asistente.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api_server.cortex.memory import (
    CORTEX_RECALL_LIMIT,
    cortex_recall,
    cortex_remember,
)


@dataclass(frozen=True)
class CortexToolContext:
    """Lo que una tool del córtex necesita para actuar.

    La ``session`` es la del admin/BYPASSRLS; el aislamiento NO viene de RLS sino
    del filtro ``owner_user_id`` explícito que las funciones de
    :mod:`api_server.cortex.memory` imponen en todo SQL.
    """

    session: AsyncSession
    owner_user_id: UUID
    # Discriminante físico de la memoria del owner (Decisión D1), NO de autorización.
    tenant_id: UUID


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------
async def _cortex_remember(
    ctx: CortexToolContext,
    *,
    content: str,
    type: str = "semantic",
    tags: list[str] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Persiste UN recuerdo duradero del córtex del owner (capado a 1/turno)."""
    return await cortex_remember(
        ctx.session,
        owner_user_id=ctx.owner_user_id,
        tenant_id=ctx.tenant_id,
        content=content,
        type=type,
        tags=tuple(tags or ()),
    )


async def _cortex_recall_more(
    ctx: CortexToolContext,
    *,
    query: str,
    limit: int = CORTEX_RECALL_LIMIT,
    **_: Any,
) -> dict[str, Any]:
    """Recall híbrido bajo demanda de la memoria del córtex del owner."""
    capped = max(1, min(int(limit), 50))
    memories = await cortex_recall(
        ctx.session,
        owner_user_id=ctx.owner_user_id,
        tenant_id=ctx.tenant_id,
        query=query,
        limit=capped,
    )
    return {"count": len(memories), "memories": memories}


# ---------------------------------------------------------------------------
# Registry + JSON schemas (the shape an LLM tool-calling API expects)
# ---------------------------------------------------------------------------
ToolImpl = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class CortexToolEntry:
    """Una entrada del catálogo de tools del córtex: la impl async + el schema."""

    impl: ToolImpl
    schema: dict[str, Any]


CORTEX_TOOLS: dict[str, CortexToolEntry] = {
    "cortex_remember": CortexToolEntry(
        impl=_cortex_remember,
        schema={
            "name": "cortex_remember",
            "description": (
                "Guarda un dato DURADERO sobre el owner o sobre el trabajo que "
                "compartís para recordarlo en futuras conversaciones (una "
                "preferencia, una decisión, un interés, un hecho del proyecto). "
                "Úsalo SOLO cuando el owner comparta algo nuevo y duradero. "
                "Llámalo UNA SOLA VEZ por turno: si hay varios datos, reúnelos en "
                "un único texto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": (
                            "El dato a recordar, en una frase breve (p. ej. "
                            "'Al owner le interesa la arquitectura hexagonal')."
                        ),
                        "maxLength": 2000,
                    },
                    "type": {
                        "type": "string",
                        "enum": ["semantic", "episodic"],
                        "description": (
                            "semantic = preferencia/hecho durable (lo habitual); "
                            "episodic = un evento puntual."
                        ),
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Etiquetas opcionales para clasificar el recuerdo.",
                    },
                },
                "required": ["content"],
            },
        },
    ),
    "cortex_recall_more": CortexToolEntry(
        impl=_cortex_recall_more,
        schema={
            "name": "cortex_recall_more",
            "description": (
                "Busca en tu memoria asociativa (recall híbrido BM25 + vector + "
                "entidad) recuerdos relevantes para una consulta concreta. Úsalo "
                "cuando necesites traer más contexto del que ya conoces para "
                "responder con precisión."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Lo que quieres recordar (texto libre).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Máximo de recuerdos a devolver (1-50).",
                        "minimum": 1,
                        "maximum": 50,
                    },
                },
                "required": ["query"],
            },
        },
    ),
}


class UnknownCortexToolError(KeyError):
    """El nombre de tool no está en el catálogo del córtex."""


async def run_cortex_tool(
    name: str,
    ctx: CortexToolContext,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Despacha una tool del córtex por nombre.

    Lanza :class:`UnknownCortexToolError` para un nombre desconocido (el grafo
    filtra a las tools habilitadas, así que esto solo salta por un error de
    programación o un modelo hostil)."""
    entry = CORTEX_TOOLS.get(name)
    if entry is None:
        raise UnknownCortexToolError(f"unknown cortex tool {name!r}")
    return await entry.impl(ctx, **(arguments or {}))


def cortex_tool_schemas(enabled: tuple[str, ...]) -> list[dict[str, Any]]:
    """Los JSON schemas de las tools habilitadas, en orden de catálogo."""
    return [CORTEX_TOOLS[name].schema for name in enabled if name in CORTEX_TOOLS]


__all__ = [
    "CORTEX_TOOLS",
    "CortexToolContext",
    "CortexToolEntry",
    "UnknownCortexToolError",
    "cortex_tool_schemas",
    "run_cortex_tool",
]
