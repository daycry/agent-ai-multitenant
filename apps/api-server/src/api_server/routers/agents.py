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
from pydantic import BaseModel, ConfigDict, Field
from shared_domain.tool_names import to_canonical_set
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.agent_tools_enforcement import compute_effective_tools
from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.chat.modes import resolve_mode_config
from api_server.db.domain import (
    Agent,
    AgentScope,
    AgentTool,
    Project,
    Tool,
    ToolImplementationType,
)
from api_server.db.knowledge import AgentKnowledgeBase, KnowledgeBase
from api_server.routers._helpers import (
    apply_partial_update,
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
)
from api_server.routers._pagination import (
    apply_pagination,
    limit_query,
    offset_query,
)
from api_server.schemas.agents import (
    AgentCreateRequest,
    AgentDiffResponse,
    AgentFieldDiff,
    AgentForkRequest,
    AgentMergeRequest,
    AgentResponse,
    AgentToolResponse,
    AgentUpdateRequest,
    GrantKBRequest,
    SetAgentToolsRequest,
    to_agent_response,
)
from api_server.schemas.catalog import tool_is_runtime_wired

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
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[AgentResponse]:
    """List agents visible to the caller.

    Visibility (enforced by RLS):
      * Tenant's own `global_tenant_template` + `project_local` rows.
      * Every `global_builtin` row, regardless of tenant_id (added by
        migration 0004 via a SELECT-only policy).

    Paged via `limit`/`offset` (task_06_14_13) so a tenant with many
    agents never produces an unbounded response.
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
    # Deterministic order (created_at, then id as a tiebreaker) so
    # offset paging is stable even when many rows share a timestamp.
    stmt = stmt.order_by(Agent.created_at, Agent.id)
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
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


# ---------------------------------------------------------------------------
# Plan 06.9: agent ↔ KB grants
# ---------------------------------------------------------------------------
#
# Three endpoints on top of /agents/{id}/knowledge-bases that mirror
# the project↔KB junction added in Plan 04. Same gate pattern
# (tenant_admin for grant/revoke, tenant_member for read) and same
# explicit-grant rule (a KB only becomes "visible to the agent" when
# the row exists).
#
# Built-in agents (scope=global_builtin) reject grant/revoke with 403.
# The platform manages those via seeds — tenant admins fork them
# (creates a global_tenant_template copy) and grant their KBs to the
# fork instead. Same UX pattern as the agent fork-and-edit flow.


async def _load_writable_agent_for_kb(
    session: AsyncSession,
    agent_id: UUID,
    principal: AuthPrincipal,
) -> Agent:
    """Load an agent and reject if it's a `global_builtin`.

    `get_writable_or_404` already filters by tenant via RLS + 404s on
    miss. Here we add the scope check: built-ins are off-limits to
    tenant admins.
    """
    agent = await get_writable_or_404(
        session, Agent, agent_id, principal, not_found_detail="agent not found"
    )
    if agent.scope == AgentScope.GLOBAL_BUILTIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "cannot grant/revoke KBs on a global_builtin agent; "
                "fork it first and grant on the fork"
            ),
        )
    return agent


@router.get(
    "/{agent_id}/knowledge-bases",
    response_model=list[dict[str, object]],
)
async def list_agent_kbs(
    agent_id: UUID,
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, object]]:
    """List KBs granted to this agent (paged via `limit`/`offset`)."""
    # First: make sure the agent is visible to the caller. RLS handles
    # cross-tenant; here we only need to surface 404 on miss instead of
    # an empty list (a hidden grant would otherwise look like "no
    # grants" to the UI).
    agent_q = await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
    )
    if agent_q.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    rows = await session.execute(
        select(
            AgentKnowledgeBase.kb_id,
            AgentKnowledgeBase.granted_at,
            AgentKnowledgeBase.granted_by,
            KnowledgeBase.name,
            KnowledgeBase.description,
            KnowledgeBase.embedding_model_id,
        )
        .join(KnowledgeBase, KnowledgeBase.id == AgentKnowledgeBase.kb_id)
        .where(
            AgentKnowledgeBase.agent_id == agent_id,
            KnowledgeBase.deleted_at.is_(None),
        )
        .order_by(KnowledgeBase.name, KnowledgeBase.id)
        .limit(limit)
        .offset(offset)
    )
    return [
        {
            "kb_id": str(r.kb_id),
            "name": r.name,
            "description": r.description,
            "embedding_model_id": r.embedding_model_id,
            "granted_at": r.granted_at.isoformat() if r.granted_at else None,
            "granted_by": str(r.granted_by) if r.granted_by else None,
        }
        for r in rows.all()
    ]


@router.post(
    "/{agent_id}/knowledge-bases",
    response_model=dict[str, object],
    status_code=status.HTTP_201_CREATED,
)
async def grant_kb_to_agent(
    agent_id: UUID,
    payload: GrantKBRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, object]:
    """Grant a KB to the agent. Re-granting is a no-op (idempotent)."""
    tenant_id = require_tenant_id(principal)
    kb_id = payload.kb_id

    agent = await _load_writable_agent_for_kb(session, agent_id, principal)

    # Verify the KB exists and is in the caller's tenant. RLS would
    # hide cross-tenant rows; this explicit check converts a silent
    # miss into a clean 404.
    kb_q = await session.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.deleted_at.is_(None))
    )
    if kb_q.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="kb not found")

    # Idempotent: if the grant already exists, return 201 with the
    # existing row instead of 409. Matches the kb_projects pattern.
    existing_q = await session.execute(
        select(AgentKnowledgeBase).where(
            AgentKnowledgeBase.agent_id == agent_id,
            AgentKnowledgeBase.kb_id == kb_id,
        )
    )
    existing = existing_q.scalar_one_or_none()
    if existing is not None:
        return {
            "agent_id": str(agent.id),
            "kb_id": str(kb_id),
            "granted_at": existing.granted_at.isoformat() if existing.granted_at else None,
        }

    grant = AgentKnowledgeBase(
        agent_id=agent_id,
        kb_id=kb_id,
        tenant_id=tenant_id,
        granted_by=principal.user_id,
    )
    session.add(grant)
    await session.flush()
    return {
        "agent_id": str(agent.id),
        "kb_id": str(kb_id),
        "granted_at": grant.granted_at.isoformat() if grant.granted_at else None,
    }


@router.delete(
    "/{agent_id}/knowledge-bases/{kb_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_kb_from_agent(
    agent_id: UUID,
    kb_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Revoke the grant. Idempotent: missing row returns 204 anyway."""
    require_tenant_id(principal)
    await _load_writable_agent_for_kb(session, agent_id, principal)

    existing_q = await session.execute(
        select(AgentKnowledgeBase).where(
            AgentKnowledgeBase.agent_id == agent_id,
            AgentKnowledgeBase.kb_id == kb_id,
        )
    )
    existing = existing_q.scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.flush()


