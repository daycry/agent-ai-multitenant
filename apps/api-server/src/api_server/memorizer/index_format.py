"""Formato del hit del ÍNDICE de memorias (task_mem_01, revelación progresiva).

`memory_recall` ya no devuelve el contenido: devuelve, por hit, lo barato para
decidir qué merece leerse (un titular, el tipo, el ámbito, la procedencia y el
coste en tokens del detalle). El contenido completo se pide después por id con
`memory_get`. Este módulo es PURO (sin BD) para poder probar el formato solo.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

#: Longitud del titular (primeros caracteres del contenido).
HEADLINE_CHARS = 120

#: Caracteres por token de la estimación (heurística estándar para texto ES/EN;
#: no hay tokenizador en el api-server y el número sólo orienta al agente).
_CHARS_PER_TOKEN = 4


def headline_of(content: str | None) -> str:
    """Primeros ``HEADLINE_CHARS`` caracteres, con los espacios colapsados."""
    return " ".join(str(content or "").split())[:HEADLINE_CHARS]


def estimate_tokens(text: str | None) -> int:
    """Estimación de tokens del texto: ``ceil(len / 4)``; 0 si está vacío."""
    n = len(text or "")
    return -(-n // _CHARS_PER_TOKEN)


def index_hit(
    *,
    memory_id: UUID,
    content: str,
    type_: str,
    kind: str | None,
    scope: str,
    created_at: datetime | None,
    agent: str | None,
    project: str | None,
    score: float,
) -> dict[str, Any]:
    """El hit del índice: ``{id, headline, type, kind, scope, created_at, agent,
    project, score, tokens}``. ``tokens`` estima el DETALLE, no el titular."""
    return {
        "id": memory_id,
        "headline": headline_of(content),
        "type": type_,
        "kind": kind,
        "scope": scope,
        "created_at": created_at,
        "agent": agent,
        "project": project,
        "score": score,
        "tokens": estimate_tokens(content),
    }


__all__ = ["HEADLINE_CHARS", "estimate_tokens", "headline_of", "index_hit"]
