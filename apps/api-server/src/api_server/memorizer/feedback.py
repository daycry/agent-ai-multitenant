"""Feedback sobre una memoria (task_mem_10): `useful` | `wrong` | `outdated`.

Escribe una fila de ``memory_feedback`` por (memoria, votante) y recalcula desde ahí
``useful_count`` / ``harmful_count`` de la memoria. Para ``wrong`` / ``outdated`` la marca
como ``disputed`` (``disputed_at``) SIN borrarla: el ranking la baja
(:mod:`api_server.memorizer.ranking`) y la compactación la propone al operador
(:mod:`api_server.memorizer.lifecycle`).

**Feedback envenenado (riesgo del plan).** Un agente no puede inflar ni hundir una memoria
repitiendo su voto: el votante es la ejecución en marcha (o, sin ella, el agente) y repetir
el veredicto reescribe la MISMA fila; cambiar de opinión también. Y sólo pesa dentro del
ámbito y del tenant del votante: el llamador (el endpoint interno) verifica antes con
``get_memories`` que la memoria es legible para el agente; aquí toda consulta lleva
``tenant_id`` además de RLS.

``disputed_at`` se limpia cuando ya no queda ningún voto negativo (el único origen de la
disputa son los votos). Una memoria ya archivada no se toca.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Verdict = Literal["useful", "wrong", "outdated"]
VERDICTS: tuple[str, ...] = ("useful", "wrong", "outdated")
NEGATIVE_VERDICTS: frozenset[str] = frozenset({"wrong", "outdated"})

#: Largo máximo de la nota que se guarda con el voto.
NOTE_MAX_CHARS = 500


@dataclass(frozen=True)
class FeedbackResult:
    memory_id: UUID
    verdict: str
    useful_count: int
    harmful_count: int
    disputed: bool


def voter_key(
    *, agent_id: UUID | None, execution_id: UUID | None, user_id: UUID | None = None
) -> str:
    """La identidad del voto: la persona si vota un humano (`task_mem_20`), si no la
    ejecución en marcha y, sin ella, el agente."""
    if user_id is not None:
        return f"user:{user_id}"
    if execution_id is not None:
        return f"exec:{execution_id}"
    if agent_id is not None:
        return f"agent:{agent_id}"
    return "anonymous"


def normalize_note(note: str | None) -> str | None:
    """La nota recortada y acotada; vacía = ``None``."""
    flat = (note or "").strip()
    return flat[:NOTE_MAX_CHARS] if flat else None


async def apply_feedback(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    memory_id: UUID,
    verdict: str,
    note: str | None,
    agent_id: UUID | None,
    execution_id: UUID | None,
    user_id: UUID | None = None,
) -> FeedbackResult | None:
    """Registra el voto y recalcula contadores y `disputed`. ``None`` si la memoria no
    existe, es de otro tenant, está borrada o archivada (el llamador responde 404).

    ``user_id``: el votante humano (`POST /memories/{id}/feedback`); un voto por persona."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    exists = await session.execute(
        text(
            "SELECT 1 FROM memory_entries WHERE id = :id AND tenant_id = :tenant_id"
            " AND deleted_at IS NULL AND archived_at IS NULL FOR UPDATE"
        ),
        {"id": memory_id, "tenant_id": tenant_id},
    )
    if exists.first() is None:
        return None

    await session.execute(
        text(
            "INSERT INTO memory_feedback"
            " (id, tenant_id, memory_id, voter_key, agent_id, execution_id, verdict, note)"
            " VALUES (gen_random_uuid(), :tenant_id, :memory_id, :voter, :agent_id,"
            "         :execution_id, :verdict, :note)"
            " ON CONFLICT (tenant_id, memory_id, voter_key)"
            " DO UPDATE SET verdict = EXCLUDED.verdict, note = EXCLUDED.note,"
            "               updated_at = now()"
        ),
        {
            "tenant_id": tenant_id,
            "memory_id": memory_id,
            "voter": voter_key(agent_id=agent_id, execution_id=execution_id, user_id=user_id),
            "agent_id": agent_id,
            "execution_id": execution_id,
            "verdict": verdict,
            "note": normalize_note(note),
        },
    )
    row = (
        await session.execute(
            text(
                "UPDATE memory_entries m SET"
                "  useful_count = c.useful,"
                "  harmful_count = c.harmful,"
                "  disputed_at = CASE WHEN c.harmful > 0"
                "                     THEN COALESCE(m.disputed_at, now()) ELSE NULL END"
                " FROM ("
                "  SELECT count(*) FILTER (WHERE verdict = 'useful') AS useful,"
                "         count(*) FILTER (WHERE verdict IN ('wrong', 'outdated')) AS harmful"
                "  FROM memory_feedback"
                "  WHERE tenant_id = :tenant_id AND memory_id = :memory_id"
                " ) c"
                " WHERE m.id = :memory_id AND m.tenant_id = :tenant_id"
                " RETURNING m.useful_count, m.harmful_count, m.disputed_at"
            ),
            {"tenant_id": tenant_id, "memory_id": memory_id},
        )
    ).one()
    return FeedbackResult(
        memory_id=memory_id,
        verdict=verdict,
        useful_count=int(row[0]),
        harmful_count=int(row[1]),
        disputed=row[2] is not None,
    )


__all__ = [
    "NEGATIVE_VERDICTS",
    "NOTE_MAX_CHARS",
    "VERDICTS",
    "FeedbackResult",
    "Verdict",
    "apply_feedback",
    "normalize_note",
    "voter_key",
]