# ---------------------------------------------------------------------------
# Plan 06.15: agent ↔ tool assignment
# ---------------------------------------------------------------------------
#
# Two endpoints on top of /agents/{id}/tools backed by the `agent_tools`
# M:N junction. Same gate pattern as the KB grants (tenant_admin for the
# write, tenant_member for the read) and the same global_builtin reject
# (those are platform-managed; fork first).
#
# The write is declarative: `PUT` replaces the agent's whole set in one
# transaction. An empty list clears all rows, which at enforcement time
# (Plan 06.15 task_06_15_02) restores the backward-compatible "no
# per-agent restriction" behaviour.
#
# Scope validation (Plan 06.15 decision):
#   * built-in tools (is_builtin)              -> assignable to any agent.
#   * custom tools (is_builtin=false)          -> only from the agent's
#       tenant. RLS already hides cross-tenant rows, so a lookup that
#       finds nothing is surfaced as a clean 422 (not a 404 — the body
#       carries an invalid tool_id, not a bad path).
#   * MCP tools (implementation_type=mcp_tool) -> only if the agent's
#       project declares that MCP server (matched by the server-name
#       prefix of `implementation_ref`). No project / no server -> 422.


async def _load_writable_agent_for_tools(
    session: AsyncSession,
    agent_id: UUID,
    principal: AuthPrincipal,
) -> Agent:
    """Load an agent for tool assignment; reject `global_builtin` (403).

    Mirrors `_load_writable_agent_for_kb`: built-ins are platform-managed
    and off-limits to tenant admins — fork first, assign on the fork.
    """
    agent = await get_writable_or_404(
        session, Agent, agent_id, principal, not_found_detail="agent not found"
    )
    if agent.scope == AgentScope.GLOBAL_BUILTIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "cannot assign tools to a global_builtin agent; "
                "fork it first and assign on the fork"
            ),
        )
    return agent


