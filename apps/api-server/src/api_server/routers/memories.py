"""`/memories` endpoints — human-facing memory CRUD (Plan 04 task_04_05).

Two routes for v1:

  - ``POST /memories`` — store a new `MemoryEntry` manually. The
    payload picks one of the four `MemoryScope` values; the owner
    pointer (`user_id` / `team_id` / `project_id`) is derived from
    the authenticated principal + request body. The DB CHECK is the
    last line of defence if the mapping ever drifts.
  - ``GET /memories`` — list memories visible under the current
    tenant + active scope filter. Lightweight pagination
    (limit + cursor by `created_at`); the recall tool covers
    search.

The agent-runtime tool wire-up (replacing the 501 placeholder Plan 02
left behind) is a separate change — it lives in `agent-runtime` and
calls the same persistence module the endpoint uses
(:func:`api_server.memorizer.persistence.persist_memory_candidates`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_member
from api_server.db.memory import MemoryEntry
from api_server.ingestion.embeddings import Embedder, EmbeddingError
from api_server.memorizer import MemoryCandidate, persist_memory_candidates
from api_server.routers._helpers import require_tenant_id
from api_server.routers.docs_viewer import get_query_embedder

router = APIRouter(prefix="/memories", tags=["memories"])

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

_SCOPE_LITERAL = Literal["private", "team_shared", "project_shared", "global"]
_TYPE_LITERAL = Literal["episodic", "semantic"]

# Owner-pointer column that identifies "who owns" a memory within a
# non-global scope. RLS only scopes rows by ``tenant_id``; two rows of
# the same tenant + same scope can still belong to *different* owners
# (project A vs project B, team X vs team Y, user U vs user V). Anything
# that crosses owners — merging or surfacing as a "similar" candidate —
# would leak content across owners, so these operations must also match
# on the owner pointer. ``global`` has no owner pointer (tenant-wide).
_SCOPE_OWNER_ATTR: dict[str, str] = {
    "private": "user_id",
    "team_shared": "team_id",
    "project_shared": "project_id",
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class MemoryStoreRequest(BaseModel):
    """Body of POST /memories.

    Validation rules (mirror the DB CHECK ck_memory_entries_scope_pointer
    so we fail at the request layer, not on insert):

      - ``private``        — `user_id` is derived from the JWT
                             (the body field is ignored).
      - ``team_shared``    — body MUST carry `team_id`.
      - ``project_shared`` — body MUST carry `project_id`.
      - ``global``         — no owner pointer needed; restricted to
                             tenant_admin via the route handler.
    """

    model_config = _BASE_CONFIG

    content: str = Field(min_length=1, max_length=2000)
    type: _TYPE_LITERAL = "semantic"
    scope: _SCOPE_LITERAL
    tags: list[str] = Field(default_factory=list, max_length=20)
    team_id: UUID | None = None
    project_id: UUID | None = None

    @model_validator(mode="after")
    def _scope_owner_consistency(self) -> MemoryStoreRequest:
        if self.scope == "team_shared" and self.team_id is None:
            raise ValueError("scope='team_shared' requires team_id")
        if self.scope == "project_shared" and self.project_id is None:
            raise ValueError("scope='project_shared' requires project_id")
        # Strip + dedupe tags while preserving order.
        clean: list[str] = []
        seen: set[str] = set()
        for tag in self.tags:
            t = tag.strip()
            if t and t not in seen:
                seen.add(t)
                clean.append(t)
        object.__setattr__(self, "tags", clean)
        return self


class MemoryResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    scope: str
    type: str
    content: str
    tags: list[str]
    user_id: UUID | None
    team_id: UUID | None
    project_id: UUID | None
    source_execution_id: UUID | None
    agent_id: UUID | None
    has_embedding: bool
    created_at: datetime
    updated_at: datetime


def _to_response(row: MemoryEntry) -> MemoryResponse:
    return MemoryResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        scope=row.scope,
        type=row.type,
        content=row.content,
        tags=list(row.tags or []),
        user_id=row.user_id,
        team_id=row.team_id,
        project_id=row.project_id,
        source_execution_id=row.source_execution_id,
        agent_id=row.agent_id,
        has_embedding=row.embedding is not None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Authorization helper for the `global` scope
# ---------------------------------------------------------------------------
async def _assert_can_write_global(
    session: AsyncSession, principal: AuthPrincipal, tenant_id: UUID
) -> None:
    """Storing a `global` memory means "every agent in the tenant
    reads this" — gate it behind `tenant_admin`. The schema CHECK
    only enforces the owner-pointer shape; this check enforces who
    is allowed."""
    from api_server.db.models import UserOrganizationMembership

    if principal.user_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auth required")
    result = await session.execute(
        select(UserOrganizationMembership.role).where(
            UserOrganizationMembership.tenant_id == tenant_id,
            UserOrganizationMembership.user_id == principal.user_id,
        )
    )
    role = result.scalar_one_or_none()
    if role not in {"tenant_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="scope='global' requires tenant_admin",
        )


def _can_access_memory(row: MemoryEntry, principal: AuthPrincipal) -> bool:
    """Owner isolation for the human-facing CRUD (H1/H2/M3).

    ``memory_entries`` is shared across three systems: the AGENTS (team_shared /
    project_shared / global), the tenant ASSISTANT and the owner CÓRTEX (both
    ``private`` with the human's ``user_id``). A ``private`` row therefore holds
    personal data and may be read / deleted / merged ONLY by its owning user —
    another user's private row is invisible (treated as 404). Shared / global
    rows are agent learnings within the tenant: any tenant member may manage them
    (the merge owner-pointer match already prevents cross-owner folds), and RLS
    fences the tenant boundary.
    """
    if row.scope == "private":
        return row.user_id is not None and row.user_id == principal.user_id
    return True


# ---------------------------------------------------------------------------
# POST /memories
# ---------------------------------------------------------------------------
@router.post("", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def store_memory(
    payload: MemoryStoreRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    embedder: Embedder | None = Depends(get_query_embedder),
) -> MemoryResponse:
    """Persist a single memory manually (Plan 04 task_04_05).

    The Memorizer (task_04_03) writes memories automatically after
    each execution; this endpoint covers the "I want to remember this
    *now*" case humans hit in the chat UI.

    Plan 06.17 task_06_17_03: el contenido se embebe en el momento de crear
    (best-effort, reutilizando ``get_query_embedder``) para que ``has_embedding``
    y "similares" funcionen ya; si el embed falla, queda NULL y el back-fill lo
    rellena luego.
    """
    tenant_id = require_tenant_id(principal)
    if payload.scope == "global":
        await _assert_can_write_global(session, principal, tenant_id)

    # `private` always pins to the authenticated user — even if the
    # body tries to set user_id, we ignore it here.
    user_id = principal.user_id if payload.scope == "private" else None
    if payload.scope == "private" and user_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auth required")

    candidate = MemoryCandidate(
        content=payload.content,
        type=payload.type,
        tags=tuple(payload.tags),
    )
    rows = await persist_memory_candidates(
        session,
        [candidate],
        tenant_id=tenant_id,
        scope=payload.scope,
        user_id=user_id,
        team_id=payload.team_id if payload.scope == "team_shared" else None,
        project_id=payload.project_id if payload.scope == "project_shared" else None,
        agent_id=None,
        source_execution_id=None,
        extra_metadata={"source": "manual", "actor_user_id": str(principal.user_id)},
        embedder=embedder,
    )
    await session.flush()
    row = rows[0]
    await session.refresh(row)
    return _to_response(row)


# ---------------------------------------------------------------------------
# GET /memories
# ---------------------------------------------------------------------------
@router.get("", response_model=list[MemoryResponse])
async def list_memories(
    scope: _SCOPE_LITERAL | None = Query(default=None),
    type: _TYPE_LITERAL | None = Query(default=None),
    project_id: UUID | None = Query(default=None),
    team_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[MemoryResponse]:
    """Visible memories in the tenant, optionally filtered by scope /
    type / owner pointer. RLS handles the tenant boundary; on top of it
    a ``private`` row is visible ONLY to its owning user (H1) — it holds
    personal data (assistant prefs + córtex owner). Shared / global rows
    are visible to any tenant member. This is the operator's Memory UI
    (task_04_06)."""
    stmt = select(MemoryEntry).where(
        MemoryEntry.deleted_at.is_(None),
        # Owner isolation: private rows only for their owner; non-private rows
        # (team_shared / project_shared / global) for any tenant member.
        or_(MemoryEntry.scope != "private", MemoryEntry.user_id == principal.user_id),
    )
    if scope is not None:
        stmt = stmt.where(MemoryEntry.scope == scope)
    if type is not None:
        stmt = stmt.where(MemoryEntry.type == type)
    if project_id is not None:
        stmt = stmt.where(MemoryEntry.project_id == project_id)
    if team_id is not None:
        stmt = stmt.where(MemoryEntry.team_id == team_id)
    stmt = stmt.order_by(MemoryEntry.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return [_to_response(row) for row in result.scalars().all()]


# ---------------------------------------------------------------------------
# GET /memories/skip-reasons — por qué un run NO dejó memoria (Plan 06.17 task_06_17_04)
# ---------------------------------------------------------------------------
class MemorizeSkipItem(BaseModel):
    """Una ejecución que NO produjo memoria + su motivo canónico."""

    model_config = _BASE_CONFIG

    execution_id: UUID
    task_id: UUID
    agent_id: UUID | None
    status: str
    reason: str
    """Código canónico (``not_done``/``skip_private``/``no_team``/``no_scope``/``llm_empty``)."""
    completed_at: datetime | None


@router.get("/skip-reasons", response_model=list[MemorizeSkipItem])
async def list_memorize_skip_reasons(
    reason: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[MemorizeSkipItem]:
    """Ejecuciones del tenant cuyo Memorizer NO produjo memoria, con el motivo.

    Fin del skip silencioso (Plan 06.17 task_06_17_04): el worker persiste el
    código en ``executions.memorize_skip_reason`` y este endpoint lo expone para
    que la UI explique "por qué no hay memoria de este run". RLS acota por tenant
    (un run de otro tenant nunca aparece); opcionalmente se filtra por ``reason``.
    """
    from api_server.db.domain import Execution

    stmt = select(Execution).where(Execution.memorize_skip_reason.is_not(None))
    if reason is not None:
        stmt = stmt.where(Execution.memorize_skip_reason == reason)
    stmt = stmt.order_by(Execution.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        MemorizeSkipItem(
            execution_id=row.id,
            task_id=row.task_id,
            agent_id=row.agent_id,
            status=row.status,
            reason=row.memorize_skip_reason or "",
            completed_at=row.completed_at,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# DELETE /memories/{id}
# ---------------------------------------------------------------------------
@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Soft-delete a memory. RLS + tenant check; we stamp `deleted_at`
    rather than dropping the row so audits survive.

    Owner authorization (H2): a ``private`` memory can only be deleted by its
    owning user — another user's private row is invisible (404). Shared / global
    rows stay manageable by any tenant member (RLS fences the tenant)."""
    require_tenant_id(principal)
    result = await session.execute(
        select(MemoryEntry).where(MemoryEntry.id == memory_id, MemoryEntry.deleted_at.is_(None))
    )
    row = result.scalar_one_or_none()
    if row is None or not _can_access_memory(row, principal):
        # Hide another user's private memory behind the same 404 as a missing row.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")
    from datetime import UTC
    from datetime import datetime as _dt

    row.deleted_at = _dt.now(tz=UTC)
    await session.flush()


# ---------------------------------------------------------------------------
# Plan 06.7 — Similar memories (pgvector cosine) + merge-into
# ---------------------------------------------------------------------------


class SimilarMemoryItem(BaseModel):
    """One candidate returned by `GET /memories/{id}/similar`."""

    model_config = _BASE_CONFIG
    memory: MemoryResponse
    similarity: float
    """Cosine similarity in [0, 1]. 1.0 = identical embeddings."""


class MergeRequest(BaseModel):
    """Body of POST /memories/{source_id}/merge-into."""

    model_config = _BASE_CONFIG
    target_id: UUID


@router.get("/{memory_id}/similar", response_model=list[SimilarMemoryItem])
async def list_similar_memories(
    memory_id: UUID,
    threshold: float | None = None,
    limit: int | None = None,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[SimilarMemoryItem]:
    """Return up to ``limit`` memories with cosine similarity ≥
    ``threshold`` to the row's embedding. Defaults come from the
    tenant-settings registry (Plan 06.7)."""
    from sqlalchemy import text as _text

    from api_server.settings_registry import get_setting

    tenant_id = require_tenant_id(principal)

    src = (
        await session.execute(
            select(MemoryEntry).where(MemoryEntry.id == memory_id, MemoryEntry.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    # Owner authorization (M3): another user's private memory is invisible —
    # surfacing its "similar" candidates would leak its content/existence.
    if src is None or not _can_access_memory(src, principal):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")
    if src.embedding is None:
        return []

    eff_threshold = (
        threshold
        if threshold is not None
        else float(await get_setting(session, tenant_id, "memories", "similarity.threshold"))
    )
    eff_limit = (
        limit
        if limit is not None
        else int(await get_setting(session, tenant_id, "memories", "similarity.limit"))
    )

    # Candidates must share the source's owner pointer, not just its
    # scope: RLS only fences by tenant, so without this clause a
    # project_shared memory of project A would surface project B's
    # rows (same tenant, same scope) — a cross-owner leak. For
    # ``global`` there is no owner pointer, so no extra filter applies.
    owner_attr = _SCOPE_OWNER_ATTR.get(src.scope)
    owner_clause = f"AND {owner_attr} = :owner_id" if owner_attr is not None else ""
    sql = _text(
        f"""
        SELECT
            id,
            1 - (embedding <=> CAST(:src_embedding AS vector)) AS similarity
        FROM memory_entries
        WHERE id != :src_id
          AND deleted_at IS NULL
          AND embedding IS NOT NULL
          AND scope = :scope
          AND tenant_id = :tenant_id
          {owner_clause}
          AND (1 - (embedding <=> CAST(:src_embedding AS vector))) >= :threshold
        ORDER BY embedding <=> CAST(:src_embedding AS vector)
        LIMIT :limit
        """
    )
    # pgvector exige formato `'[a,b,c]'` con comas. `str(numpy_array)`
    # devuelve `'[a b c]'` con espacios y rompe el CAST. Misma receta
    # que `memorizer/recall.py` y `rag/search.py`.
    src_vec_literal = "[" + ",".join(f"{x:.6f}" for x in src.embedding) + "]"
    params: dict[str, object] = {
        "src_id": src.id,
        "src_embedding": src_vec_literal,
        "scope": src.scope,
        "tenant_id": tenant_id,
        "threshold": eff_threshold,
        "limit": eff_limit,
    }
    if owner_attr is not None:
        params["owner_id"] = getattr(src, owner_attr)
    rows = (await session.execute(sql, params)).all()
    if not rows:
        return []

    candidate_ids = [r.id for r in rows]
    sim_by_id = {r.id: float(r.similarity) for r in rows}
    candidates = (
        (await session.execute(select(MemoryEntry).where(MemoryEntry.id.in_(candidate_ids))))
        .scalars()
        .all()
    )
    by_id = {c.id: c for c in candidates}

    return [
        SimilarMemoryItem(
            memory=_to_response(by_id[cid]),
            similarity=round(sim_by_id[cid], 4),
        )
        for cid in candidate_ids
        if cid in by_id
    ]


@router.post("/{source_id}/merge-into", response_model=MemoryResponse)
async def merge_memory_into(
    source_id: UUID,
    payload: MergeRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    embedder: Embedder | None = Depends(get_query_embedder),
) -> MemoryResponse:
    """Fold ``source_id`` INTO ``target_id`` (asymmetric merge).

    Target's content gets the source's appended (separator
    ``\\n\\n---\\n``); tags become the union; ``metadata.merged_from``
    accumulates the source's id. The source is soft-deleted.

    Plan 06.17 task_06_17_03: el contenido del destino CAMBIA, así que su
    embedding queda obsoleto — se RE-EMBEBE el contenido combinado (best-effort,
    reutilizando ``get_query_embedder``) para que "similares" siga siendo
    coherente. Si el embed falla (Ollama caído), el embedding queda NULL y el
    back-fill lo rellena luego; el merge no se bloquea.
    """
    require_tenant_id(principal)

    if source_id == payload.target_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="cannot merge a memory into itself",
        )

    src = (
        await session.execute(
            select(MemoryEntry).where(MemoryEntry.id == source_id, MemoryEntry.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    tgt = (
        await session.execute(
            select(MemoryEntry).where(
                MemoryEntry.id == payload.target_id, MemoryEntry.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if src is None or tgt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")
    # Owner authorization (M3): another user's private memory is invisible (404),
    # so it can be neither source nor target of a merge. Shared/global folds stay
    # guarded by the owner-pointer match below (no cross-project/team leak).
    for row in (src, tgt):
        if not _can_access_memory(row, principal):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")
    if src.scope != tgt.scope:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"cannot merge across scopes ({src.scope!r} -> {tgt.scope!r})",
        )
    # Same scope is necessary but not sufficient: within a scope the
    # owner pointer (project_id / team_id / user_id) partitions rows
    # by owner. Merging two project_shared memories of *different*
    # projects (or team_shared of different teams, or private of
    # different users) folds one owner's content into another — a
    # cross-owner leak. ``global`` has no owner pointer, so any two
    # global rows of the tenant may merge.
    owner_attr = _SCOPE_OWNER_ATTR.get(src.scope)
    if owner_attr is not None and getattr(src, owner_attr) != getattr(tgt, owner_attr):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"cannot merge across {owner_attr} within scope {src.scope!r}"
                f" ({getattr(src, owner_attr)!r} -> {getattr(tgt, owner_attr)!r})"
            ),
        )

    tgt.content = f"{tgt.content}\n\n---\n{src.content}"
    merged_tags = list(dict.fromkeys([*list(tgt.tags or []), *list(src.tags or [])]))
    tgt.tags = merged_tags
    md = dict(tgt.metadata_ or {})
    history = list(md.get("merged_from") or [])
    history.append(str(src.id))
    md["merged_from"] = history
    tgt.metadata_ = md

    # El contenido del destino cambió → re-embeber (best-effort) para no dejar
    # un embedding que ya no representa el texto. Un fallo deja NULL (back-fill).
    tgt.embedding = await _reembed_content(embedder, tgt.content)

    from datetime import UTC
    from datetime import datetime as _dt

    src.deleted_at = _dt.now(tz=UTC)
    await session.flush()
    await session.refresh(tgt)
    return _to_response(tgt)


async def _reembed_content(embedder: Embedder | None, content: str) -> list[float] | None:
    """Re-embebe ``content`` (best-effort) tras un merge.

    Devuelve el vector, o ``None`` cuando no hay embedder, el contenido está
    vacío o el embed falla (Ollama caído). En ese último caso el back-fill
    rellena el NULL más tarde; el merge no se bloquea."""
    if embedder is None or not content.strip():
        return None
    try:
        vectors = await embedder.embed([content])
    except EmbeddingError:
        return None
    return list(vectors[0]) if vectors else None


__all__ = ["router"]
