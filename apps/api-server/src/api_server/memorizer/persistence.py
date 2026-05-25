"""Persist :class:`MemoryCandidate` instances as `MemoryEntry` rows
(Plan 04 task_04_03).

This is the only place that knows how to map an agent's scope to the
owner pointer trio (`user_id` / `team_id` / `project_id`). The CHECK
constraint ``ck_memory_entries_scope_pointer`` (migration 0020) makes
the DB the final arbiter — if our mapping is wrong, the insert fails
loudly. Tests cover every scope branch.

The embedding column is left NULL on insert. Task_04_14 will back-fill
it once the Ollama embedding provider lands; the column is nullable
exactly so a write here never blocks on the embedder.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import MemoryScope
from api_server.db.memory import MemoryEntry
from api_server.memorizer.distillation import MemoryCandidate

logger = structlog.get_logger(__name__)


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
    extra_metadata: Mapping[str, Any] | None = None,
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
            distilled from (NULL on human-curated memories).
        extra_metadata: Anything to merge into each row's `metadata`
            JSONB (e.g. distillation model id, cost in USD). Tags
            stay on the candidate side; this column carries the
            "how was this produced" metadata.

    Returns the newly-added (not yet flushed) `MemoryEntry`
    instances.
    """
    if not candidates:
        return []

    owner = _owner_kwargs(scope, user_id=user_id, team_id=team_id, project_id=project_id)
    metadata_base: dict[str, Any] = dict(extra_metadata or {})

    rows: list[MemoryEntry] = []
    for cand in candidates:
        row = MemoryEntry(
            tenant_id=tenant_id,
            scope=scope,
            type=cand.type,
            content=cand.content,
            agent_id=agent_id,
            source_execution_id=source_execution_id,
            tags=list(cand.tags),
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
    )
    return rows


__all__ = ["persist_memory_candidates"]
