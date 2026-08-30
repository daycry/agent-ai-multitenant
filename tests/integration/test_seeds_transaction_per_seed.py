"""prod-13 · task_prod13_09 — el runner de seeds usa UNA transacción por seed.

Hallazgo db-8. `python -m api_server.seeds` envolvía los ~20 seeds en un único
`session.begin()`, con dos consecuencias:

  1. **Una transacción abierta durante llamadas de red.** `seed_catalog_ingestion`
     embebe el corpus contra Ollama dentro de esa transacción: una conexión
     retenida (y sus locks) durante minutos de I/O externo.
  2. **Todo o nada.** Si el seed nº 18 falla, los 17 anteriores se van con él —
     aunque fueran correctos e idempotentes. En un arranque con Ollama caído eso
     dejaba la instalación SIN agentes, SIN equipos y SIN tools por un fallo en
     la ingesta del catálogo, que es la parte prescindible.

Lo que se comprueba, y por qué no basta con menos:

  * un fallo a mitad NO tira lo ya sembrado — es la diferencia observable entre
    una transacción y N, y un test que solo contara filas tras un run feliz
    pasaría igual con el código viejo;
  * el catálogo commitea POR DOCUMENTO: un corpus que revienta al tercero
    conserva los dos primeros;
  * los pasos siguen en el ORDEN que exigen las FKs (agentes antes que equipos,
    KBs antes que el catálogo), que es lo que un refactor de este tipo rompe.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import pytest
from alembic import command
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


def _as_async_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    return dsn.replace("postgres://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE chunks, documents, knowledge_bases, team_members, teams, "
            "agents, organizations CASCADE"
        )
    finally:
        await conn.close()


async def _count(dsn: str, table: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(await conn.fetchval(f"SELECT count(*) FROM {table}"))
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 1. Un fallo a mitad no tira lo ya sembrado
# ---------------------------------------------------------------------------
def test_a_failing_seed_does_not_roll_back_the_previous_ones(
    alembic_config: Any, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    from api_server.seeds.__main__ import SeedStep, run_seeds
    from api_server.seeds.builtin_agents import seed_builtin_agents
    from api_server.seeds.platform import ensure_platform_tenant

    async def _boom(_session: AsyncSession) -> int:
        raise RuntimeError("Ollama caído a mitad del arranque")

    steps = (
        SeedStep("platform_tenant", ensure_platform_tenant),
        SeedStep("agents", seed_builtin_agents),
        SeedStep("boom", _boom),
    )

    async def _run() -> None:
        engine = create_async_engine(_as_async_dsn(migrations_pg_dsn), pool_pre_ping=False)
        try:
            sm = async_sessionmaker(engine, expire_on_commit=False)
            with pytest.raises(RuntimeError):
                await run_seeds(sm, steps=steps)
        finally:
            await engine.dispose()

    asyncio.run(_run())

    # Con una sola transacción global esto sería 0 y 0.
    assert asyncio.run(_count(migrations_pg_dsn, "organizations")) >= 1
    assert asyncio.run(_count(migrations_pg_dsn, "agents")) == 11


# ---------------------------------------------------------------------------
# 2. El catálogo commitea por documento
# ---------------------------------------------------------------------------
def test_catalog_ingestion_commits_per_document(
    alembic_config: Any, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    import api_server.seeds.catalog_ingestion as cat
    from api_server.ingestion.embeddings import HashEmbedder
    from api_server.seeds.__main__ import SeedStep, run_seeds
    from api_server.seeds.builtin_kb_categories import seed_builtin_kb_categories
    from api_server.seeds.builtin_kbs import seed_builtin_kbs
    from api_server.seeds.platform import ensure_platform_tenant

    original = cat._ingest_one
    seen: list[str] = []

    async def _fail_on_third(session: AsyncSession, **kwargs: Any) -> Any:
        seen.append(str(kwargs["slug"]))
        if len(seen) == 3:
            raise RuntimeError("corpus corrupto en el tercero")
        return await original(session, **kwargs)

    monkeypatch.setattr(cat, "_ingest_one", _fail_on_third)

    async def _run() -> None:
        engine = create_async_engine(_as_async_dsn(migrations_pg_dsn), pool_pre_ping=False)
        try:
            sm = async_sessionmaker(engine, expire_on_commit=False)
            await run_seeds(
                sm,
                steps=(
                    SeedStep("platform_tenant", ensure_platform_tenant),
                    SeedStep("kb_categories", seed_builtin_kb_categories),
                    SeedStep("knowledge_bases", seed_builtin_kbs),
                ),
            )
            with pytest.raises(RuntimeError):
                await cat.seed_catalog_ingestion_per_document(sm, embedder=HashEmbedder())
        finally:
            await engine.dispose()

    asyncio.run(_run())

    assert len(seen) == 3, "el tercero debía intentarse"
    # Los dos primeros documentos sobreviven al fallo del tercero.
    assert asyncio.run(_count(migrations_pg_dsn, "documents")) == 2
    assert asyncio.run(_count(migrations_pg_dsn, "chunks")) > 0


# ---------------------------------------------------------------------------
# 3. El orden que exigen las FKs no se ha perdido en el refactor
# ---------------------------------------------------------------------------
def test_the_step_order_still_respects_the_foreign_keys() -> None:
    """No necesita BD: es la propiedad estructural que un refactor rompe."""
    from api_server.seeds.__main__ import SEED_STEPS

    keys = [step.key for step in SEED_STEPS]
    assert len(keys) == len(set(keys)), f"claves de paso duplicadas: {keys}"
    assert len(keys) >= 15, f"el runner perdió pasos por el camino ({len(keys)})"

    def _before(first: str, second: str) -> None:
        assert keys.index(first) < keys.index(second), f"{first} debe sembrarse antes que {second}"

    _before("platform_tenant", "agents")
    _before("agents", "teams")  # FK team_members.agent_id
    _before("skills", "agent_skills")  # FK agent_skills.skill_id
    _before("tools", "agent_tools")  # FK agent_tools.tool_id
    # El QA E2E Automator vive fuera de `BUILTIN_AGENTS`, así que su cableado de
    # tools es un paso propio con DOS precondiciones. Sin fijarlas aquí, moverlo
    # de sitio fallaría en el arranque real y no en la suite — y su ausencia ya
    # dejó al agente con cero tools durante meses sin que nada se quejara.
    _before("qa_e2e_automator", "qa_e2e_automator_tools")  # FK agent_tools.agent_id
    _before("tools", "qa_e2e_automator_tools")  # FK agent_tools.tool_id
    _before("ci4_agents", "ci4_team")  # FK team_members.agent_id
    _before("kb_categories", "knowledge_bases")  # FK knowledge_bases.category_id
    _before("knowledge_bases", "catalog_ingestion")  # el corpus necesita la KB
    _before("teams", "project_templates")  # FK projects.team_id
