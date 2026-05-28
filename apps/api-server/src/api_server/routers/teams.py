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

from api_server.auth.deps import AuthPrincipal, get_principal, get_tenant_session
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
    _: AuthPrincipal = Depends(get_principal),
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
    _: AuthPrincipal = Depends(get_principal),
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
    principal: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> TeamResponse:
    tenant_id = require_tenant_id(principal)
    team = Team(
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        default_workflow_template_id=payload.default_workflow_template_id,
        shared_memory_namespace=payload.shared_memory_namespace,
    )
    session.add(team)
    await session.flush()
    await session.refresh(team)
    return to_team_response(team, [])


@router.put("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: UUID,
    payload: TeamUpdateRequest,
    principal: AuthPrincipal = Depends(get_principal),
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
    principal: AuthPrincipal = Depends(get_principal),
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
    principal: AuthPrincipal = Depends(get_principal),
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
    principal: AuthPrincipal = Depends(get_principal),
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
    principal: AuthPrincipal = Depends(get_principal),
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
