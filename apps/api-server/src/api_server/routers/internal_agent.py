"""Router for `/internal/agent/*` (Plan 04.5 task_04_5_01 / 03).

The family of endpoints the agent-runtime sandbox calls back into
during a run. All routes share the dedicated auth dependency
:func:`get_agent_principal` that validates the sandbox-scoped JWT
minted by the worker; tenant-scoped routes additionally use
:func:`get_agent_tenant_session` so RLS pins the active tenant.

Endpoints:

  - ``GET  /_health``       smoke probe (task_04_5_01).
  - ``POST /memory-recall`` hybrid BM25 + vector recall (task_04_5_03).
  - ``POST /memory-store``  persist one MemoryEntry (task_04_5_03).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.internal_agent import (
    AgentPrincipal,
    get_agent_principal,
    get_agent_tenant_session,
)
from api_server.db.domain import Agent, MemoryScope, Project
from api_server.memorizer import MemoryCandidate, persist_memory_candidates
from api_server.memorizer.recall import recall

router = APIRouter(prefix="/internal/agent", tags=["internal-agent"])

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

_SCOPE_LITERAL = Literal["private", "team_shared", "project_shared", "global"]
_TYPE_LITERAL = Literal["episodic", "semantic"]


# ---------------------------------------------------------------------------
# /_health
# ---------------------------------------------------------------------------
@router.get("/_health")
async def health(
    principal: AgentPrincipal = Depends(get_agent_principal),
) -> dict[str, str]:
    """Smoke endpoint that proves the agent-token auth dependency works.

    Returns the principal's `agent_id` and `tenant_id` so tests can
    assert the token was parsed correctly. No DB writes, no side
    effects — safe to keep enabled in all environments.
    """
    return {
        "status": "ok",
        "agent_id": str(principal.agent_id),
        "tenant_id": str(principal.tenant_id),
    }


# ---------------------------------------------------------------------------
# /memory-recall
# ---------------------------------------------------------------------------
class MemoryRecallRequest(BaseModel):
    model_config = _BASE_CONFIG

    query: str = Field(min_length=1, max_length=2000)
    scopes: list[_SCOPE_LITERAL] = Field(default_factory=list, max_length=4)
    limit: int = Field(default=5, ge=1, le=20)


class MemoryRecallHitOut(BaseModel):
    model_config = _BASE_CONFIG

    memory_id: UUID
    content: str
    scope: str
    type: str
    bm25_rank: int | None
    vector_rank: int | None
    rrf_score: float


class MemoryRecallResponse(BaseModel):
    model_config = _BASE_CONFIG

    hits: list[MemoryRecallHitOut]


async def _resolve_agent_context(
    session: AsyncSession, agent_id: UUID, tenant_id: UUID
) -> tuple[Agent, Project | None]:
    """Load the agent + (when scope=project_local) its project.

    Both ``team_shared`` and ``project_shared`` recall/store need the
    project to derive ``team_id`` / ``project_id`` for the owner
    filter. The agent row is also the gate that decides which scopes
    this agent is allowed to read or write.
    """
    result = await session.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant_id,
            Agent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="agent not found in tenant",
        )
    project: Project | None = None
    if agent.project_id is not None:
        project = (
            await session.execute(select(Project).where(Project.id == agent.project_id))
        ).scalar_one_or_none()
    return agent, project


@router.post("/memory-recall", response_model=MemoryRecallResponse)
async def memory_recall(
    payload: MemoryRecallRequest,
    principal: AgentPrincipal = Depends(get_agent_principal),
    session: AsyncSession = Depends(get_agent_tenant_session),
) -> MemoryRecallResponse:
    """Hybrid BM25 + vector recall over memory_entries.

    Scope resolution:
      - the agent picks which scopes to search via ``payload.scopes``;
      - the server resolves the owner pointers (team_id / project_id)
        from the agent's project so an agent can never look at another
        team's or project's memories;
      - ``private`` is included for forward compatibility but yields
        nothing for an AI agent (no user_id is set on AI principals).
    """
    agent, project = await _resolve_agent_context(session, principal.agent_id, principal.tenant_id)
    scopes = payload.scopes or _default_readable_scopes(agent.memory_scope)
    team_id = project.team_id if project is not None else None
    project_id = agent.project_id

    hits = await recall(
        session,
        query=payload.query,
        tenant_id=principal.tenant_id,
        scopes=scopes,
        user_id=None,  # AI agents have no user attribution
        team_id=team_id,
        project_id=project_id,
        query_embedding=None,  # embedder wire-up: Plan 04 task_04_14
        limit=payload.limit,
    )
    return MemoryRecallResponse(
        hits=[
            MemoryRecallHitOut(
                memory_id=h.memory_id,
                content=h.content,
                scope=h.scope,
                type=h.type,
                bm25_rank=h.bm25_rank,
                vector_rank=h.vector_rank,
                rrf_score=h.rrf_score,
            )
            for h in hits
        ]
    )


# ---------------------------------------------------------------------------
# /memory-store
# ---------------------------------------------------------------------------
class MemoryStoreRequest(BaseModel):
    """Body of POST /internal/agent/memory-store.

    The agent provides content + type + optional tags. The scope, when
    omitted, defaults to the agent's own ``memory_scope``. When given,
    it must equal the agent's scope — an agent may not store into a
    scope wider than its own (defence in depth on top of the schema
    CHECK)."""

    model_config = _BASE_CONFIG

    content: str = Field(min_length=1, max_length=2000)
    type: _TYPE_LITERAL = "semantic"
    scope: _SCOPE_LITERAL | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)


class MemoryStoreResponse(BaseModel):
    model_config = _BASE_CONFIG

    memory_id: UUID
    scope: str
    type: str


@router.post(
    "/memory-store",
    response_model=MemoryStoreResponse,
    status_code=status.HTTP_201_CREATED,
)
async def memory_store(
    payload: MemoryStoreRequest,
    principal: AgentPrincipal = Depends(get_agent_principal),
    session: AsyncSession = Depends(get_agent_tenant_session),
) -> MemoryStoreResponse:
    """Persist a single memory written by the agent.

    The agent's ``memory_scope`` decides where the row lands. Owner
    pointers (team_id / project_id) are resolved from the agent's
    project, never from the request body — the agent cannot escape
    its own tenant + team + project boundary.
    """
    agent, project = await _resolve_agent_context(session, principal.agent_id, principal.tenant_id)

    scope = payload.scope or agent.memory_scope
    if scope not in {s.value for s in MemoryScope}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported memory scope: {scope!r}",
        )
    if scope != agent.memory_scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"agent memory_scope is {agent.memory_scope!r}; "
                f"cannot store memories in scope {scope!r}"
            ),
        )

    owner = _resolve_store_owner(scope=scope, project=project, agent=agent)
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"scope={scope!r} requires an owner pointer that the agent "
                "cannot provide (e.g. team_shared on a project without a "
                "team_id, or private for an AI agent)"
            ),
        )

    candidate = MemoryCandidate(
        content=payload.content,
        type=payload.type,
        tags=tuple(t.strip() for t in payload.tags if t.strip()),
    )
    rows = await persist_memory_candidates(
        session,
        [candidate],
        tenant_id=principal.tenant_id,
        scope=scope,
        agent_id=agent.id,
        source_execution_id=None,
        extra_metadata={"source": "agent_runtime"},
        **owner,
    )
    await session.flush()
    row = rows[0]
    await session.refresh(row)
    return MemoryStoreResponse(memory_id=row.id, scope=row.scope, type=row.type)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _default_readable_scopes(agent_scope: str) -> list[str]:
    """The scope set an agent reads when it doesn't specify one.

    The contract: an agent can read everything at or below its own
    write scope. So:
      private        -> private + team_shared + project_shared + global
      team_shared    -> team_shared + project_shared + global
      project_shared -> project_shared + global
      global         -> global

    This widens reads but never widens writes (writes still pin to the
    agent's own memory_scope). The order is most-specific-first.
    """
    ladder = ["private", "team_shared", "project_shared", "global"]
    if agent_scope not in ladder:
        return ["global"]  # an agent opted out of canonical scopes reads only global
    return ladder[ladder.index(agent_scope) :]


def _resolve_store_owner(
    *, scope: str, project: Project | None, agent: Agent
) -> dict[str, UUID | None] | None:
    """Compute the owner kwargs ``persist_memory_candidates`` needs."""
    if scope == MemoryScope.GLOBAL.value:
        return {"user_id": None, "team_id": None, "project_id": None}
    if scope == MemoryScope.PROJECT_SHARED.value:
        if agent.project_id is None:
            return None
        return {"user_id": None, "team_id": None, "project_id": agent.project_id}
    if scope == MemoryScope.TEAM_SHARED.value:
        if project is None or project.team_id is None:
            return None
        return {"user_id": None, "team_id": project.team_id, "project_id": None}
    # MemoryScope.PRIVATE — no user attribution for an AI agent.
    return None
