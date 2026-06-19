"""Guardia (Ola B): todo agente de un equipo built-in tiene ≥1 tool y ≥1 skill.

Antes de la Ola B había dos huecos simétricos: el equipo CI4 cableaba tools pero
NO skills, y los agentes built-in sueltos (miembros de los equipos de
builtin_teams) tenían skills pero NO tools. Resultado: equipos built-in "a
medias". Esta guardia evita la regresión componiendo solo los seeders relevantes
(sin la ingesta de catálogo, que usa Ollama)."""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

_TRUNCATE = (
    "TRUNCATE agent_skills, agent_tools, team_members, teams, agents, skills,"
    " tools, organizations CASCADE"
)
_QUERY = (
    "SELECT a.name,"
    " (SELECT count(*) FROM agent_tools t WHERE t.agent_id = a.id) AS tools,"
    " (SELECT count(*) FROM agent_skills s WHERE s.agent_id = a.id) AS skills"
    " FROM agents a"
    " JOIN team_members tm ON tm.agent_id = a.id"
    " JOIN teams te ON te.id = tm.team_id"
    " WHERE te.is_builtin = true"
)


def _as_async_dsn(dsn: str) -> str:
    return dsn.replace("postgres://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


async def _seed_subset(dsn: str) -> None:
    from api_server.seeds.builtin_agents import (
        seed_builtin_agent_skills,
        seed_builtin_agent_tools,
        seed_builtin_agents,
    )
    from api_server.seeds.builtin_skills import seed_builtin_skills
    from api_server.seeds.builtin_teams import seed_builtin_teams
    from api_server.seeds.builtin_tools import seed_builtin_tools
    from api_server.seeds.ci4_team import (
        seed_ci4_agent_skills,
        seed_ci4_agent_tools,
        seed_ci4_agents,
        seed_ci4_team,
    )
    from api_server.seeds.platform import ensure_platform_tenant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await ensure_platform_tenant(session)
            await seed_builtin_skills(session)
            await seed_builtin_tools(session)
            await seed_builtin_agents(session)
            await seed_builtin_agent_skills(session)
            await seed_builtin_agent_tools(session)
            await seed_ci4_agents(session)
            await seed_ci4_agent_tools(session)
            await seed_ci4_agent_skills(session)
            await seed_builtin_teams(session)
            await seed_ci4_team(session)
    finally:
        await engine.dispose()


async def _truncate_and_query(dsn: str) -> list[tuple[str, int, int]]:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(_TRUNCATE)
    finally:
        await conn.close()
    await _seed_subset(_as_async_dsn(dsn))
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(_QUERY)
    finally:
        await conn.close()
    return [(r["name"], r["tools"], r["skills"]) for r in rows]


def test_every_builtin_team_agent_has_tool_and_skill(
    alembic_config, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")
    rows = asyncio.run(_truncate_and_query(migrations_pg_dsn))
    assert rows, "no hay agentes de equipos built-in tras el seed"
    empty = [(name, t, s) for (name, t, s) in rows if t == 0 or s == 0]
    assert not empty, f"agentes de equipo built-in sin tool/skill: {empty}"
