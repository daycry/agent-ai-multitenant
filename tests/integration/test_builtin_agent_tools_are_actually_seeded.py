"""Lo que el catálogo DECLARA acaba de verdad en `agent_tools`.

`tests/unit/test_builtin_prompt_tool_coherence.py` fija lo que cada agente
built-in debe tener, pero lo lee de ``resolved_tool_slugs()`` — o sea de la
declaración. Una declaración impecable que ningún paso del seed llega a escribir
es una guarda que pasa vacía, y no es hipotético: el **QA E2E Automator** vive
fuera de ``BUILTIN_AGENTS`` para no mover un conteo, el cableado de tools iteraba
esa tupla, y así se quedó con CERO filas en `agent_tools` desde el día que se
sembró — con un prompt que le ordena escribir specs Playwright.

Este fichero cierra ese hueco por el único sitio donde no se puede fingir: corre
los pasos REALES del runner contra PostgreSQL y compara lo sembrado con lo
declarado, agente a agente.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import asyncpg
import pytest
from alembic import command
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_TRUNCATE = (
    "TRUNCATE agent_skills, agent_tools, team_members, teams, agents, skills,"
    " tools, organizations CASCADE"
)


def _as_async_dsn(dsn: str) -> str:
    return dsn.replace("postgres://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


def _declared() -> dict[UUID, tuple[str, frozenset[UUID]]]:
    """``agent_id -> (slug, tool_ids declarados)`` para los TRES seeds."""
    from api_server.seeds.builtin_agents import BUILTIN_AGENTS
    from api_server.seeds.builtin_tools import _tool_id
    from api_server.seeds.ci4_team import CI4_AGENTS
    from api_server.seeds.qa_e2e_automator import QA_E2E_AUTOMATOR

    out: dict[UUID, tuple[str, frozenset[UUID]]] = {}
    for agent in (*BUILTIN_AGENTS, QA_E2E_AUTOMATOR, *CI4_AGENTS):
        out[agent.id] = (
            agent.slug,
            frozenset(_tool_id(slug) for slug in agent.resolved_tool_slugs()),
        )
    return out


async def _seed_the_real_steps(dsn: str) -> None:
    """Corre los pasos del runner que tocan agentes y tools, en su orden real.

    Se toman de ``SEED_STEPS`` por CLAVE, no se reescribe la lista: una copia a
    mano dejaría de reflejar el runner justo el día que alguien añada un paso —
    que es exactamente el fallo que este fichero persigue.
    """
    from api_server.seeds.__main__ import SEED_STEPS, run_seeds

    wanted = (
        "platform_tenant",
        "agents",
        "qa_e2e_automator",
        "ci4_agents",
        "tools",
        "agent_tools",
        "qa_e2e_automator_tools",
        "ci4_agent_tools",
    )
    by_key = {step.key: step for step in SEED_STEPS}
    missing = [key for key in wanted if key not in by_key]
    assert not missing, f"el runner perdió pasos que este test necesita: {missing}"
    steps = tuple(by_key[key] for key in wanted)

    engine = create_async_engine(_as_async_dsn(dsn), pool_pre_ping=False)
    try:
        await run_seeds(async_sessionmaker(engine, expire_on_commit=False), steps=steps)
    finally:
        await engine.dispose()


async def _seeded(dsn: str) -> dict[UUID, set[UUID]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT agent_id, tool_id FROM agent_tools")
    finally:
        await conn.close()
    out: dict[UUID, set[UUID]] = {}
    for row in rows:
        out.setdefault(row["agent_id"], set()).add(row["tool_id"])
    return out


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(_TRUNCATE)
    finally:
        await conn.close()


def test_every_builtin_agent_gets_exactly_the_tools_it_declares(
    alembic_config: Any, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_seed_the_real_steps(migrations_pg_dsn))
    seeded = asyncio.run(_seeded(migrations_pg_dsn))

    problems: list[str] = []
    for agent_id, (slug, declared) in _declared().items():
        got = seeded.get(agent_id, set())
        if got != declared:
            problems.append(
                f"{slug}: faltan {sorted(str(t) for t in declared - got)}, "
                f"sobran {sorted(str(t) for t in got - declared)}"
            )
    assert not problems, "el seed no escribe lo que el catálogo declara:\n  " + "\n  ".join(
        sorted(problems)
    )


def test_no_builtin_agent_ends_up_with_zero_tools(
    alembic_config: Any, migrations_pg_dsn: str
) -> None:
    """La forma cruda del fallo, por si la comparación exacta se relajara.

    Un agente built-in sin NINGUNA tool no puede leer un fichero: no es un
    reparto discutible, es un agente que no puede trabajar.
    """
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_seed_the_real_steps(migrations_pg_dsn))
    seeded = asyncio.run(_seeded(migrations_pg_dsn))

    empty = sorted(slug for agent_id, (slug, _d) in _declared().items() if not seeded.get(agent_id))
    assert not empty, f"agentes built-in sembrados sin una sola tool: {empty}"