def _mcp_server_name(implementation_ref: str | None) -> str | None:
    """Extract the MCP server name from a tool's `implementation_ref`.

    MCP tools are namespaced `<server>.<tool>` (see the MCP server-name
    pattern in `mcp.config`). The server name is the prefix before the
    first dot; a ref with no dot is treated as the server name itself.
    """
    if not implementation_ref:
        return None
    return implementation_ref.split(".", 1)[0]


@router.get("/{agent_id}/tools", response_model=list[AgentToolResponse])
async def list_agent_tools(
    agent_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[AgentToolResponse]:
    """List the tools assigned to this agent via the `agent_tools` junction.

    Read shape mirrors the read-only `agent-tools-diagnostic` panel plus
    `is_builtin` (the básica/avanzada taxonomy) and the per-agent
    `config_override`. An agent with no rows returns ``[]`` (which at
    enforcement time means "no per-agent restriction").
    """
    # Surface 404 on a hidden/missing agent instead of an empty list — an
    # invisible agent would otherwise look like "no assignments" to the UI.
    agent_q = await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
    )
    if agent_q.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    rows = await session.execute(
        select(AgentTool.config_override, Tool)
        .join(Tool, Tool.id == AgentTool.tool_id)
        .where(
            AgentTool.agent_id == agent_id,
            Tool.deleted_at.is_(None),
        )
        .order_by(Tool.name, Tool.id)
    )
    return [
        AgentToolResponse(
            tool_id=tool.id,
            name=tool.name,
            description=tool.description,
            category=tool.category,
            implementation_type=tool.implementation_type,
            security_level=tool.security_level,
            is_builtin=tool.is_builtin,
            config_override=config_override,
        )
        for config_override, tool in rows.all()
    ]


@router.put("/{agent_id}/tools", response_model=list[AgentToolResponse])
async def set_agent_tools(
    agent_id: UUID,
    payload: SetAgentToolsRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[AgentToolResponse]:
    """Declaratively replace the agent's tool assignments (tenant_admin).

    The desired set is validated for scope, then the agent's existing
    `agent_tools` rows are deleted and the new set inserted in the same
    request transaction. Returns the resulting assignments.
    """
    require_tenant_id(principal)
    agent = await _load_writable_agent_for_tools(session, agent_id, principal)

    requested = {entry.tool_id: entry.config_override for entry in payload.tools}

    # Load every requested Tool in one query. RLS scopes the result to
    # platform built-ins + the caller's tenant, so a cross-tenant custom
    # tool simply won't appear here — caught by the "missing" check below.
    tools_by_id: dict[UUID, Tool] = {}
    if requested:
        tool_rows = await session.execute(
            select(Tool).where(
                Tool.id.in_(requested.keys()),
                Tool.deleted_at.is_(None),
            )
        )
        tools_by_id = {tool.id: tool for tool in tool_rows.scalars().all()}

    # Any id we couldn't load is either non-existent or a custom tool from
    # another tenant (hidden by RLS). Both are an invalid declarative set.
    missing = [tool_id for tool_id in requested if tool_id not in tools_by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "unknown or non-assignable tool_id(s): "
                + ", ".join(str(tool_id) for tool_id in missing)
            ),
        )

    # Reject a tool the agent-runtime cannot execute (ADR 0049, task_06_18_06):
    # assigning a builtin with no executor (apply_patch / search_code /
    # summarize_text, the retired git_*) would die as a silent `unknown tool`
    # at run time. We refuse it up front rather than at execution. Typed rows
    # (http_endpoint / python_function / docker_command / mcp_tool) are wired
    # from a serialised spec, so only non-executable builtins are rejected.
    not_wired = [
        tool
        for tool in tools_by_id.values()
        if not tool_is_runtime_wired(tool.name, tool.implementation_type)
    ]
    if not_wired:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "tool(s) not executable in the agent-runtime (no wired executor): "
                + ", ".join(sorted(tool.name for tool in not_wired))
            ),
        )

    # MCP tools require the agent's project to declare the matching server.
    mcp_tools = [
        tool
        for tool in tools_by_id.values()
        if tool.implementation_type == ToolImplementationType.MCP_TOOL.value
    ]
    if mcp_tools:
        project_server_names = await _agent_project_mcp_server_names(session, agent)
        for tool in mcp_tools:
            server = _mcp_server_name(tool.implementation_ref)
            if server is None or server not in project_server_names:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"MCP tool {tool.name!r} requires MCP server {server!r} "
                        "on the agent's project; not declared"
                    ),
                )

    # Replace the set transactionally: delete the old rows, insert new.
    await session.execute(delete(AgentTool).where(AgentTool.agent_id == agent_id))
    for tool_id, config_override in requested.items():
        session.add(
            AgentTool(
                agent_id=agent_id,
                tool_id=tool_id,
                config_override=config_override,
            )
        )
    await session.flush()

    return [
        AgentToolResponse(
            tool_id=tool.id,
            name=tool.name,
            description=tool.description,
            category=tool.category,
            implementation_type=tool.implementation_type,
            security_level=tool.security_level,
            is_builtin=tool.is_builtin,
            config_override=requested[tool.id],
        )
        for tool in sorted(tools_by_id.values(), key=lambda t: (t.name, str(t.id)))
    ]


