"""Cascada de borrado de KB (G-03).

`delete_kb` solo soft-borraba la fila de la KB, dejando sus documentos vivos
bajo una KB muerta: invisibles para el recall (join por `deleted_at` de la KB)
pero eternos, sin blobs ni chunks reclamados. Esta cascada soft-borra también
los documentos de la KB para que el GC (`workers.maintenance.knowledge_gc`)
los recoja y libere chunks + blobs una vez vencida la retención.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession


async def soft_delete_kb_cascade(session: AsyncSession, *, kb_id: UUID) -> int:
    """Soft-borra los documentos VIVOS de la KB; devuelve cuántos marcó.

    Idempotente (solo toca `deleted_at IS NULL`). El caller es responsable de
    soft-borrar la fila de la KB en sí y de poseer la transacción."""
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            text(
                "UPDATE documents SET deleted_at = now() WHERE kb_id = :kb AND deleted_at IS NULL"
            ),
            {"kb": kb_id},
        ),
    )
    return int(result.rowcount or 0)


__all__ = ["soft_delete_kb_cascade"]
