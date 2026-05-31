"""``/human-agents`` endpoints — the Human Agents gallery (Plan 16 task_16_07).

A Human Agent is an :class:`~api_server.db.domain.Agent` with
``agent_type='human'`` and a 1:1 :class:`~api_server.db.domain.HumanAgentConfig`
row. This router treats the pair as ONE cohesive resource:

  GET    /human-agents                       list the tenant's Human Agents
  GET    /human-agents/templates             the global Human-Agent template catalog
  GET    /human-agents/assignable-users      the tenant's members (for the picker)
  POST   /human-agents                       create agent + config in one shot
  GET    /human-agents/{id}                  one Human Agent (agent + config)
  PUT    /human-agents/{id}                  edit agent + config
  DELETE /human-agents/{id}                  soft-delete (cascades the config)
  POST   /human-agents/templates/{id}/clone  clone-and-fork a global template

Auth model
----------
- Reads (`GET`) require an active tenant membership (`require_tenant_member`).
- Writes (`POST`/`PUT`/`DELETE`/clone) require `tenant_admin`.
- RLS scopes every tenant row to the caller's tenant. The global template
  catalog is the set of ``global_builtin`` Human Agents owned by the platform
  tenant, visible to all tenants via the ``agents_global_builtin_read`` SELECT
  policy (migration 0004).

Forking semantics (Plan 16 Decisiones Clave)
--------------------------------------------
A global Human-Agent template is ALWAYS forked into the tenant on clone — never
linked cross-tenant. The clone creates a NEW tenant-owned ``Agent`` (scope
``global_tenant_template``, ``forked_from_agent_id`` pointing at the source) and
a FRESH ``HumanAgentConfig`` in the tenant. The ``assigned_user_id`` is
intrinsically tenant-scoped, so the global template carries no config and the
fork starts unassigned (or pre-assigned via the request).
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
from api_server.db.domain import Agent, AgentScope, AgentType, HumanAgentConfig
from api_server.db.models import User, UserOrganizationMembership
from api_server.routers._helpers import (
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
)
from api_server.routers._pagination import apply_pagination, limit_query, offset_query
from api_server.schemas.human_agents import (
    DEFAULT_ASSIGNMENT_MODE,
    AssignableUserResponse,
    HumanAgentConfigUpdate,
    HumanAgentCreateRequest,
    HumanAgentForkRequest,
    HumanAgentResponse,
    HumanAgentUpdateRequest,
    to_human_agent_response,
)

router = APIRouter(prefix="/human-agents", tags=["human-agents"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
async def _load_config(session: AsyncSession, agent_id: UUID) -> HumanAgentConfig | None:
    """Load the 1:1 config row for a human agent (RLS-scoped)."""
    result = await session.execute(
        select(HumanAgentConfig).where(HumanAgentConfig.agent_id == agent_id)
    )
    return result.scalar_one_or_none()


async def _load_human_agent_or_404(session: AsyncSession, agent_id: UUID) -> Agent:
    """Load a human Agent visible to the caller, or 404.

    Restricts to ``agent_type='human'`` so the gallery never returns an AI
    agent by id (an AI agent would surface a confusing ``config=None``).
    """
    result = await session.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.agent_type == AgentType.HUMAN.value,
            Agent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="human agent not found")
    return agent


async def _configs_by_agent(
    session: AsyncSession, agent_ids: list[UUID]
) -> dict[UUID, HumanAgentConfig]:
    """Bulk-load configs for a set of agents (one round-trip)."""
    if not agent_ids:
        return {}
    result = await session.execute(
        select(HumanAgentConfig).where(HumanAgentConfig.agent_id.in_(agent_ids))
    )
    return {c.agent_id: c for c in result.scalars().all()}


# ---------------------------------------------------------------------------
# GET /human-agents — the tenant's own Human Agents
# ---------------------------------------------------------------------------
@router.get("", response_model=list[HumanAgentResponse])
async def list_human_agents(
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[HumanAgentResponse]:
    """List the caller tenant's Human Agents (NOT the global templates).

    Excludes ``global_builtin`` so the gallery's main list shows only the
    tenant's own (forked or hand-created) Human Agents; the template catalog
    lives under ``/human-agents/templates``.
    """
    stmt = (
        select(Agent)
        .where(
            Agent.agent_type == AgentType.HUMAN.value,
            Agent.scope != AgentScope.GLOBAL_BUILTIN.value,
            Agent.deleted_at.is_(None),
        )
        .order_by(Agent.created_at, Agent.id)
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    agents = list((await session.execute(stmt)).scalars().all())
    configs = await _configs_by_agent(session, [a.id for a in agents])
    return [to_human_agent_response(a, configs.get(a.id)) for a in agents]


# ---------------------------------------------------------------------------
# GET /human-agents/templates — global Human-Agent template catalog
# ---------------------------------------------------------------------------
@router.get("/templates", response_model=list[HumanAgentResponse])
async def list_human_agent_templates(
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[HumanAgentResponse]:
    """List the platform's global Human-Agent templates (clone-and-fork source).

    These are ``global_builtin`` Human Agents owned by the platform tenant,
    visible to every tenant via the ``agents_global_builtin_read`` SELECT
    policy. They carry NO config (assignment is tenant-intrinsic), so each
    response has ``config=None``.
    """
    stmt = (
        select(Agent)
        .where(
            Agent.agent_type == AgentType.HUMAN.value,
            Agent.scope == AgentScope.GLOBAL_BUILTIN.value,
            Agent.deleted_at.is_(None),
        )
        .order_by(Agent.name, Agent.id)
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    agents = list((await session.execute(stmt)).scalars().all())
    return [to_human_agent_response(a, None) for a in agents]


# ---------------------------------------------------------------------------
# GET /human-agents/assignable-users — the tenant's members (for the picker)
# ---------------------------------------------------------------------------
@router.get("/assignable-users", response_model=list[AssignableUserResponse])
async def list_assignable_users(
    limit: int = limit_query(),
    offset: int = offset_query(),
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[AssignableUserResponse]:
    """List the active members of the caller's tenant.

    Powers the ``assigned_user_id`` / ``escalation_target_user_id`` pickers in
    the gallery form. RLS scopes ``user_org_memberships`` to the tenant; the
    join to ``users`` (un-RLSed, but reached only for the membership rows the
    policy already let through) yields the email / full name to display.
    """
    tenant_id = require_tenant_id(principal)
    stmt = (
        select(
            UserOrganizationMembership.user_id,
            UserOrganizationMembership.role,
            User.email,
            User.full_name,
        )
        .join(User, User.id == UserOrganizationMembership.user_id)
        .where(
            UserOrganizationMembership.tenant_id == tenant_id,
            UserOrganizationMembership.is_active.is_(True),
            UserOrganizationMembership.deleted_at.is_(None),
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
        .order_by(User.email, UserOrganizationMembership.user_id)
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    rows = (await session.execute(stmt)).all()
    return [
        AssignableUserResponse(user_id=r.user_id, email=r.email, full_name=r.full_name, role=r.role)
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /human-agents/{id}
# ---------------------------------------------------------------------------
@router.get("/{agent_id}", response_model=HumanAgentResponse)
async def get_human_agent(
    agent_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> HumanAgentResponse:
    agent = await _load_human_agent_or_404(session, agent_id)
    config = await _load_config(session, agent.id)
    return to_human_agent_response(agent, config)


# ---------------------------------------------------------------------------
# POST /human-agents — create agent + config cohesively
# ---------------------------------------------------------------------------
@router.post("", response_model=HumanAgentResponse, status_code=status.HTTP_201_CREATED)
async def create_human_agent(
    payload: HumanAgentCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> HumanAgentResponse:
    """Create a Human Agent (``agent_type='human'``) + its config in one shot.

    The agent is a ``global_tenant_template`` (tenant-wide reusable). The
    config row carries the assignment / rate / notification / escalation
    settings. ``assignment_mode`` is fixed to ``specific_user`` (MVP).
    """
    tenant_id = require_tenant_id(principal)

    agent = Agent(
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        avatar_url=payload.avatar_url,
        agent_type=AgentType.HUMAN.value,
        role=payload.role,
        system_prompt=payload.system_prompt,
        scope=AgentScope.GLOBAL_TENANT_TEMPLATE.value,
        is_template=True,
    )
    session.add(agent)
    await session.flush()

    cfg = payload.config
    config = HumanAgentConfig(
        tenant_id=tenant_id,
        agent_id=agent.id,
        assignment_mode=DEFAULT_ASSIGNMENT_MODE,
        assigned_user_id=cfg.assigned_user_id,
        hourly_rate=cfg.hourly_rate,
        hourly_rate_currency=cfg.hourly_rate_currency,
        notification_channels=list(cfg.notification_channels),
        acceptance_timeout_hours=cfg.acceptance_timeout_hours,
        escalation_target_user_id=cfg.escalation_target_user_id,
        expected_response_time_hours=cfg.expected_response_time_hours,
        expected_execution_time_hours=cfg.expected_execution_time_hours,
    )
    session.add(config)
    await session.flush()
    await session.refresh(agent)
    await session.refresh(config)
    return to_human_agent_response(agent, config)


# ---------------------------------------------------------------------------
# PUT /human-agents/{id} — partial update of agent + config
# ---------------------------------------------------------------------------
def _apply_config_update(config: HumanAgentConfig, patch: HumanAgentConfigUpdate) -> None:
    """Mutate ``config`` with only the fields the caller actually sent."""
    changes = patch.model_dump(exclude_unset=True)
    for attr, value in changes.items():
        if attr == "notification_channels" and value is not None:
            setattr(config, attr, list(value))
        else:
            setattr(config, attr, value)


@router.put("/{agent_id}", response_model=HumanAgentResponse)
async def update_human_agent(
    agent_id: UUID,
    payload: HumanAgentUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> HumanAgentResponse:
    require_tenant_id(principal)
    agent = await get_writable_or_404(
        session,
        Agent,
        agent_id,
        principal,
        not_found_detail="human agent not found",
        extra_filters=(Agent.agent_type == AgentType.HUMAN.value,),
    )

    agent_changes = payload.model_dump(exclude_unset=True, exclude={"config"})
    for attr, value in agent_changes.items():
        setattr(agent, attr, value)

    config = await _load_config(session, agent.id)
    if payload.config is not None:
        if config is None:
            # A tenant-owned human agent should always have a config; if it
            # somehow lost it, materialise one from the patch + DB defaults.
            config = HumanAgentConfig(
                tenant_id=agent.tenant_id,
                agent_id=agent.id,
                assignment_mode=DEFAULT_ASSIGNMENT_MODE,
            )
            session.add(config)
        _apply_config_update(config, payload.config)

    await session.flush()
    await session.refresh(agent)
    if config is not None:
        await session.refresh(config)
    return to_human_agent_response(agent, config)


# ---------------------------------------------------------------------------
# DELETE /human-agents/{id} — soft delete (config cascades on hard delete; the
# soft-delete leaves the config orphaned but invisible — same as agents).
# ---------------------------------------------------------------------------
@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_human_agent(
    agent_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    require_tenant_id(principal)
    agent = await get_writable_or_404(
        session,
        Agent,
        agent_id,
        principal,
        not_found_detail="human agent not found",
        extra_filters=(Agent.agent_type == AgentType.HUMAN.value,),
    )
    await soft_delete(session, agent)


# ---------------------------------------------------------------------------
# POST /human-agents/templates/{source_id}/clone — clone-and-fork into tenant
# ---------------------------------------------------------------------------
@router.post(
    "/templates/{source_id}/clone",
    response_model=HumanAgentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def clone_human_agent_template(
    source_id: UUID,
    payload: HumanAgentForkRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> HumanAgentResponse:
    """Fork a global Human-Agent template into the caller's tenant.

    Forking (never linking) is mandatory — the assignment to a concrete User is
    intrinsically tenant-scoped (Plan 16 Decisiones Clave). The clone is a NEW
    tenant-owned ``global_tenant_template`` Agent with ``forked_from_agent_id``
    set, plus a FRESH config (unassigned, or pre-assigned via the request).
    """
    tenant_id = require_tenant_id(principal)

    # The source must be a global_builtin human template (visible via the
    # SELECT-only RLS policy). Restrict to that scope + agent_type so a tenant
    # cannot "clone a template" out of one of its own rows by id.
    src_result = await session.execute(
        select(Agent).where(
            Agent.id == source_id,
            Agent.agent_type == AgentType.HUMAN.value,
            Agent.scope == AgentScope.GLOBAL_BUILTIN.value,
            Agent.deleted_at.is_(None),
        )
    )
    source = src_result.scalar_one_or_none()
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="human agent template not found",
        )

    forked_from_version = source.updated_at.isoformat() if source.updated_at is not None else None

    fork = Agent(
        tenant_id=tenant_id,
        name=payload.name or source.name,
        description=source.description,
        avatar_url=source.avatar_url,
        agent_type=AgentType.HUMAN.value,
        role=source.role,
        system_prompt=source.system_prompt,
        # A forked human template lands as a tenant-wide template, reusable
        # across the tenant's projects (assignment to a user happens here).
        scope=AgentScope.GLOBAL_TENANT_TEMPLATE.value,
        is_template=True,
        forked_from_agent_id=source.id,
        forked_from_version=forked_from_version,
        anchored_version=None,
    )
    session.add(fork)
    await session.flush()

    # A fresh, tenant-owned config — NEVER linked to the global. The template's
    # planning estimates (response/execution time) ride along in model_config
    # if the seed set them; default everything else.
    estimates = source.model_config or {}
    config = HumanAgentConfig(
        tenant_id=tenant_id,
        agent_id=fork.id,
        assignment_mode=DEFAULT_ASSIGNMENT_MODE,
        assigned_user_id=payload.assigned_user_id,
        acceptance_timeout_hours=int(estimates.get("acceptance_timeout_hours", 24)),
        notification_channels=list(estimates.get("notification_channels", [])),
        expected_response_time_hours=estimates.get("expected_response_time_hours"),
        expected_execution_time_hours=estimates.get("expected_execution_time_hours"),
    )
    session.add(config)
    await session.flush()
    await session.refresh(fork)
    await session.refresh(config)
    return to_human_agent_response(fork, config)