async def _agent_project_mcp_server_names(
    session: AsyncSession,
    agent: Agent,
) -> set[str]:
    """Return the set of MCP server names declared on the agent's project.

    A non-`project_local` agent (template) has no project, hence no MCP
    servers — returns an empty set, which makes every MCP tool fail the
    scope check (you cannot assign MCP tools to a template agent).
    """
    if agent.project_id is None:
        return set()
    project_q = await session.execute(
        select(Project).where(Project.id == agent.project_id, Project.deleted_at.is_(None))
    )
    project = project_q.scalar_one_or_none()
    if project is None:
        return set()
    names: set[str] = set()
    for entry in project.mcp_servers or []:
        name = entry.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


# ---------------------------------------------------------------------------
# Plan 06.18 task_06_18_07: GET /agents/{id}/effective-tools
# ---------------------------------------------------------------------------
#
# The honest "what does the runtime really execute for this agent" contract —
# the frontier 06.17's Capability Hub (HACER) consumes without recomputing.
# It answers the operator's "I assigned a tool and nothing happens" by being
# the single place that:
#   * lists the agent's assigned tools, each flagged executable_in_runtime;
#   * computes the agent∩mode intersection through the SINGLE point
#     (`combine_tool_allowlists`, reused inside `compute_effective_tools`);
#   * crosses shell_exec with the project's `allowed_commands`;
#   * surfaces explicit, readable warnings instead of silent surprises.
#
# Field names are snake_case and stable: this is a contract. Read-only,
# tenant-scoped like the rest of the router (404 on a hidden/missing agent).


class EffectiveToolEntry(BaseModel):
    """One assigned tool, projected with its real runtime executability."""

    model_config = ConfigDict(populate_by_name=True)

    tool_id: UUID
    name: str
    #: Canonical name(s) the runtime resolves this assignment to (alias layer,
    #: ADR 0048): ``semantic_search`` → ``rag_search``, ``http_request`` →
    #: both verbs. Usually a single name; a list keeps the contract stable.
    canonical_names: list[str] = Field(default_factory=list)
    category: str
    implementation_type: str
    security_level: str
    is_builtin: bool
    #: Whether the agent-runtime can actually execute this tool (ADR 0049).
    #: A ``False`` here is the "asignada pero no ejecutable" case.
    executable_in_runtime: bool


class EffectiveToolsResponse(BaseModel):
    """The effective-tools contract (06.18 → 06.17).

    ``assigned`` lists every assignment with its executability; ``effective``
    is the sorted canonical set the runtime really wires for the requested
    ``mode`` (agent∩mode ∩ runtime-wired, plus ``shell_exec`` only when its
    project authorises commands). ``unrestricted`` is ``True`` for an agent with
    no per-agent assignment (backward-compatible 06.15 behaviour: the runtime
    keeps its default surface, so ``effective`` is empty by design).
    """

    model_config = ConfigDict(populate_by_name=True)

    agent_id: UUID
    #: The chat mode the effective set was computed against, or ``None`` when no
    #: mode was requested (the task-dispatch path carries no mode allowlist).
    mode: str | None = None
    assigned: list[EffectiveToolEntry] = Field(default_factory=list)
    effective: list[str] = Field(default_factory=list)
    #: ``True`` when the agent has no assignments (no per-agent restriction).
    unrestricted: bool = False
    #: Whether ``shell_exec`` is effective (assigned ∧ allowed_commands non-empty).
    shell_exec_effective: bool = False
    #: Readable notices: empty set in a mode, non-executable assignment,
    #: shell_exec without allowed_commands.
    warnings: list[str] = Field(default_factory=list)


