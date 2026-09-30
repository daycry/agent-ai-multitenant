"""Caducidad y compactación de memorias (task_mem_11). NUNCA borra: archiva con motivo.

Dos operaciones, las dos con fila de auditoría (``audit_log``) por memoria tocada:

  - :func:`archive_expired` archiva (soft: ``archived_at`` + ``archive_reason``) las
    ``episodic`` sin ``kind = summary``, con ``recall_count = 0`` y de más de N días
    (``memory.archive_after_days``, default 90). La antigüedad cuenta desde
    ``greatest(created_at, memory.usage_tracking_since)``: la migración 0151 dejó
    ``recall_count = 0`` en TODO lo anterior, así que nada previo al inicio de la medición
    del uso caduca hasta que pasen N días de medición real (default = 2026-09-30, la fecha
    de esa migración). Va en tandas hasta agotar, con un tope por ejecución. Las
    ``semantic`` y los resúmenes no caducan. Sólo toca lo que escriben los AGENTES
    (``team_shared`` / ``project_shared`` / ``global``): una ``private`` es de una persona
    (CLAUDE.md) o del córtex, que tiene su propio olvido (ADR 0077), y un barrido de
    plataforma no decide sobre ella.
  - :func:`propose_compactions` agrupa las memorias ``disputed`` vivas de un MISMO dueño
    (proyecto para ``project_shared``, equipo para ``team_shared``, el tenant para
    ``global``; NUNCA ``private``) en una revisión ``pending`` (a lo sumo una por dueño).
    Una propuesta pendiente NO se modifica: es el conjunto que el admin está mirando, y lo
    nuevo espera a una propuesta nueva tras su decisión. No aplica nada: un
    ``tenant_admin`` la confirma (:func:`confirm_compaction`, que archiva EXACTAMENTE el
    conjunto que vio: si difiere, 409) o la rechaza (:func:`reject_compaction`, que limpia
    ``disputed_at`` y da sus votos por revisados: sólo votos posteriores la vuelven a
    disputar).

Las consultas de los jobs corren con la sesión BYPASSRLS del worker, así que cada
``UPDATE``/``INSERT`` lleva ``tenant_id`` explícito; las de la API van con la sesión del
tenant (RLS) y también con ``tenant_id``.

El archivado NO borra nada: el recall (``recall.py``), ``memory_get`` y la línea de tiempo
ignoran lo archivado, pero ``GET /memories?archived=true`` lo enseña.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.audit import write_audit_log
from api_server.db.models import AuditLog
from api_server.memorizer.timeline import headline

logger = structlog.get_logger(__name__)

REASON_EXPIRED = "expired_unused"
REASON_DISPUTED_COMPACTED = "disputed_compacted"

#: Ámbitos que barre el job: lo que escriben los agentes. `private` queda fuera.
ARCHIVABLE_SCOPES: tuple[str, ...] = ("team_shared", "project_shared", "global")

#: Filas archivadas por tanda (una sentencia, con su auditoría en bloque).
ARCHIVE_BATCH_LIMIT = 1000

#: Tope TOTAL por ejecución del job: a partir de aquí se para y se loguea el atraso (el
#: resto cae en la ejecución del día siguiente).
ARCHIVE_MAX_PER_RUN = 20_000

#: Sin suelo de medición (tests, llamadores puros): todo cuenta desde su ``created_at``.
_NO_USAGE_FLOOR = datetime(1970, 1, 1, tzinfo=UTC)
_SCOPES_SQL = ", ".join(f"'{scope}'" for scope in ARCHIVABLE_SCOPES)

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
    #: Antigüedad EFECTIVA (ver :func:`effective_age_days`), no la de ``created_at``.
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


def effective_age_days(created_at: datetime, *, usage_since: datetime, now: datetime) -> float:
    """Antigüedad que cuenta la caducidad: desde ``greatest(created_at, usage_since)``."""
    return (now - max(created_at, usage_since)).total_seconds() / 86400


#: Los literales (no ``ANY(:scopes)``) permiten al planificador demostrar que la consulta
#: implica el predicado del índice parcial ``ix_memory_entries_archive_candidates`` (0154).
#: ``created_at < cutoff AND :since < cutoff`` es ``greatest(created_at, :since) < cutoff``.
_ARCHIVE_SQL_WHERE = (
    " deleted_at IS NULL"
    " AND archived_at IS NULL"
    " AND type = 'episodic'"
    " AND kind IS DISTINCT FROM 'summary'"
    f" AND scope IN ({_SCOPES_SQL})"
    " AND COALESCE(metadata ->> 'cortex', '') <> 'true'"
    " AND recall_count = 0"
    " AND created_at < now() - make_interval(days => CAST(:days AS integer))"
    " AND CAST(:since AS timestamptz) < now() - make_interval(days => CAST(:days AS integer))"
)


async def _audit_archived(
    session: AsyncSession,
    rows: Sequence[tuple[UUID, UUID]],
    *,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Una fila de ``audit_log`` por memoria, insertadas EN BLOQUE (no sentencia a sentencia)."""
    if not rows:
        return
    changes = {"reason": reason, **(extra or {})}
    await session.execute(
        insert(AuditLog),
        [
            {
                "id": uuid7(),
                "tenant_id": tenant_id,
                "user_id": None,
                "action": "memory.archived",
                "resource_type": "memory_entry",
                "resource_id": memory_id,
                "changes": changes,
            }
            for memory_id, tenant_id in rows
        ],
    )


