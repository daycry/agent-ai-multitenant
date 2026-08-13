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

from typing import Any
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
from api_server.db.domain import Agent, AgentScope, Project, Team, TeamMember
from api_server.routers._agent_names import flush_agent_or_conflict
from api_server.routers._helpers import (
    apply_partial_update,
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
)
from api_server.routers._integrity import flush_or_conflict
from api_server.routers.agents import _clone_agent_capabilities
from api_server.schemas.teams import (
    TeamAdoptRequest,
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
        memory_scope=payload.memory_scope.value if payload.memory_scope else None,
    )
    session.add(team)
    await flush_or_conflict(session, context="team.create")
    await session.refresh(team)
    return to_team_response(team, [])


# ---------------------------------------------------------------------------
# Ola C / ADR 0066: POST /teams/{source_id}/adopt
# ---------------------------------------------------------------------------
# Adopta un equipo built-in (o de otro origen visible) como COPIA editable del
# tenant. Crea un Team `is_builtin=false` enlazado al origen (`forked_from_*`),
# forkea cada miembro (persona + tools + skills, reusando el helper de fork por
# agente) al scope destino (project_local | global_tenant_template) y recrea los
# TeamMember. El built-in original queda intacto (read-only, global).
#
# La re-adopción crea copias nuevas, pero desde la migración 0126 NO cabe dos
# veces en el MISMO destino: ni el nombre del equipo ni el de sus miembros
# forkeados pueden repetirse en ese espacio de nombres. El equipo se renombra por
# la petición (`payload.name`); los miembros no tienen dónde, así que readoptar en
# el mismo proyecto responde 409 y hay que elegir otro destino o quitar de en
# medio los agentes que ya están. Ver `_agent_names`.
@router.post("/{source_id}/adopt", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def adopt_team(
    source_id: UUID,
    payload: TeamAdoptRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> TeamResponse:
    tenant_id = require_tenant_id(principal)

    # El origen puede ser un built-in global (visible vía RLS de SELECT) o un
    # equipo del propio tenant.
    src = (
        await session.execute(select(Team).where(Team.id == source_id, Team.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source team not found")

    # Resuelve scope destino + (opcional) proyecto del tenant.
    if payload.target == "project":
        project = (
            await session.execute(
                select(Project).where(
                    Project.id == payload.project_id,
                    Project.tenant_id == tenant_id,
                    Project.deleted_at.is_(None),
                    Project.is_template.is_(False),
                )
            )
        ).scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
        scope = AgentScope.PROJECT_LOCAL.value
        fork_project_id: UUID | None = payload.project_id
    else:
        scope = AgentScope.GLOBAL_TENANT_TEMPLATE.value
        fork_project_id = None

    new_team = await fork_team_into(
        session,
        src,
        tenant_id=tenant_id,
        scope=scope,
        project_id=fork_project_id,
        name=payload.name,
        llm_config=payload.llm_config,
        granted_by=principal.user_id,
    )
    await session.refresh(new_team)
    members = await _load_members(session, new_team.id)
    return to_team_response(new_team, members)


async def fork_team_into(
    session: AsyncSession,
    src: Team,
    *,
    tenant_id: UUID,
    scope: str,
    project_id: UUID | None,
    name: str | None,
    llm_config: dict[str, Any] | None,
    granted_by: UUID | None,
) -> Team:
    """Copia profunda de un equipo `src` a uno editable del tenant (Ola C / ADR
    0066): crea un Team enlazado por ``forked_from``, forkea cada miembro al
    ``scope`` dado (clonando KBs/tools/skills) y recrea los ``TeamMember``.
    Reutilizable por ``POST /teams/{id}/adopt`` y por la creación de proyecto con
    fork de equipo (ADR 0068). Devuelve el Team nuevo (flush hecho, sin refresh)."""
    team_version = src.updated_at.isoformat() if src.updated_at is not None else None
    new_team = Team(
        tenant_id=tenant_id,
        name=name or src.name,
        description=src.description,
        default_workflow_template_id=src.default_workflow_template_id,
        is_builtin=False,
        forked_from_team_id=src.id,
        forked_from_version=team_version,
        model_config=dict(llm_config or {}),
        memory_scope=src.memory_scope,  # ADR 0071: hereda la política del origen
    )
    session.add(new_team)
    # `name or src.name`: adoptar dos veces el mismo equipo sin renombrarlo choca
    # con `uq_teams_tenant_name_live` (misma migración 0126). El genérico basta
    # aquí porque el nombre del equipo SÍ es un campo de la petición.
    await flush_or_conflict(session, context="team.adopt")

    # Miembros del origen (team_members no tiene RLS; los agentes built-in son
    # visibles vía la policy de SELECT). Orden estable para reproducibilidad.
    src_members = (
        (
            await session.execute(
                select(TeamMember)
                .where(TeamMember.team_id == src.id)
                .order_by(TeamMember.assignment_priority, TeamMember.created_at)
            )
        )
        .scalars()
        .all()
    )

    for member in src_members:
        src_agent = (
            await session.execute(
                select(Agent).where(Agent.id == member.agent_id, Agent.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if src_agent is None:
            # Miembro no visible/borrado → se omite (no rompe el fork).
            continue

        agent_version = (
            src_agent.updated_at.isoformat() if src_agent.updated_at is not None else None
        )
        fork = Agent(
            tenant_id=tenant_id,
            name=src_agent.name,
            description=src_agent.description,
            avatar_url=src_agent.avatar_url,
            agent_type=src_agent.agent_type,
            role=src_agent.role,
            system_prompt=src_agent.system_prompt,
            model_config=dict(src_agent.model_config or {}),
            memory_scope=src_agent.memory_scope,
            review_capability=src_agent.review_capability,
            max_concurrent_tasks=src_agent.max_concurrent_tasks,
            is_template=False,
            scope=scope,
            project_id=project_id,
            forked_from_agent_id=src_agent.id,
            forked_from_version=agent_version,
            anchored_version=None,
        )
        session.add(fork)
        # Cada MIEMBRO se forkea con el nombre del origen y la petición no tiene
        # dónde renombrarlo: readoptar el mismo equipo al mismo destino choca aquí
        # aunque el equipo se haya renombrado. El `hint` dice qué se puede hacer,
        # porque la sugerencia de nombre sola no es accionable en esta ruta.
        await flush_agent_or_conflict(
            session,
            context="team.adopt.member",
            name=fork.name,
            tenant_id=tenant_id,
            project_id=project_id,
            hint=(
                "Es un miembro del equipo que estás adoptando: adopta el equipo en"
                " otro proyecto, o renombra/borra el agente que ya existe."
            ),
        )
        # Clona SABER/HACER/SER (KBs/tools/skills) del agente origen al fork.
        await _clone_agent_capabilities(
            session,
            source_id=src_agent.id,
            fork_id=fork.id,
            tenant_id=tenant_id,
            granted_by=granted_by,
        )
        session.add(
            TeamMember(
                team_id=new_team.id,
                agent_id=fork.id,
                role_in_team=member.role_in_team,
                is_team_leader=member.is_team_leader,
                assignment_priority=member.assignment_priority,
            )
        )

    await session.flush()
    return new_team


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

    # `llm_config` (JSON `model_config`) → columna `model_config` (Ola A);
    # `chat_llm_config` (JSON `chat_model_config`) → columna `chat_model_config`.
    # `memory_scope` (ADR 0071): enum → string; `null` explícito quita la política.
    apply_partial_update(
        team,
        payload,
        rename={"llm_config": "model_config", "chat_llm_config": "chat_model_config"},
        enum_fields=("memory_scope",),
    )

    await flush_or_conflict(session, context="team.update")
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
