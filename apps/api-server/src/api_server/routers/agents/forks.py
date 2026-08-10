"""Fork / diff / merge de agentes (ADR 0006, Plan 06.17 `task_06_17_12`).

Un fork clona un agente visible (built-in o plantilla del tenant) en una copia
`project_local` del tenant; el diff compara campo a campo contra el origen y el
merge absorbe selectivamente los cambios de aguas arriba.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.db.domain import Agent, AgentScope, Project
from api_server.routers._helpers import get_writable_or_404, require_tenant_id
from api_server.routers.agents.common import (
    _agent_capability_ids,
    _clone_agent_capabilities,
)
from api_server.schemas.agents import (
    AgentCapabilitiesDiff,
    AgentDiffResponse,
    AgentFieldDiff,
    AgentForkRequest,
    AgentMergeRequest,
    AgentResponse,
    to_agent_response,
)

# Fields that participate in the fork-vs-source diff. JSON columns are
# compared as whole values; in v2 we may want a deeper diff for nested
# dicts but for now any change inside `model_config` is one diff entry.
_DIFFABLE_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "avatar_url",
    "agent_type",
    "role",
    "system_prompt",
    "model_config",
    "memory_scope",
    "review_capability",
    "max_concurrent_tasks",
    "is_template",
)

router = APIRouter(prefix="/agents", tags=["agents"])


# ---------------------------------------------------------------------------
# POST /agents/{source_id}/fork
#
# Clones a visible agent (built-in or this tenant's template) into a
# tenant-owned `project_local` copy. The source row is untouched; the
# new row links back via `forked_from_agent_id` and captures the
# source's `updated_at` as `forked_from_version` so the diff/merge
# operations (task_01_16 + 17) can tell whether the source has moved
# since fork time.
# ---------------------------------------------------------------------------
@router.post(
    "/{source_id}/fork",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def fork_agent(
    source_id: UUID,
    payload: AgentForkRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentResponse:
    tenant_id = require_tenant_id(principal)

    # The source can be a global_builtin (visible to all tenants via the
    # SELECT-only RLS policy) or a row owned by the caller's tenant.
    src_result = await session.execute(
        select(Agent).where(Agent.id == source_id, Agent.deleted_at.is_(None))
    )
    source = src_result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source agent not found")

    # The target project must belong to the caller's tenant (RLS
    # already filters; the explicit tenant_id check is belt-and-braces).
    proj_result = await session.execute(
        select(Project).where(
            Project.id == payload.project_id,
            Project.tenant_id == tenant_id,
            Project.deleted_at.is_(None),
            Project.is_template.is_(False),
        )
    )
    project = proj_result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    # Snapshot the source's updated_at so we can later answer "has the
    # source moved since I forked?" with a simple timestamp compare.
    forked_from_version = source.updated_at.isoformat() if source.updated_at is not None else None

    fork = Agent(
        tenant_id=tenant_id,
        name=payload.name or source.name,
        description=source.description,
        avatar_url=source.avatar_url,
        agent_type=source.agent_type,
        role=source.role,
        system_prompt=payload.system_prompt or source.system_prompt,
        # dict() makes a shallow copy so editing the fork's config later
        # doesn't mutate the source row's JSON in memory.
        model_config=dict(source.model_config or {}),
        memory_scope=source.memory_scope,
        review_capability=source.review_capability,
        max_concurrent_tasks=source.max_concurrent_tasks,
        # A fork is a concrete agent, not a template -- the user can
        # always re-mark it as template via PUT if they want.
        is_template=False,
        scope=AgentScope.PROJECT_LOCAL.value,
        project_id=payload.project_id,
        forked_from_agent_id=source.id,
        forked_from_version=forked_from_version,
        anchored_version=None,
    )
    session.add(fork)
    await session.flush()

    # Plan 06.17 task_06_17_12: el fork hereda las CAPACIDADES del origen, no
    # solo la persona. Clonamos las tres junctions (SABER/HACER/SER):
    #   * agent_knowledge_bases (KBs de rol)
    #   * agent_tools (tools asignadas, con su config_override)
    #   * agent_skills (skills asignadas, con su proficiency)
    #
    # Tenant-safe por construcción: solo se copian las filas VISIBLES al que
    # forkea. `agent_knowledge_bases` está aislada por RLS (tenant_id), así que
    # forkear un built-in de plataforma NO arrastra sus KBs (ADR 0026 — el tenant
    # grantea las suyas al fork). `agent_tools`/`agent_skills` no tienen RLS
    # propia pero el origen ya es visible (RLS de `agents`), de modo que un
    # source de otro tenant ni siquiera llega aquí (404 arriba). Las filas
    # clonadas de KB llevan el `tenant_id` del que forkea, nunca el del origen.
    await _clone_agent_capabilities(
        session,
        source_id=source.id,
        fork_id=fork.id,
        tenant_id=tenant_id,
        granted_by=principal.user_id,
    )

    await session.refresh(fork)
    return to_agent_response(fork)


# ---------------------------------------------------------------------------
# GET /agents/{fork_id}/diff
#
# Field-by-field comparison between a fork and its source. Empty
# `fields` means "fork matches source exactly". `source_moved` is true
# when the source has been updated since the fork point -- the UI uses
# this to offer the "absorb upstream improvements" workflow.
# ---------------------------------------------------------------------------
@router.get("/{fork_id}/diff", response_model=AgentDiffResponse)
async def diff_fork_against_source(
    fork_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentDiffResponse:
    # The fork must be visible to the caller (RLS handles cross-tenant).
    fork_result = await session.execute(
        select(Agent).where(Agent.id == fork_id, Agent.deleted_at.is_(None))
    )
    fork = fork_result.scalar_one_or_none()
    if fork is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    if fork.forked_from_agent_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="agent is not a fork (forked_from_agent_id is null)",
        )

    # The source may be soft-deleted; we still surface the diff so the
    # UI can warn that the upstream is gone. RLS-visible only.
    source_result = await session.execute(
        select(Agent).where(Agent.id == fork.forked_from_agent_id)
    )
    source = source_result.scalar_one_or_none()
    source_deleted = source is None or source.deleted_at is not None
    source_current_version: str | None = None
    fields: dict[str, AgentFieldDiff] = {}
    if source is not None:
        source_current_version = (
            source.updated_at.isoformat() if source.updated_at is not None else None
        )
        for field in _DIFFABLE_FIELDS:
            fork_val = getattr(fork, field)
            src_val = getattr(source, field)
            if fork_val != src_val:
                fields[field] = AgentFieldDiff(fork=fork_val, source=src_val)

    source_moved = (
        source is not None
        and not source_deleted
        and fork.forked_from_version is not None
        and source_current_version is not None
        and source_current_version != fork.forked_from_version
    )

    # Exponemos también las CAPACIDADES de cada lado (Plan 06.17 task_06_17_12)
    # para que la UI muestre qué KBs/tools/skills tiene el fork frente al origen.
    capabilities: dict[str, AgentCapabilitiesDiff] = {
        "fork": await _agent_capability_ids(session, fork.id),
    }
    if source is not None:
        capabilities["source"] = await _agent_capability_ids(session, source.id)

    return AgentDiffResponse(
        fork_id=fork.id,
        source_id=fork.forked_from_agent_id,
        forked_from_version=fork.forked_from_version,
        source_current_version=source_current_version,
        source_moved=source_moved,
        source_deleted=source_deleted,
        fields=fields,
        capabilities=capabilities,
    )


# ---------------------------------------------------------------------------
# POST /agents/{fork_id}/merge
#
# Selectively absorb upstream improvements: for each field the caller
# lists, copy the source's current value into the fork. Fields not
# listed stay untouched. After the merge `forked_from_version` is
# advanced to the source's current `updated_at`, so the next diff
# treats this state as the new baseline.
# ---------------------------------------------------------------------------
@router.post("/{fork_id}/merge", response_model=AgentResponse)
async def merge_from_source(
    fork_id: UUID,
    payload: AgentMergeRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentResponse:
    require_tenant_id(principal)

    fork = await get_writable_or_404(
        session, Agent, fork_id, principal, not_found_detail="agent not found"
    )
    if fork.forked_from_agent_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="agent is not a fork (forked_from_agent_id is null)",
        )

    unknown = set(payload.fields) - set(_DIFFABLE_FIELDS)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown or non-mergeable fields: {sorted(unknown)}",
        )

    source_result = await session.execute(
        select(Agent).where(Agent.id == fork.forked_from_agent_id, Agent.deleted_at.is_(None))
    )
    source = source_result.scalar_one_or_none()
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="source agent no longer exists; cannot merge",
        )

    for field in payload.fields:
        src_val = getattr(source, field)
        # Deep-ish copy for JSON fields so future source edits don't
        # leak through shared Python references.
        if isinstance(src_val, dict):
            src_val = dict(src_val)
        elif isinstance(src_val, list):
            src_val = list(src_val)
        setattr(fork, field, src_val)

    # Re-anchor: from now on, "source moved?" compares against this snapshot.
    fork.forked_from_version = (
        source.updated_at.isoformat() if source.updated_at is not None else None
    )

    await session.flush()
    await session.refresh(fork)
    return to_agent_response(fork)