async def archive_expired(
    session: AsyncSession,
    *,
    older_than_days: int,
    usage_since: datetime | None = None,
    limit: int = ARCHIVE_BATCH_LIMIT,
    max_total: int = ARCHIVE_MAX_PER_RUN,
) -> int:
    """Archiva las ``episodic`` caducadas (ver el docstring del módulo). Devuelve cuántas.

    ``usage_since``: inicio de la medición del uso (``memory.usage_tracking_since``); la
    antigüedad cuenta desde ``greatest(created_at, usage_since)``. ``None`` = sin suelo.
    Procesa en tandas de ``limit`` hasta agotar o hasta ``max_total`` (y entonces loguea el
    atraso). Idempotente: una segunda pasada no vuelve a tocar lo archivado. ``FOR UPDATE
    SKIP LOCKED`` para que dos pasadas solapadas no se pisen. Cada memoria deja una fila de
    ``audit_log`` con la acción ``memory.archived`` y el motivo."""
    params: dict[str, Any] = {
        "days": int(older_than_days),
        "since": usage_since or _NO_USAGE_FLOOR,
    }
    total = 0
    capped = True
    while total < max_total:
        batch = min(int(limit), max_total - total)
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
            {**params, "limit": batch, "reason": REASON_EXPIRED},
        )
        archived = [(row[0], row[1]) for row in result.all()]
        await _audit_archived(
            session, archived, reason=REASON_EXPIRED, extra={"days": older_than_days}
        )
        total += len(archived)
        if len(archived) < batch:
            capped = False
            break
    if capped:
        backlog = (
            await session.execute(
                text(
                    "SELECT count(*) FROM (SELECT 1 FROM memory_entries"
                    f" WHERE {_ARCHIVE_SQL_WHERE} LIMIT 100001) t"
                ),
                params,
            )
        ).scalar_one()
        if backlog:
            logger.warning(
                "memory_lifecycle.archive_backlog", archived=total, remaining_at_least=backlog
            )
    if total:
        logger.info("memory_lifecycle.archived", count=total, reason=REASON_EXPIRED)
    return total


def _proposal_summary(rows: Sequence[tuple[UUID, str]]) -> str:
    """Un titular por memoria (id corto + primeros caracteres); acotado."""
    lines = [
        f"- [mem:{str(mid)[:8]}] {headline(content, _PROPOSAL_HEADLINE_CHARS)}"
        for mid, content in rows[:_PROPOSAL_MAX_ITEMS]
    ]
    if len(rows) > _PROPOSAL_MAX_ITEMS:
        lines.append(f"- … y {len(rows) - _PROPOSAL_MAX_ITEMS} más")
    return "\n".join(lines)


#: La llave de un dueño de propuesta: (tenant, ámbito, proyecto, equipo). ``global`` no
#: lleva puntero; ``private`` no se propone nunca.
_OwnerKey = tuple[UUID, str, UUID | None, UUID | None]


