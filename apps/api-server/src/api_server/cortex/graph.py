"""Grafo reactivo del córtex (F1) — clon del turn-loop del asistente.

El córtex **reutiliza** el sustrato del asistente sin duplicarlo: el loop
``decide→run_tools→decide→answer``, los topes (``MAX_TOOL_ROUNDS``, cap 1/turno
de la escritura de memoria) y la lógica de convergencia viven en
:mod:`api_server.assistant.graph`. Aquí solo se aporta:

  * :class:`CortexState` — subclase de :class:`AssistantState` cuyo ``tool_ctx``
    es un :class:`CortexToolContext` (owner-scoped, BYPASSRLS) en vez del
    ``AssistantToolContext`` tenant-scoped.
  * :func:`run_cortex_turn` — compila el grafo del asistente con dos seams: el
    ``state_type`` (``CortexState``) y el ``tool_runner`` (``run_cortex_tool``),
    de modo que el grafo ejecuta las tools del córtex con sus propios topes
    (``cortex_remember`` capado a 1/turno por ``_PER_TOOL_CALL_CAP``).

No se forka nada del asistente: ``build_assistant_graph`` quedó parametrizado por
``state_type`` + ``tool_runner`` (sus defaults dejan el asistente intacto), así
que cualquier mejora del loop beneficia a ambos.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from api_server.assistant.graph import (
    AssistantModelClient as CortexModelClient,
)
from api_server.assistant.graph import (
    AssistantState,
    AssistantTurnResult,
    build_assistant_graph,
)
from api_server.cortex.tools import CortexToolContext, run_cortex_tool


@dataclass
class CortexState(AssistantState):
    """Estado de un turno del córtex.

    Idéntico a :class:`AssistantState` salvo que ``tool_ctx`` es un
    :class:`CortexToolContext` (owner-scoped) en vez del ``AssistantToolContext``
    tenant-scoped. Se redeclara el campo para fijar el tipo correcto sin romper la
    herencia de los demás campos (system_prompt, chat_history, enabled_tools, los
    de bookkeeping del loop)."""

    tool_ctx: CortexToolContext | None = field(default=None)  # type: ignore[assignment]


async def run_cortex_turn(
    model: CortexModelClient,
    *,
    system_prompt: str,
    enabled_tools: tuple[str, ...],
    tool_ctx: CortexToolContext,
    chat_history: Sequence[dict[str, Any]] | None = None,
) -> AssistantTurnResult:
    """Corre UN turno del córtex y devuelve la respuesta sintetizada.

    Reutiliza el grafo del asistente (mismo loop + topes) con ``state_type`` =
    :class:`CortexState` y ``tool_runner`` = :func:`run_cortex_tool`. La respuesta
    es lo que el endpoint persiste como turno ``cortex``; ``tools_called`` permite
    al caller registrar qué tools se usaron (auditoría)."""
    initial = CortexState(
        system_prompt=system_prompt,
        chat_history=list(chat_history or []),
        enabled_tools=enabled_tools,
        tool_ctx=tool_ctx,
    )
    compiled = build_assistant_graph(model, state_type=CortexState, tool_runner=run_cortex_tool)
    final = await compiled.ainvoke(initial)
    final_state = CortexState(**final) if isinstance(final, dict) else final
    return AssistantTurnResult(
        content=final_state.answer or "",
        tools_called=tuple(final_state.tools_called),
        rounds=final_state.rounds,
    )


__all__ = [
    "CortexModelClient",
    "CortexState",
    "run_cortex_turn",
]
