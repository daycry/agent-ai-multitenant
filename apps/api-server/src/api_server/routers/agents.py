"""`/agents` endpoints — tenant-scoped CRUD with scope filters.

Auth model
----------
- All endpoints require a valid JWT (`get_tenant_session` -> RLS-scoped
  AsyncSession). Tenant users see their own templates + project-locals
  plus all `global_builtin` agents (the latter via the
  `agents_global_builtin_read` policy added in migration 0004).
- Writes are restricted by scope:
    * `project_local` and `global_tenant_template` -> any tenant user.
    * `global_builtin` -> blocked here (returns 403). System Admin
      creates/updates those via seed scripts or future /admin/agents
      endpoints; built-ins are not editable from the tenant API.

Soft-delete semantics
---------------------
`DELETE /agents/{id}` stamps `deleted_at`. The row stays for audit but
is filtered out of list/get queries (`deleted_at IS NULL` on every
read). Re-using the same name later is allowed because uniqueness
constraints don't currently exist on agents.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.db.domain import Agent, AgentScope, Project
from api_server.routers._helpers import (
    apply_partial_update,
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
)
from api_server.schemas.agents import (
    AgentCreateRequest,
    AgentDiffResponse,
    AgentFieldDiff,
    AgentForkRequest,
    AgentMergeRequest,
    AgentResponse,
    AgentUpdateRequest,
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
# GET /agents -- list with optional filters
# ---------------------------------------------------------------------------
@router.get("", response_model=list[AgentResponse])
async def list_agents(
    scope: AgentScope | None = Query(default=None, description="Filter by scope"),
    project_id: UUID | None = Query(default=None, description="Filter by project_id"),
    role: str | None = Query(default=None, description="Filter by role"),
    agent_type: str | None = Query(default=None, description="Filter by agent_type (ai|human)"),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[AgentResponse]:
    """List agents visible to the caller.

    Visibility (enforced by RLS):
      * Tenant's own `global_tenant_template` + `project_local` rows.
      * Every `global_builtin` row, regardless of tenant_id (added by
        migration 0004 via a SELECT-only policy).
    """
    stmt = select(Agent).where(Agent.deleted_at.is_(None))
    if scope is not None:
        stmt = stmt.where(Agent.scope == scope.value)
    if project_id is not None:
        stmt = stmt.where(Agent.project_id == project_id)
    if role is not None:
        stmt = stmt.where(Agent.role == role)
    if agent_type is not None:
        stmt = stmt.where(Agent.agent_type == agent_type)
    stmt = stmt.order_by(Agent.created_at)
    result = await session.execute(stmt)
    return [to_agent_response(a) for a in result.scalars().all()]


# ---------------------------------------------------------------------------
# GET /agents/{id}
# ---------------------------------------------------------------------------
@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentResponse:
    result = await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    return to_agent_response(agent)


# ---------------------------------------------------------------------------
# POST /agents
# ---------------------------------------------------------------------------
@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentResponse:
    tenant_id = require_tenant_id(principal)

    if payload.scope == AgentScope.GLOBAL_BUILTIN:
        # Built-ins are owned by the platform and only created by the
        # seed scripts (task_01_09). A tenant-API caller cannot inject
        # them even if RLS would let them: refuse explicitly with 403
        # so the rejection is auditable.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="global_builtin agents cannot be created through the tenant API",
        )

    agent = Agent(
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        avatar_url=payload.avatar_url,
        agent_type=payload.agent_type.value,
        role=payload.role.value,
        system_prompt=payload.system_prompt,
        model_config=payload.llm_config,
        memory_scope=payload.memory_scope.value,
        review_capability=payload.review_capability,
        max_concurrent_tasks=payload.max_concurrent_tasks,
        is_template=payload.is_template,
        scope=payload.scope.value,
        project_id=payload.project_id,
    )
    session.add(agent)
    await session.flush()
    await session.refresh(agent)
    return to_agent_response(agent)


# ---------------------------------------------------------------------------
# PUT /agents/{id} -- partial update
# ---------------------------------------------------------------------------
@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: UUID,
    payload: AgentUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentResponse:
    require_tenant_id(principal)
    agent = await get_writable_or_404(
        session, Agent, agent_id, principal, not_found_detail="agent not found"
    )

    apply_partial_update(
        agent,
        payload,
        enum_fields=("agent_type", "role", "memory_scope"),
        rename={"llm_config": "model_config"},
    )

    await session.flush()
    await session.refresh(agent)
    return to_agent_response(agent)


# ---------------------------------------------------------------------------
# DELETE /agents/{id} -- soft delete
# ---------------------------------------------------------------------------
@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    require_tenant_id(principal)
    agent = await get_writable_or_404(
        session, Agent, agent_id, principal, not_found_detail="agent not found"
    )
    await soft_delete(session, agent)


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

    return AgentDiffResponse(
        fork_id=fork.id,
        source_id=fork.forked_from_agent_id,
        forked_from_version=fork.forked_from_version,
        source_current_version=source_current_version,
        source_moved=source_moved,
        source_deleted=source_deleted,
        fields=fields,
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