@router.get("/{agent_id}/effective-tools", response_model=EffectiveToolsResponse)
async def get_agent_effective_tools(
    agent_id: UUID,
    mode: str | None = Query(
        default=None,
        description=(
            "Chat mode whose allowlist the effective set is intersected with "
            "(planning|discussion|execution|<custom>). Omit for no mode "
            "restriction (the task-dispatch path)."
        ),
    ),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> EffectiveToolsResponse:
    """Return the agent's honest effective tool set + per-tool executability.

    Tenant-scoped: RLS hides cross-tenant agents, so a hidden/missing agent
    surfaces as 404 (an empty body would otherwise look like "no tools").
    """
    # 404 on a hidden/missing agent (RLS handles cross-tenant).
    agent_q = await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
    )
    agent = agent_q.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    # Resolve the mode allowlist (None when no mode requested). An unknown mode
    # name is a 422 — the operator asked for a mode that does not exist.
    mode_allowed_tools: tuple[str, ...] | None = None
    if mode is not None:
        try:
            mode_config = resolve_mode_config(mode)
        except ValueError:
            # Built-in lookup miss (custom modes are resolved elsewhere with a
            # tenant registry). Treat an unknown built-in name as a bad request.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"unknown chat mode: {mode!r}",
            ) from None
        mode_allowed_tools = mode_config.allowed_tools

    # Load the assigned Tool rows (live only) — one query, projected to the
    # entry shape. `resolve_agent_tool_names` is the read seam for the *names*,
    # but we need the full rows for the per-tool entry + shell_exec detection.
    rows = await session.execute(
        select(Tool)
        .join(AgentTool, AgentTool.tool_id == Tool.id)
        .where(AgentTool.agent_id == agent_id, Tool.deleted_at.is_(None))
        .order_by(Tool.name, Tool.id)
    )
    assigned_tools = list(rows.scalars().all())

    # `None` (no rows) is the load-bearing "no per-agent restriction" sentinel,
    # exactly as `resolve_agent_tool_names` returns it.
    assigned_names: list[str] | None = [t.name for t in assigned_tools] if assigned_tools else None
    shell_exec_assigned = any(t.name == "shell_exec" for t in assigned_tools)

    # The canonical names that are *actually* runtime-wired, computed from the
    # full Tool rows so a typed custom tool (http_endpoint / python_function /
    # docker_command / mcp_tool) counts as wired regardless of name. This is the
    # honest "executable in runtime" set the pure computation intersects with.
    wired_canonical_names: set[str] = set()
    for tool in assigned_tools:
        if tool_is_runtime_wired(tool.name, tool.implementation_type):
            wired_canonical_names |= to_canonical_set([tool.name])

    # Whether the agent's project authorises any shell command (the cross that
    # makes shell_exec truly effective).
    allowed_commands_non_empty = False
    if agent.project_id is not None:
        project_q = await session.execute(
            select(Project.allowed_commands).where(
                Project.id == agent.project_id, Project.deleted_at.is_(None)
            )
        )
        commands = project_q.scalar_one_or_none()
        allowed_commands_non_empty = bool(commands)

    result = compute_effective_tools(
        assigned_names,
        mode_allowed_tools,
        mode_name=mode,
        shell_exec_assigned=shell_exec_assigned,
        allowed_commands_non_empty=allowed_commands_non_empty,
        wired_canonical_names=wired_canonical_names,
    )

    return EffectiveToolsResponse(
        agent_id=agent_id,
        mode=mode,
        assigned=[
            EffectiveToolEntry(
                tool_id=tool.id,
                name=tool.name,
                canonical_names=sorted(to_canonical_set([tool.name])),
                category=tool.category,
                implementation_type=tool.implementation_type,
                security_level=tool.security_level,
                is_builtin=tool.is_builtin,
                executable_in_runtime=tool_is_runtime_wired(tool.name, tool.implementation_type),
            )
            for tool in assigned_tools
        ],
        effective=result.effective,
        unrestricted=result.unrestricted,
        shell_exec_effective=result.shell_exec_effective,
        warnings=result.warnings,
    )