async def propose_compactions(session: AsyncSession) -> int:
    """Crea una revisión ``pending`` por (tenant, ámbito, dueño) con memorias ``disputed``.
    No archiva nada. Devuelve cuántas propuestas NUEVAS creó.

    Agrupa ``project_shared`` por proyecto, ``team_shared`` por equipo y ``global`` por
    tenant; ``private`` se excluye explícitamente (``scope <> 'private'``, además del
    puntero: es de una persona). Se ignoran las archivadas / borradas y las que una
    propuesta ``rejected`` ya dio por buenas (salvo que un voto POSTERIOR al rechazo las
    haya vuelto a disputar).

    Concurrencia: primero se BLOQUEAN las propuestas pendientes (un ``reject``/``confirm``
    en vuelo termina antes) y sólo después se lee el estado de las memorias, de modo que lo
    recién rechazado o archivado no se re-propone. Una propuesta pendiente no se toca: es el
    conjunto que el admin mira; lo nuevo espera a una propuesta nueva tras su decisión. La
    pendiente sin ninguna memoria viva se elimina, con fila de ``audit_log``."""
    pending_rows = (
        await session.execute(
            text(
                "SELECT id, tenant_id, scope, project_id, team_id, memory_ids"
                " FROM memory_compaction_proposals WHERE status = 'pending'"
                " ORDER BY id FOR UPDATE"
            )
        )
    ).all()
    rows = (
        await session.execute(
            text(
                "SELECT m.tenant_id, m.scope, m.project_id, m.team_id, m.id, m.content"
                " FROM memory_entries m"
                " WHERE m.deleted_at IS NULL AND m.archived_at IS NULL"
                "   AND m.disputed_at IS NOT NULL AND m.scope <> 'private'"
                "   AND COALESCE(m.metadata ->> 'cortex', '') <> 'true'"
                "   AND ((m.scope = 'project_shared' AND m.project_id IS NOT NULL)"
                "     OR (m.scope = 'team_shared' AND m.team_id IS NOT NULL)"
                "     OR m.scope = 'global')"
                "   AND NOT EXISTS ("
                "     SELECT 1 FROM memory_compaction_proposals p"
                "     WHERE p.tenant_id = m.tenant_id AND p.status = 'rejected'"
                "       AND p.decided_at >= m.disputed_at"
                "       AND p.memory_ids ? CAST(m.id AS text))"
                " ORDER BY m.tenant_id, m.scope, m.project_id, m.team_id, m.created_at, m.id"
            )
        )
    ).all()
    groups: dict[_OwnerKey, list[tuple[UUID, str]]] = {}
    for tenant_id, scope, project_id, team_id, memory_id, content in rows:
        # El puntero que no toca al ámbito no cuenta (un `global` no tiene dueño).
        key: _OwnerKey = (
            tenant_id,
            scope,
            project_id if scope == "project_shared" else None,
            team_id if scope == "team_shared" else None,
        )
        groups.setdefault(key, []).append((memory_id, content))

    pending_keys: set[_OwnerKey] = set()
    for pid, tenant_id, scope, project_id, team_id, memory_ids in pending_rows:
        key = (tenant_id, scope, project_id, team_id)
        live = {str(mid) for mid, _ in groups.get(key, [])}
        if not ({str(i) for i in memory_ids} & live):
            # Sin ninguna memoria disputada viva ya no tiene objeto: se cierra sin decisión.
            await session.execute(
                text("DELETE FROM memory_compaction_proposals WHERE id = :id AND tenant_id = :t"),
                {"id": pid, "t": tenant_id},
            )
            await write_audit_log(
                session,
                action="memory.compaction_orphan_deleted",
                actor_user_id=None,
                tenant_id=tenant_id,
                resource_type="memory_compaction_proposal",
                resource_id=pid,
                changes={"scope": scope, "memories": len(memory_ids)},
            )
        else:
            pending_keys.add(key)

    touched = 0
    for key, members in groups.items():
        if len(members) < MIN_DISPUTED_PER_PROPOSAL or key in pending_keys:
            continue
        tenant_id, scope, project_id, team_id = key
        created = await session.execute(
            text(
                "INSERT INTO memory_compaction_proposals"
                " (id, tenant_id, scope, project_id, team_id, status, memory_ids, summary)"
                " VALUES (gen_random_uuid(), :tenant_id, :scope, :project_id, :team_id,"
                "         'pending', CAST(:ids AS jsonb), :summary)"
                " ON CONFLICT DO NOTHING RETURNING id"
            ),
            {
                "tenant_id": tenant_id,
                "scope": scope,
                "project_id": project_id,
                "team_id": team_id,
                "ids": json.dumps([str(mid) for mid, _ in members]),
                "summary": _proposal_summary(members),
            },
        )
        if created.first() is not None:
            touched += 1
    if touched:
        logger.info("memory_lifecycle.compactions_proposed", proposals=touched)
    return touched


class CompactionNotFoundError(LookupError):
    """La propuesta no existe, es de otro tenant o ya no está ``pending``."""


class CompactionMismatchError(ValueError):
    """El conjunto que el admin vio no es el de la propuesta (409): se archiva lo que vio."""


@dataclass(frozen=True)
class _Pending:
    memory_ids: list[UUID]
    scope: str
    project_id: UUID | None
    team_id: UUID | None


async def _load_pending(session: AsyncSession, *, tenant_id: UUID, proposal_id: UUID) -> _Pending:
    row = (
        await session.execute(
            text(
                "SELECT memory_ids, scope, project_id, team_id"
                " FROM memory_compaction_proposals"
                " WHERE id = :id AND tenant_id = :tenant_id AND status = 'pending'"
                " FOR UPDATE"
            ),
            {"id": proposal_id, "tenant_id": tenant_id},
        )
    ).first()
    if row is None:
        raise CompactionNotFoundError(str(proposal_id))
    return _Pending([UUID(str(i)) for i in row[0]], row[1], row[2], row[3])


