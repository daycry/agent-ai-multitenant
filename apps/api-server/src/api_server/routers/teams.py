"""`/teams` endpoints + member management.

Routes:

    GET    /teams                          -> list teams (with members)
    GET    /teams/{id}                     -> team detail + members
    POST   /teams                          -> create
    PUT    /teams/{id}                     -> update metadata
    DELETE /teams/{id}                     -> soft-delete

    POST   /teams/{id}/members             -> add an agent to the team
    PUT    /teams/{id}/members/{agent_id}  -> update per-team metadata
    DELETE /teams/{id}/members/{agent_id}  -> remove the agent

Adding a `global_builtin` agent works -- RLS exposes those rows on read,
and the FK accepts the agent_id. The membership row itself stays
tenant-scoped via its parent Team.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.capabilities import (
    WARN_TEAM_NO_MEMBERS,
    CapabilitiesResponse,
    CapabilityHacer,
    CapabilityKB,
    CapabilityRecordar,
    CapabilitySaber,
    CapabilityWarning,
    hacer_for_agent,
    kbs_for_agent_role,
    kbs_for_project,
    memory_counts,
    merge_kbs,
)
from api_server.db.domain import Agent, Team, TeamMember
from api_server.routers._helpers import (
    apply_partial_update,
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
)
from api_server.schemas.teams import (
    TeamCreateRequest,
    TeamMemberAddRequest,
    TeamMemberUpdateRequest,
    TeamResponse,
    TeamUpdateRequest,
    to_team_response,
)

router = APIRouter(prefix="/teams", tags=["teams"])


# ---------------------------------------------------------------------------
# Team-specific helpers (the generic ones live in _helpers.py)
# ---------------------------------------------------------------------------
async def _load_members(session: AsyncSession, team_id: UUID) -> list[TeamMember]:
    result = await session.execute(
        select(TeamMember)
        .where(TeamMember.team_id == team_id)
        .order_by(TeamMember.assignment_priority, TeamMember.created_at)
    )
    return list(result.scalars().all())


async def _verify_agent_visible(session: AsyncSession, agent_id: UUID) -> Agent:
    """RLS-aware lookup. Built-in agents are visible to all tenants;
    other-tenant agents are not. Used before inserting a TeamMember so
    the API can return a clean 404 instead of a Postgres FK error
    message."""
    result = await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    return agent


# ---------------------------------------------------------------------------
# Teams CRUD
# ---------------------------------------------------------------------------
@router.get("", response_model=list[TeamResponse])
async def list_teams(
    q: str | None = Query(
        default=None,
        min_length=1,
        max_length=200,
        description="Case-insensitive substring match on team name (used by the TeamCombobox).",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description="Max teams returned. Small for typeahead, default 100 for the listing page.",
    ),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[TeamResponse]:
    stmt = select(Team).where(Team.deleted_at.is_(None))
    if q is not None:
        stmt = stmt.where(Team.name.ilike(f"%{q}%"))
    stmt = stmt.order_by(Team.created_at).limit(limit)
    teams_res = await session.execute(stmt)
    teams = list(teams_res.scalars().all())

    if not teams:
        return []

    # One round-trip for all members of the visible teams.
    member_res = await session.execute(
        select(TeamMember)
        .where(TeamMember.team_id.in_([t.id for t in teams]))
        .order_by(TeamMember.assignment_priority, TeamMember.created_at)
    )
    by_team: dict[UUID, list[TeamMember]] = {}
    for m in member_res.scalars().all():
        by_team.setdefault(m.team_id, []).append(m)

    return [to_team_response(t, by_team.get(t.id, [])) for t in teams]


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> TeamResponse:
    result = await session.execute(
        select(Team).where(Team.id == team_id, Team.deleted_at.is_(None))
    )
    team = result.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team not found")
    members = await _load_members(session, team_id)
    return to_team_response(team, members)


@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    payload: TeamCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> TeamResponse:
    tenant_id = require_tenant_id(principal)
    team = Team(
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        default_workflow_template_id=payload.default_workflow_template_id,
    )
    session.add(team)
    await session.flush()
    await session.refresh(team)
    return to_team_response(team, [])


@router.put("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: UUID,
    payload: TeamUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> TeamResponse:
    require_tenant_id(principal)
    team = await get_writable_or_404(
        session, Team, team_id, principal, not_found_detail="team not found"
    )

    apply_partial_update(team, payload)

    await session.flush()
    await session.refresh(team)
    members = await _load_members(session, team_id)
    return to_team_response(team, members)


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    require_tenant_id(principal)
    team = await get_writable_or_404(
        session, Team, team_id, principal, not_found_detail="team not found"
    )
    await soft_delete(session, team)


# ---------------------------------------------------------------------------
# Members sub-routes
# ---------------------------------------------------------------------------
@router.post(
    "/{team_id}/members",
    response_model=TeamResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    team_id: UUID,
    payload: TeamMemberAddRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> TeamResponse:
    require_tenant_id(principal)
    team = await get_writable_or_404(
        session, Team, team_id, principal, not_found_detail="team not found"
    )
    await _verify_agent_visible(session, payload.agent_id)

    member = TeamMember(
        team_id=team.id,
        agent_id=payload.agent_id,
        role_in_team=payload.role_in_team,
        is_team_leader=payload.is_team_leader,
        assignment_priority=payload.assignment_priority,
    )
    session.add(member)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="agent is already a member of this team",
        ) from exc

    members = await _load_members(session, team.id)
    return to_team_response(team, members)


@router.put(
    "/{team_id}/members/{agent_id}",
    response_model=TeamResponse,
)
async def update_member(
    team_id: UUID,
    agent_id: UUID,
    payload: TeamMemberUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> TeamResponse:
    require_tenant_id(principal)
    team = await get_writable_or_404(
        session, Team, team_id, principal, not_found_detail="team not found"
    )

    result = await session.execute(
        select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.agent_id == agent_id)
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="membership not found")

    for attr, value in payload.model_dump(exclude_unset=True).items():
        setattr(member, attr, value)
    await session.flush()

    members = await _load_members(session, team.id)
    return to_team_response(team, members)


@router.delete(
    "/{team_id}/members/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    team_id: UUID,
    agent_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    require_tenant_id(principal)
    team = await get_writable_or_404(
        session, Team, team_id, principal, not_found_detail="team not found"
    )

    result = await session.execute(
        select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.agent_id == agent_id)
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="membership not found")
    await session.delete(member)
    await session.flush()


# ---------------------------------------------------------------------------
# Plan 06.17 task_06_17_08: GET /teams/{id}/capabilities (ADR 0053)
# ---------------------------------------------------------------------------
#
# El Hub de Capacidad por equipo. Según ADR 0053 (Opción B) NO existe un
# subsistema TeamKnowledgeBase: la capacidad de equipo es la UNIÓN AGREGADA
# read-only de lo que ya saben/pueden sus MIEMBROS. SABER = unión de las KBs de
# rol + stack de cada agente miembro; HACER = unión del set efectivo de tools de
# cada miembro (delegando en `compute_effective_tools` de 06.18, no recalculando).
# RECORDAR = memoria `team_shared` del equipo + `global`. Honestidad de estado:
# un equipo sin miembros lo avisa explícitamente. Read-only, tenant-scoped: RLS
# oculta equipos cross-tenant, así que un equipo oculto/inexistente → 404.
@router.get("/{team_id}/capabilities", response_model=CapabilitiesResponse)
async def get_team_capabilities(
    team_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> CapabilitiesResponse:
    """Devuelve la capacidad de equipo AGREGADA de sus miembros (ADR 0053).

    Sin persistencia de equipo nueva: agrega read-only las KBs y tools efectivas
    de los agentes miembros. Avisa honestamente cuando el equipo no tiene
    miembros (no finge capacidad).
    """
    team_q = await session.execute(
        select(Team).where(Team.id == team_id, Team.deleted_at.is_(None))
    )
    team = team_q.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team not found")

    # Miembros visibles (built-ins incluidos via RLS de SELECT).
    member_rows = await session.execute(
        select(Agent)
        .join(TeamMember, TeamMember.agent_id == Agent.id)
        .where(TeamMember.team_id == team_id, Agent.deleted_at.is_(None))
        .order_by(Agent.name, Agent.id)
    )
    members = list(member_rows.scalars().all())

    warnings: list[CapabilityWarning] = []
    if not members:
        warnings.append(
            CapabilityWarning(
                code=WARN_TEAM_NO_MEMBERS,
                es=(
                    "equipo sin miembros: aún no hay conocimiento ni tools de equipo "
                    "(la capacidad de equipo es la unión de la de sus miembros, ADR 0053)"
                ),
                en=(
                    "team has no members: no team knowledge or tools yet "
                    "(team capability is the union of its members', ADR 0053)"
                ),
            )
        )

    # SABER agregado: union de rol mas stack de cada miembro.
    kb_lists: list[list[CapabilityKB]] = []
    effective_tools: set[str] = set()
    shell_exec_effective = False
    for agent in members:
        kb_lists.append(await kbs_for_agent_role(session, agent_id=agent.id))
        if agent.project_id is not None:
            kb_lists.append(await kbs_for_project(session, project_id=agent.project_id))
        member_hacer, _member_warnings = await hacer_for_agent(session, agent=agent)
        effective_tools |= set(member_hacer.effective)
        shell_exec_effective = shell_exec_effective or member_hacer.shell_exec_effective

    saber = CapabilitySaber(knowledge_bases=merge_kbs(*kb_lists) if kb_lists else [])

    # RECORDAR: memoria team_shared del equipo + global.
    recordar = CapabilityRecordar(
        memory_scope=None,
        memory=await memory_counts(session, team_id=team_id),
    )

    return CapabilitiesResponse(
        entity_type="team",
        entity_id=team.id,
        saber=saber,
        recordar=recordar,
        ser=None,
        hacer=CapabilityHacer(
            effective=sorted(effective_tools),
            unrestricted=not members,
            shell_exec_effective=shell_exec_effective,
        ),
        warnings=warnings,
    )
