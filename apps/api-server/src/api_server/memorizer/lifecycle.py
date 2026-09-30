"""Caducidad y compactación de memorias (task_mem_11). NUNCA borra: archiva con motivo.

Dos operaciones, las dos con fila de auditoría (``audit_log``) por memoria tocada:

  - :func:`archive_expired` archiva (soft: ``archived_at`` + ``archive_reason``) las
    ``episodic`` sin ``kind = summary``, con ``recall_count = 0`` y de más de N días
    (``memory.archive_after_days``, default 90). Las ``semantic`` y los resúmenes no
    caducan. Sólo toca lo que escriben los AGENTES (``team_shared`` / ``project_shared``
    / ``global``): una ``private`` es de una persona (CLAUDE.md) o del córtex, que tiene
    su propio olvido (ADR 0077), y un barrido de plataforma no decide sobre ella.
  - :func:`propose_compactions` agrupa las memorias ``disputed`` vivas de un MISMO
    proyecto en una revisión ``pending`` (a lo sumo una por proyecto: se refresca). No
    aplica nada: un ``tenant_admin`` la confirma (:func:`confirm_compaction`, que archiva
    las memorias con motivo ``disputed_compacted``) o la rechaza
    (:func:`reject_compaction`, que las deja como están y evita re-proponerlas).

Las consultas de los jobs corren con la sesión BYPASSRLS del worker, así que cada
``UPDATE``/``INSERT`` lleva ``tenant_id`` explícito; las de la API van con la sesión del
tenant (RLS) y también con ``tenant_id``.

El archivado NO borra nada: el recall (``recall.py``), ``memory_get`` y la línea de tiempo
ignoran lo archivado, pero ``GET /memories?archived=true`` lo enseña.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import structlog
from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.audit import write_audit_log
from api_server.memorizer.timeline import headline

logger = structlog.get_logger(__name__)

REASON_EXPIRED = "expired_unused"
REASON_DISPUTED_COMPACTED = "disputed_compacted"

#: Ámbitos que barre el job: lo que escriben los agentes. `private` queda fuera.
ARCHIVABLE_SCOPES: tuple[str, ...] = ("team_shared", "project_shared", "global")

#: Tope de filas archivadas por pasada (el resto cae en la siguiente).
ARCHIVE_BATCH_LIMIT = 1000

#: Cuántas `disputed` de un proyecto hacen falta para proponer una revisión.
MIN_DISPUTED_PER_PROPOSAL = 1

#: Largo del titular de cada memoria en el resumen de la propuesta.
_PROPOSAL_HEADLINE_CHARS = 160
_PROPOSAL_MAX_ITEMS = 100


@dataclass(frozen=True)
class ArchiveCandidate:
    """Lo que la regla de caducidad mira de una memoria."""

    type: str
    kind: str | None
    scope: str
    recall_count: int
    age_days: float
    archived: bool = False
    deleted: bool = False
    cortex: bool = False


def should_archive(candidate: ArchiveCandidate, *, older_than_days: int) -> bool:
    """La regla de selección del job, pura. ESPEJO de :data:`_ARCHIVE_SQL_WHERE`; los dos
    los fija ``tests/integration/test_memory_lifecycle.py`` con los mismos casos."""
    return (
        not candidate.archived
        and not candidate.deleted
        and not candidate.cortex
        and candidate.type == "episodic"
        and candidate.kind != "summary"
        and candidate.scope in ARCHIVABLE_SCOPES
        and candidate.recall_count == 0
        and candidate.age_days > older_than_days
    )


_ARCHIVE_SQL_WHERE = (
    " deleted_at IS NULL"
    " AND archived_at IS NULL"
    " AND type = 'episodic'"
    " AND kind IS DISTINCT FROM 'summary'"
    " AND scope = ANY(:scopes)"
    " AND COALESCE(metadata ->> 'cortex', '') <> 'true'"
    " AND recall_count = 0"
    " AND created_at < now() - make_interval(days => CAST(:days AS integer))"
)


async def _audit_archived(
    session: AsyncSession,
    rows: Sequence[tuple[UUID, UUID]],
    *,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> None:
    for memory_id, tenant_id in rows:
        await write_audit_log(
            session,
            action="memory.archived",
            actor_user_id=None,
            tenant_id=tenant_id,
            resource_type="memory_entry",
            resource_id=memory_id,
            changes={"reason": reason, **(extra or {})},
        )


async def archive_expired(
    session: AsyncSession, *, older_than_days: int, limit: int = ARCHIVE_BATCH_LIMIT
) -> int:
    """Archiva las ``episodic`` caducadas (ver el docstring del módulo). Devuelve cuántas.

    Idempotente: una segunda pasada no vuelve a tocar lo archivado. ``FOR UPDATE SKIP
    LOCKED`` para que dos pasadas solapadas no se pisen. Cada memoria deja una fila de
    ``audit_log`` con la acción ``memory.archived`` y el motivo."""
    result = await session.execute(
        text(
            "WITH picked AS ("
            "  SELECT id, tenant_id FROM memory_entries"
            f"  WHERE {_ARCHIVE_SQL_WHERE}"
            "  ORDER BY created_at LIMIT :limit"
            "  FOR UPDATE SKIP LOCKED"
            ")"
            " UPDATE memory_entries m"
            " SET archived_at = now(), archive_reason = :reason"
            " FROM picked"
            " WHERE m.id = picked.id AND m.tenant_id = picked.tenant_id"
            " RETURNING m.id, m.tenant_id"
        ),
        {
            "scopes": list(ARCHIVABLE_SCOPES),
            "days": int(older_than_days),
            "limit": int(limit),
            "reason": REASON_EXPIRED,
        },
    )
    archived = [(row[0], row[1]) for row in result.all()]
    await _audit_archived(session, archived, reason=REASON_EXPIRED, extra={"days": older_than_days})
    if archived:
        logger.info("memory_lifecycle.archived", count=len(archived), reason=REASON_EXPIRED)
    return len(archived)


def _proposal_summary(rows: Sequence[tuple[UUID, str]]) -> str:
    """Un titular por memoria (id corto + primeros caracteres); acotado."""
    lines = [
        f"- [mem:{str(mid)[:8]}] {headline(content, _PROPOSAL_HEADLINE_CHARS)}"
        for mid, content in rows[:_PROPOSAL_MAX_ITEMS]
    ]
    if len(rows) > _PROPOSAL_MAX_ITEMS:
        lines.append(f"- … y {len(rows) - _PROPOSAL_MAX_ITEMS} más")
    return "\n".join(lines)


async def propose_compactions(session: AsyncSession) -> int:
    """Crea o refresca una revisión ``pending`` por (tenant, proyecto) con memorias
    ``disputed``. No archiva nada. Devuelve cuántas propuestas quedó tocando.

    Se ignoran las memorias de una propuesta ``rejected`` (el operador ya dijo que se
    quedan) y las archivadas / borradas. Una propuesta pendiente cuyo conjunto ya no
    existe (todas archivadas) se cierra sin decisión: se elimina, no queda colgada."""
    rows = (
        await session.execute(
            text(
                "SELECT m.tenant_id, m.project_id, m.id, m.content"
                " FROM memory_entries m"
                " WHERE m.deleted_at IS NULL AND m.archived_at IS NULL"
                "   AND m.disputed_at IS NOT NULL AND m.project_id IS NOT NULL"
                "   AND NOT EXISTS ("
                "     SELECT 1 FROM memory_compaction_proposals p"
                "     WHERE p.tenant_id = m.tenant_id AND p.status = 'rejected'"
                "       AND p.memory_ids ? CAST(m.id AS text))"
                " ORDER BY m.tenant_id, m.project_id, m.created_at, m.id"
            )
        )
    ).all()
    groups: dict[tuple[UUID, UUID], list[tuple[UUID, str]]] = {}
    for tenant_id, project_id, memory_id, content in rows:
        groups.setdefault((tenant_id, project_id), []).append((memory_id, content))

    touched = 0
    for (tenant_id, project_id), members in groups.items():
        if len(members) < MIN_DISPUTED_PER_PROPOSAL:
            continue
        ids = [str(mid) for mid, _ in members]
        summary = _proposal_summary(members)
        updated = cast(
            "CursorResult[Any]",
            await session.execute(
                text(
                    "UPDATE memory_compaction_proposals"
                    " SET memory_ids = CAST(:ids AS jsonb), summary = :summary, updated_at = now()"
                    " WHERE tenant_id = :tenant_id AND project_id = :project_id"
                    "   AND status = 'pending'"
                ),
                {
                    "ids": _json(ids),
                    "summary": summary,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                },
            ),
        )
        if not updated.rowcount:
            await session.execute(
                text(
                    "INSERT INTO memory_compaction_proposals"
                    " (id, tenant_id, project_id, status, memory_ids, summary)"
                    " VALUES (gen_random_uuid(), :tenant_id, :project_id, 'pending',"
                    "         CAST(:ids AS jsonb), :summary)"
                ),
                {
                    "ids": _json(ids),
                    "summary": summary,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                },
            )
        touched += 1

    # Una pendiente sin ninguna memoria disputada viva ya no tiene objeto.
    live_projects = [(t, p) for (t, p) in groups]
    stale = (
        await session.execute(
            text(
                "SELECT id, tenant_id, project_id FROM memory_compaction_proposals"
                " WHERE status = 'pending'"
            )
        )
    ).all()
    for pid, tenant_id, project_id in stale:
        if (tenant_id, project_id) not in live_projects:
            await session.execute(
                text("DELETE FROM memory_compaction_proposals WHERE id = :id AND tenant_id = :t"),
                {"id": pid, "t": tenant_id},
            )
    if touched:
        logger.info("memory_lifecycle.compactions_proposed", proposals=touched)
    return touched


def _json(value: Any) -> str:
    import json

    return json.dumps(value)


class CompactionNotFoundError(LookupError):
    """La propuesta no existe, es de otro tenant o ya no está ``pending``."""


async def _load_pending(
    session: AsyncSession, *, tenant_id: UUID, proposal_id: UUID
) -> tuple[list[UUID], UUID]:
    row = (
        await session.execute(
            text(
                "SELECT memory_ids, project_id FROM memory_compaction_proposals"
                " WHERE id = :id AND tenant_id = :tenant_id AND status = 'pending'"
                " FOR UPDATE"
            ),
            {"id": proposal_id, "tenant_id": tenant_id},
        )
    ).first()
    if row is None:
        raise CompactionNotFoundError(str(proposal_id))
    return [UUID(str(i)) for i in row[0]], row[1]


async def confirm_compaction(
    session: AsyncSession, *, tenant_id: UUID, proposal_id: UUID, actor_user_id: UUID | None
) -> int:
    """Aplica la revisión: archiva sus memorias (motivo ``disputed_compacted``), con
    auditoría por memoria y una fila ``memory.compaction_confirmed`` de la propuesta.
    Devuelve cuántas archivó. Sólo las que siguen vivas, del proyecto y disputadas."""
    memory_ids, project_id = await _load_pending(
        session, tenant_id=tenant_id, proposal_id=proposal_id
    )
    result = await session.execute(
        text(
            "UPDATE memory_entries SET archived_at = now(), archive_reason = :reason"
            " WHERE tenant_id = :tenant_id AND project_id = :project_id"
            "   AND id = ANY(:ids) AND deleted_at IS NULL AND archived_at IS NULL"
            "   AND disputed_at IS NOT NULL"
            " RETURNING id, tenant_id"
        ),
        {
            "reason": REASON_DISPUTED_COMPACTED,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "ids": memory_ids,
        },
    )
    archived = [(row[0], row[1]) for row in result.all()]
    await _audit_archived(
        session,
        archived,
        reason=REASON_DISPUTED_COMPACTED,
        extra={"proposal_id": str(proposal_id), "actor_user_id": str(actor_user_id)},
    )
    await session.execute(
        text(
            "UPDATE memory_compaction_proposals SET status = 'confirmed', decided_at = now(),"
            " decided_by = :actor, updated_at = now()"
            " WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"actor": actor_user_id, "id": proposal_id, "tenant_id": tenant_id},
    )
    await write_audit_log(
        session,
        action="memory.compaction_confirmed",
        actor_user_id=actor_user_id,
        tenant_id=tenant_id,
        resource_type="memory_compaction_proposal",
        resource_id=proposal_id,
        changes={"archived": len(archived), "project_id": str(project_id)},
    )
    return len(archived)


async def reject_compaction(
    session: AsyncSession, *, tenant_id: UUID, proposal_id: UUID, actor_user_id: UUID | None
) -> None:
    """Descarta la revisión: las memorias se quedan como están (siguen ``disputed``) y no
    se vuelven a proponer. Deja fila de auditoría."""
    _ids, project_id = await _load_pending(session, tenant_id=tenant_id, proposal_id=proposal_id)
    await session.execute(
        text(
            "UPDATE memory_compaction_proposals SET status = 'rejected', decided_at = now(),"
            " decided_by = :actor, updated_at = now()"
            " WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"actor": actor_user_id, "id": proposal_id, "tenant_id": tenant_id},
    )
    await write_audit_log(
        session,
        action="memory.compaction_rejected",
        actor_user_id=actor_user_id,
        tenant_id=tenant_id,
        resource_type="memory_compaction_proposal",
        resource_id=proposal_id,
        changes={"project_id": str(project_id)},
    )


__all__ = [
    "ARCHIVABLE_SCOPES",
    "ARCHIVE_BATCH_LIMIT",
    "MIN_DISPUTED_PER_PROPOSAL",
    "REASON_DISPUTED_COMPACTED",
    "REASON_EXPIRED",
    "ArchiveCandidate",
    "CompactionNotFoundError",
    "archive_expired",
    "confirm_compaction",
    "propose_compactions",
    "reject_compaction",
    "should_archive",
]
