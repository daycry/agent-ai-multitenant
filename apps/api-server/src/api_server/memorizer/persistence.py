"""Persist :class:`MemoryCandidate` instances as `MemoryEntry` rows
(Plan 04 task_04_03).

This is the only place that knows how to map an agent's scope to the
owner pointer trio (`user_id` / `team_id` / `project_id`). The CHECK
constraint ``ck_memory_entries_scope_pointer`` (migration 0020) makes
the DB the final arbiter — if our mapping is wrong, the insert fails
loudly. Tests cover every scope branch.

El embedding se rellena EN EL MOMENTO DE CREAR cuando el caller pasa un
``embedder`` (Plan 06.17 task_06_17_03): así el recall vectorial y los
"similares" funcionan sin esperar al back-fill. La columna sigue siendo
nullable y el embed es BEST-EFFORT — si el embedder falla (Ollama caído) o no
se pasa, la fila nace con ``embedding=NULL`` y el worker dedicado de back-fill
(``workers.backfill_memory_embeddings``) la rellena después, idempotente. Un
write de memoria NUNCA se bloquea por el embedder.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import MemoryScope
from api_server.db.memory import MemoryEntry
from api_server.ingestion.embeddings import Embedder, EmbeddingError
from api_server.memorizer.distillation import MemoryCandidate

logger = structlog.get_logger(__name__)


async def count_memories_for_source(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    source_execution_id: UUID | None = None,
    source_human_work_session_id: UUID | None = None,
) -> int:
    """Count live (non-deleted) memories already persisted for one source.

    Used by the memorizer as an idempotency guard before re-distilling: with
    ``task_acks_late=True`` global a broker redelivery / worker crash re-runs the
    memorize task, and re-distilling would make a fresh (non-deterministic) LLM
    call and persist duplicate rows. The worker is BYPASSRLS, so ``tenant_id`` is
    filtered explicitly (defence in depth on top of the source filter)."""
    stmt = (
        select(func.count())
        .select_from(MemoryEntry)
        .where(MemoryEntry.tenant_id == tenant_id, MemoryEntry.deleted_at.is_(None))
    )
    if source_execution_id is not None:
        stmt = stmt.where(MemoryEntry.source_execution_id == source_execution_id)
    if source_human_work_session_id is not None:
        stmt = stmt.where(MemoryEntry.source_human_work_session_id == source_human_work_session_id)
    return int((await session.execute(stmt)).scalar_one())


async def _embed_contents(
    embedder: Embedder | None, contents: Sequence[str]
) -> list[list[float] | None]:
    """Embebe ``contents`` con ``embedder`` (best-effort).

    Devuelve una lista alineada con ``contents``: el vector por contenido, o
    ``None`` cuando no hay embedder o el embed falla. Nunca lanza — un fallo del
    embedder no debe abortar la persistencia de la memoria (BM25 sigue
    funcionando y el back-fill rellenará los NULL más tarde)."""
    if embedder is None or not contents:
        return [None] * len(contents)
    try:
        vectors = await embedder.embed(list(contents))
    except EmbeddingError as exc:
        logger.warning("memorizer.embed_failed", error=str(exc), count=len(contents))
        return [None] * len(contents)
    if len(vectors) != len(contents):
        logger.warning(
            "memorizer.embed_count_mismatch",
            expected=len(contents),
            got=len(vectors),
        )
        return [None] * len(contents)
    return [list(v) for v in vectors]


def _owner_kwargs(
    scope: str,
    *,
    user_id: UUID | None,
    team_id: UUID | None,
    project_id: UUID | None,
) -> dict[str, UUID | None]:
    """Map the scope to which owner pointer must be set.

    Raises :class:`ValueError` if the caller didn't supply the owner
    the scope needs — the DB CHECK would catch it anyway, but we'd
    rather fail before we open a transaction."""
    if scope == MemoryScope.PRIVATE.value:
        if user_id is None:
            raise ValueError("scope='private' requires user_id")
        return {"user_id": user_id, "team_id": None, "project_id": None}
    if scope == MemoryScope.TEAM_SHARED.value:
        if team_id is None:
            raise ValueError("scope='team_shared' requires team_id")
        return {"user_id": None, "team_id": team_id, "project_id": None}
    if scope == MemoryScope.PROJECT_SHARED.value:
        if project_id is None:
            raise ValueError("scope='project_shared' requires project_id")
        return {"user_id": None, "team_id": None, "project_id": project_id}
    if scope == MemoryScope.GLOBAL.value:
        return {"user_id": None, "team_id": None, "project_id": None}
    raise ValueError(f"unknown memory scope {scope!r}")


async def persist_memory_candidates(
    session: AsyncSession,
    candidates: Sequence[MemoryCandidate],
    *,
    tenant_id: UUID,
    scope: str,
    agent_id: UUID | None = None,
    user_id: UUID | None = None,
    team_id: UUID | None = None,
    project_id: UUID | None = None,
    source_execution_id: UUID | None = None,
    source_human_work_session_id: UUID | None = None,
    extra_metadata: Mapping[str, Any] | None = None,
    embedder: Embedder | None = None,
) -> list[MemoryEntry]:
    """Write `candidates` as `MemoryEntry` rows and return them.

    Args:
        session: An :class:`AsyncSession` already scoped to the
            tenant (so RLS is honoured). The caller flushes /
            commits; we only `session.add()`.
        candidates: Output of :func:`distil_execution`.
        tenant_id: Active tenant.
        scope: One of the four `MemoryScope` values.
        agent_id: Author agent (optional but recommended).
        user_id / team_id / project_id: Owner pointers. The one
            required by `scope` must be set.
        source_execution_id: Back-link to the `Execution` we
            distilled from (NULL on human-curated / human-session memories).
        source_human_work_session_id: Back-link to the `HumanWorkSession`
            we distilled from (Plan 16 task_16_15). Mutually exclusive with
            `source_execution_id` (DB CHECK ck_memory_entries_single_source).
        extra_metadata: Anything to merge into each row's `metadata`
            JSONB (e.g. distillation model id, cost in USD). Tags
            stay on the candidate side; this column carries the
            "how was this produced" metadata.
        embedder: opcional (Plan 06.17 task_06_17_03). Cuando se pasa, el
            contenido se embebe EN EL MOMENTO DE CREAR para que el recall
            vectorial / "similares" funcionen sin esperar al back-fill. Es
            best-effort: si el embed falla, la fila nace con ``embedding=NULL``
            (el back-fill la rellena luego) y el write no se bloquea.

    Returns the newly-added (not yet flushed) `MemoryEntry`
    instances.
    """
    if not candidates:
        return []

    # A memory cites at most one source — Execution XOR HumanWorkSession. The
    # DB CHECK (ck_memory_entries_single_source) is the final arbiter; fail
    # early with a clear error before we open a transaction.
    if source_execution_id is not None and source_human_work_session_id is not None:
        raise ValueError(
            "a memory cites at most one source: pass source_execution_id OR "
            "source_human_work_session_id, not both"
        )

    owner = _owner_kwargs(scope, user_id=user_id, team_id=team_id, project_id=project_id)
    metadata_base: dict[str, Any] = dict(extra_metadata or {})

    # Embebe el contenido en el momento de crear (best-effort) cuando hay
    # embedder; si falla / no se pasa, queda NULL y el back-fill lo rellena.
    embeddings = await _embed_contents(embedder, [c.content for c in candidates])

    rows: list[MemoryEntry] = []
    for cand, embedding in zip(candidates, embeddings, strict=True):
        row = MemoryEntry(
            tenant_id=tenant_id,
            scope=scope,
            type=cand.type,
            content=cand.content,
            embedding=embedding,
            agent_id=agent_id,
            source_execution_id=source_execution_id,
            source_human_work_session_id=source_human_work_session_id,
            tags=list(cand.tags),
            entities=list(cand.entities),
            metadata_={**metadata_base, "tags": list(cand.tags)},
            **owner,
        )
        session.add(row)
        rows.append(row)

    logger.info(
        "memorizer.persisted",
        tenant_id=str(tenant_id),
        scope=scope,
        count=len(rows),
        source_execution_id=str(source_execution_id) if source_execution_id else None,
        source_human_work_session_id=(
            str(source_human_work_session_id) if source_human_work_session_id else None
        ),
    )
    return rows


__all__ = ["persist_memory_candidates"]
