"""Built-in team templates (task_01_12).

Five templates per spec §6.3, with sensible role assignments using the
eleven built-in agents seeded in task_01_09. Each team carries a
suggested member set with `is_team_leader=true` on the PM and an
`assignment_priority` that hints at queue ordering when multiple
members can take a task.

Like skills/tools, team rows live under the platform tenant with
`is_builtin=true`; the `teams_builtin_read` policy added in 0006
exposes them to every tenant. Forking a team (cloning it into a
tenant-owned project) lands in task_01_15+.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.seeds import (
    PLATFORM_TENANT_ID,
    TEAM_SEED_NAMESPACE,
)
from api_server.seeds.builtin_agents import _agent_id as builtin_agent_id


def _team_id(slug: str) -> UUID:
    return uuid5(TEAM_SEED_NAMESPACE, f"team:{slug}")


@dataclass(frozen=True)
class TeamMemberDef:
    agent_slug: str
    role_in_team: str
    is_team_leader: bool = False
    assignment_priority: int = 100


@dataclass(frozen=True)
class BuiltinTeam:
    slug: str
    name: str
    description: str
    members: tuple[TeamMemberDef, ...]

    @property
    def id(self) -> UUID:
        return _team_id(self.slug)


BUILTIN_TEAMS: tuple[BuiltinTeam, ...] = (
    BuiltinTeam(
        slug="full-stack-web",
        name="Equipo Full-Stack Web",
        description=(
            "Pod completo para webapps: planificación, arquitectura, "
            "backend, frontend, QA y revisión."
        ),
        members=(
            TeamMemberDef("project-manager", "PM", is_team_leader=True, assignment_priority=10),
            TeamMemberDef("architect", "Arquitecto", assignment_priority=20),
            TeamMemberDef("backend-senior", "Backend Lead", assignment_priority=30),
            TeamMemberDef("frontend-dev", "Frontend", assignment_priority=40),
            TeamMemberDef("qa-engineer", "QA", assignment_priority=60),
            TeamMemberDef("reviewer", "Reviewer", assignment_priority=70),
        ),
    ),
    BuiltinTeam(
        slug="backend-api",
        name="Equipo Backend / API",
        description=(
            "Pod centrado en APIs y servicios: PM, Arquitecto, "
            "dos Backend Devs (Sr+Jr), QA y Reviewer."
        ),
        members=(
            TeamMemberDef("project-manager", "PM", is_team_leader=True, assignment_priority=10),
            TeamMemberDef("architect", "Arquitecto", assignment_priority=20),
            TeamMemberDef("backend-senior", "Backend Senior", assignment_priority=30),
            TeamMemberDef("backend-junior", "Backend Junior", assignment_priority=50),
            TeamMemberDef("qa-engineer", "QA", assignment_priority=60),
            TeamMemberDef("reviewer", "Reviewer", assignment_priority=70),
        ),
    ),
    BuiltinTeam(
        slug="research-spec",
        name="Equipo Research & Spec",
        description=(
            "Pod para investigación, evaluación de opciones y " "redacción de especificaciones."
        ),
        members=(
            TeamMemberDef("project-manager", "PM", is_team_leader=True, assignment_priority=10),
            TeamMemberDef("researcher", "Investigador", assignment_priority=20),
            TeamMemberDef("architect", "Arquitecto", assignment_priority=30),
            TeamMemberDef("technical-writer", "Technical Writer", assignment_priority=40),
        ),
    ),
    BuiltinTeam(
        slug="devops-platform",
        name="Equipo DevOps & Platform",
        description=(
            "Pod de plataforma: CI/CD, infraestructura, "
            "observabilidad, seguridad y QA de operación."
        ),
        members=(
            TeamMemberDef("project-manager", "Líder", is_team_leader=True, assignment_priority=10),
            TeamMemberDef("devops-engineer", "DevOps", assignment_priority=20),
            TeamMemberDef("security-specialist", "Security Specialist", assignment_priority=30),
            TeamMemberDef("qa-engineer", "QA", assignment_priority=50),
        ),
    ),
    BuiltinTeam(
        slug="data",
        name="Equipo Data",
        description=(
            "Pod orientado a datos: pipelines, modelado, " "ingestión y validación de calidad."
        ),
        members=(
            TeamMemberDef("project-manager", "PM", is_team_leader=True, assignment_priority=10),
            TeamMemberDef("backend-senior", "Data Engineer", assignment_priority=20),
            TeamMemberDef("researcher", "Investigador", assignment_priority=40),
            TeamMemberDef("qa-engineer", "QA", assignment_priority=60),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
_UPSERT_TEAM_SQL = text(
    """
    INSERT INTO teams (id, tenant_id, name, description, is_builtin)
    VALUES (:id, :tenant_id, :name, :description, true)
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        updated_at = now()
    """
)

_UPSERT_MEMBER_SQL = text(
    """
    INSERT INTO team_members (
        team_id, agent_id, role_in_team, is_team_leader, assignment_priority
    )
    VALUES (:team_id, :agent_id, :role_in_team, :is_team_leader, :priority)
    ON CONFLICT (team_id, agent_id) DO UPDATE SET
        role_in_team = EXCLUDED.role_in_team,
        is_team_leader = EXCLUDED.is_team_leader,
        assignment_priority = EXCLUDED.assignment_priority,
        updated_at = now()
    """
)

_DELETE_STALE_MEMBERS_SQL = text(
    """
    DELETE FROM team_members
     WHERE team_id = :team_id
       AND agent_id <> ALL(:keep_ids)
    """
)


async def seed_builtin_teams(session: AsyncSession) -> int:
    for team in BUILTIN_TEAMS:
        await session.execute(
            _UPSERT_TEAM_SQL,
            {
                "id": str(team.id),
                "tenant_id": str(PLATFORM_TENANT_ID),
                "name": team.name,
                "description": team.description,
            },
        )
        for member in team.members:
            await session.execute(
                _UPSERT_MEMBER_SQL,
                {
                    "team_id": str(team.id),
                    "agent_id": str(builtin_agent_id(member.agent_slug)),
                    "role_in_team": member.role_in_team,
                    "is_team_leader": member.is_team_leader,
                    "priority": member.assignment_priority,
                },
            )
        # Drop any member rows that aren't in the current spec for this team
        # -- handles the case where we shrink a team between seed releases.
        keep_ids = [str(builtin_agent_id(m.agent_slug)) for m in team.members]
        await session.execute(
            _DELETE_STALE_MEMBERS_SQL,
            {"team_id": str(team.id), "keep_ids": keep_ids},
        )
    return len(BUILTIN_TEAMS)