async def confirm_compaction(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    proposal_id: UUID,
    actor_user_id: UUID | None,
    expected_memory_ids: Collection[UUID] | None = None,
) -> int:
    """Aplica la revisión: archiva sus memorias (motivo ``disputed_compacted``), con
    auditoría por memoria y una fila ``memory.compaction_confirmed`` de la propuesta.
    Devuelve cuántas archivó. Sólo las que siguen vivas, del dueño y disputadas.

    ``expected_memory_ids`` es el conjunto que el admin VIO al confirmar: si difiere del de
    la propuesta (aunque sea el orden, no; el contenido sí) lanza
    :class:`CompactionMismatchError` y no archiva nada."""
    pending = await _load_pending(session, tenant_id=tenant_id, proposal_id=proposal_id)
    if expected_memory_ids is not None and set(expected_memory_ids) != set(pending.memory_ids):
        raise CompactionMismatchError(str(proposal_id))
    result = await session.execute(
        text(
            "UPDATE memory_entries SET archived_at = now(), archive_reason = :reason"
            " WHERE tenant_id = :tenant_id AND scope = :scope AND scope <> 'private'"
            "   AND project_id IS NOT DISTINCT FROM :project_id"
            "   AND team_id IS NOT DISTINCT FROM :team_id"
            "   AND id = ANY(:ids) AND deleted_at IS NULL AND archived_at IS NULL"
            "   AND disputed_at IS NOT NULL"
            " RETURNING id, tenant_id"
        ),
        {
            "reason": REASON_DISPUTED_COMPACTED,
            "tenant_id": tenant_id,
            "scope": pending.scope,
            "project_id": pending.project_id,
            "team_id": pending.team_id,
            "ids": pending.memory_ids,
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
        changes={
            "archived": len(archived),
            "scope": pending.scope,
            "project_id": str(pending.project_id) if pending.project_id else None,
            "team_id": str(pending.team_id) if pending.team_id else None,
        },
    )
    return len(archived)


async def reject_compaction(
    session: AsyncSession, *, tenant_id: UUID, proposal_id: UUID, actor_user_id: UUID | None
) -> None:
    """Descarta la revisión: las memorias se quedan (vivas) pero YA NO disputadas, sus votos
    negativos pasan a revisados y sólo votos posteriores pueden volver a disputarlas. Deja
    fila de auditoría."""
    pending = await _load_pending(session, tenant_id=tenant_id, proposal_id=proposal_id)
    await session.execute(
        text(
            "UPDATE memory_compaction_proposals SET status = 'rejected', decided_at = now(),"
            " decided_by = :actor, updated_at = now()"
            " WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"actor": actor_user_id, "id": proposal_id, "tenant_id": tenant_id},
    )
    cleared = await session.execute(
        text(
            "UPDATE memory_entries SET disputed_at = NULL"
            " WHERE tenant_id = :tenant_id AND id = ANY(:ids) AND scope <> 'private'"
            "   AND disputed_at IS NOT NULL AND archived_at IS NULL"
            " RETURNING id"
        ),
        {"tenant_id": tenant_id, "ids": pending.memory_ids},
    )
    cleared_count = len(cleared.all())
    await session.execute(
        text(
            "UPDATE memory_feedback SET reviewed_at = now()"
            " WHERE tenant_id = :tenant_id AND memory_id = ANY(:ids)"
            "   AND verdict IN ('wrong', 'outdated') AND reviewed_at IS NULL"
        ),
        {"tenant_id": tenant_id, "ids": pending.memory_ids},
    )
    await write_audit_log(
        session,
        action="memory.compaction_rejected",
        actor_user_id=actor_user_id,
        tenant_id=tenant_id,
        resource_type="memory_compaction_proposal",
        resource_id=proposal_id,
        changes={
            "scope": pending.scope,
            "project_id": str(pending.project_id) if pending.project_id else None,
            "team_id": str(pending.team_id) if pending.team_id else None,
            "disputes_cleared": cleared_count,
        },
    )


__all__ = [
    "ARCHIVABLE_SCOPES",
    "ARCHIVE_BATCH_LIMIT",
    "ARCHIVE_MAX_PER_RUN",
    "MIN_DISPUTED_PER_PROPOSAL",
    "REASON_DISPUTED_COMPACTED",
    "REASON_EXPIRED",
    "ArchiveCandidate",
    "CompactionMismatchError",
    "CompactionNotFoundError",
    "archive_expired",
    "confirm_compaction",
    "effective_age_days",
    "propose_compactions",
    "reject_compaction",
    "should_archive",
]
