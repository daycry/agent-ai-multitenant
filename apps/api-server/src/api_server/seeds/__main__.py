"""CLI entry point: `python -m api_server.seeds`.

Runs every built-in seed in order under the BYPASSRLS admin engine.
Idempotent -- safe to re-run after a migration.

**Una transacción por seed** (prod-13 task_prod13_09, hallazgo db-8). Antes los
~20 seeds iban dentro de un único ``session.begin()``, lo que traía dos
problemas:

  1. Una transacción abierta durante llamadas de red: ``seed_catalog_ingestion``
     embebe el corpus contra Ollama, así que la conexión (y sus locks) se
     retenía durante minutos de I/O externo.
  2. Todo o nada: un fallo en el seed nº 18 tiraba los 17 anteriores. En un
     arranque con Ollama caído, la instalación se quedaba sin agentes, sin
     equipos y sin tools por culpa de la ingesta del catálogo — la parte
     prescindible.

Ahora cada paso commitea lo suyo. El cambio es seguro porque **todos los seeds
son idempotentes** (uuid5 estable, upsert por identidad, hash de corpus): un
re-run tras un fallo repasa lo hecho sin duplicar. Lo que NO cambia es el ORDEN,
que lo imponen las FKs y está fijado por un test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Import the ORM models aggregator FIRST so every mapper is registered before
# any session/flush triggers mapper configuration. Without it a standalone
# `python -m api_server.seeds` trips on an unresolved cross-module FK (e.g.
# documents -> users) because only a subset of model modules got imported.
# Mirrors migrations/env.py, which imports this for the same reason.
from api_server.db import models as _models  # noqa: F401
from api_server.db.session import get_admin_sessionmaker
from api_server.logging import configure_logging
from api_server.marketplace.seed import seed_marketplace_listings
from api_server.seeds.builtin_agents import (
    seed_builtin_agent_skills,
    seed_builtin_agent_tools,
    seed_builtin_agents,
)
from api_server.seeds.builtin_approval_policies import seed_builtin_approval_policies
from api_server.seeds.builtin_kb_categories import seed_builtin_kb_categories
from api_server.seeds.builtin_kbs import seed_builtin_kbs
from api_server.seeds.builtin_project_templates import seed_builtin_project_templates
from api_server.seeds.builtin_skills import seed_builtin_skills
from api_server.seeds.builtin_teams import seed_builtin_teams
from api_server.seeds.builtin_tools import seed_builtin_tools
from api_server.seeds.catalog_ingestion import seed_catalog_ingestion_per_document
from api_server.seeds.ci4_team import (
    seed_ci4_agent_skills,
    seed_ci4_agent_tools,
    seed_ci4_agents,
    seed_ci4_project_template,
    seed_ci4_team,
)
from api_server.seeds.human_agent_templates import seed_human_agent_templates
from api_server.seeds.platform import ensure_platform_tenant
from api_server.seeds.qa_e2e_automator import seed_qa_e2e_automator

_Sessionmaker = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class SeedStep:
    """Un seed y la clave con la que aparece en el log final.

    ``run`` recibe una sesión ya dentro de su propia transacción; el runner la
    commitea al salir. Un seed que necesite gobernar sus propias transacciones
    (el catálogo, que commitea por documento) NO es un ``SeedStep``: lo llama
    :func:`run_seeds` aparte, con el sessionmaker.
    """

    key: str
    run: Callable[[AsyncSession], Awaitable[Any]]


async def _owns_its_transactions(_session: AsyncSession) -> Any:
    """Marcador de un paso que el runner despacha aparte, con el sessionmaker.

    Levanta a propósito: si alguien lo llamase con una sesión, sería porque el
    despacho especial de :func:`run_seeds` dejó de reconocer la clave, y eso
    debe romper ruidosamente en vez de sembrar el catálogo en la transacción
    equivocada.
    """
    raise AssertionError(
        "este paso gobierna sus propias transacciones; run_seeds debe despacharlo aparte"
    )


#: El orden NO es cosmético: lo imponen las FKs. Los comentarios explican cuál
#: en cada caso, y `test_seeds_transaction_per_seed` fija las dependencias que
#: un refactor puede romper sin que nada más lo note hasta el arranque.
SEED_STEPS: tuple[SeedStep, ...] = (
    SeedStep("platform_tenant", ensure_platform_tenant),
    SeedStep("agents", seed_builtin_agents),
    # Plan 09 task_09_14: the QA E2E Automator template references the
    # Playwright marketplace listing (task_09_13). It is one more
    # global_builtin platform agent under the same model as the eleven
    # core built-ins, seeded via its own loader to keep that count stable.
    SeedStep("qa_e2e_automator", seed_qa_e2e_automator),
    # Plan 16 task_16_07: five global Human-Agent templates (Security
    # Reviewer, Brand Lead, DBA, Legal Reviewer, UX Lead). Another set of
    # global_builtin platform agents — agent_type='human' — that tenants
    # clone-and-fork from the Human Agents gallery.
    SeedStep("human_agent_templates", seed_human_agent_templates),
    # Plan codeigniter-4-builtin-team: ten more global_builtin platform
    # agents (the ci4-* roster) seeded via their own loader to keep the
    # eleven-core count test_seed_agents pins stable. They carry NO
    # provider/model in model_config (they inherit the platform default,
    # ADR 0055 / f87ca62). MUST run before seed_builtin_teams (the
    # codeigniter-4 team's members FK to these agent ids by slug).
    SeedStep("ci4_agents", seed_ci4_agents),
    SeedStep("skills", seed_builtin_skills),
    # Agent<->skill links need both agents AND skills to exist first
    # (FKs on agent_skills). Wire them once both seeds have run.
    SeedStep("agent_skills", seed_builtin_agent_skills),
    SeedStep("tools", seed_builtin_tools),
    # Ola B: wire each standalone built-in agent to its tools (por rol). MUST
    # run after seed_builtin_agents (FK agent_tools.agent_id) AND
    # seed_builtin_tools (FK agent_tools.tool_id). Antes los built-in sueltos
    # tenían skills pero NO tools → equipos built-in "a medias".
    SeedStep("agent_tools", seed_builtin_agent_tools),
    # Plan codeigniter-4-builtin-team: wire each ci4-* agent to its
    # built-in tools via the agent_tools junction (the table does NOT
    # restrict scope, so global_builtin agents can carry tools). MUST run
    # after seed_builtin_tools (FK agent_tools.tool_id) AND after
    # seed_ci4_agents (FK agent_tools.agent_id).
    SeedStep("ci4_agent_tools", seed_ci4_agent_tools),
    # Ola B: wire each ci4-* agent to its skills (por rol + extras PHP) via
    # the agent_skills junction. MUST run after seed_ci4_agents (FK
    # agent_skills.agent_id) AND seed_builtin_skills (FK agent_skills.skill_id).
    SeedStep("ci4_agent_skills", seed_ci4_agent_skills),
    # Teams depend on agents being present (FK on team_members.agent_id).
    SeedStep("teams", seed_builtin_teams),
    # Plan codeigniter-4-builtin-team: the CodeIgniter 4 built-in team
    # (1 team + 10 members) seeded via its own loader so the five-team
    # count test_seed_teams pins stays stable. MUST run after
    # seed_ci4_agents (FK team_members.agent_id resolves the ci4-* slugs).
    SeedStep("ci4_team", seed_ci4_team),
    # Plan 06.10: las categorías deben existir antes que las KBs
    # built-in (seed_builtin_kbs resuelve category_slug -> FK).
    SeedStep("kb_categories", seed_builtin_kb_categories),
    # Plan 06.9: canonical KBs must exist before project templates
    # reference them via default_kb_grants.
    SeedStep("knowledge_bases", seed_builtin_kbs),
    # Plan 06.13: ingest the curated corpus into the (now-existing) built-in
    # KBs so granting one actually feeds the RAG. Único paso que gobierna sus
    # propias transacciones (una por documento): es el que habla con Ollama.
    SeedStep("catalog_ingestion", _owns_its_transactions),
    # Project templates depend on teams (FK on projects.team_id).
    SeedStep("project_templates", seed_builtin_project_templates),
    # Plan codeigniter-4-builtin-team: the codeigniter-4-app project
    # template seeded via its own loader (keeps the eight-template count
    # test_seed_project_templates pins stable). MUST run after
    # seed_ci4_team (FK projects.team_id) and after seed_builtin_kbs (its
    # default_kb_grants reference the CI4 built-in KB slugs).
    SeedStep("ci4_project_template", seed_ci4_project_template),
    SeedStep("approval_policies", seed_builtin_approval_policies),
    # Plan 09.1 task_09_1_01: fill the official marketplace catalog so it is
    # not empty on a fresh install. Publishes the VERIFIED + GLOBAL listings
    # under the ``official-catalog`` source — the flagship Playwright tool
    # (task_09_13) + a curated set of SKILL listings built from the platform's
    # own convention docs. Idempotent (upsert by listing identity); writes the
    # SKILL.md artifacts under the official catalog root the install
    # LocalArtifactFetcher reads.
    SeedStep("marketplace_listings", seed_marketplace_listings),
)

#: Clave del paso que gobierna sus propias transacciones. Se resuelve por nombre
#: (y no por identidad de función) para que el runner y la tabla de arriba no se
#: puedan desincronizar en silencio.
CATALOG_STEP_KEY = "catalog_ingestion"


async def run_seeds(
    sessionmaker: _Sessionmaker,
    *,
    steps: tuple[SeedStep, ...] | None = None,
) -> dict[str, Any]:
    """Ejecuta los seeds en orden, **cada uno en su propia transacción**.

    Devuelve ``{clave: lo que devolvió el seed}``. NO captura excepciones: un
    seed que revienta aborta el arranque (que es lo correcto — un despliegue a
    medias debe verse), pero lo ya commiteado se queda, y el re-run lo respeta
    por idempotencia.
    """
    results: dict[str, Any] = {}
    for step in steps if steps is not None else SEED_STEPS:
        if step.key == CATALOG_STEP_KEY:
            results[step.key] = await seed_catalog_ingestion_per_document(sessionmaker)
            continue
        async with sessionmaker() as session, session.begin():
            results[step.key] = await step.run(session)
    return results


async def main() -> None:
    configure_logging(service="seed-runner")
    log = structlog.get_logger("seed-runner")

    results = await run_seeds(get_admin_sessionmaker())
    catalog = results.get(CATALOG_STEP_KEY) or []
    listings = results["marketplace_listings"]

    log.info(
        "seed.completed",
        agents=results["agents"],
        qa_e2e_automator=results["qa_e2e_automator"],
        human_agent_templates=results["human_agent_templates"],
        ci4_agents=results["ci4_agents"],
        ci4_agent_tools=results["ci4_agent_tools"],
        ci4_agent_skills=results["ci4_agent_skills"],
        ci4_team=results["ci4_team"],
        ci4_project_template=results["ci4_project_template"],
        skills=results["skills"],
        agent_skills=results["agent_skills"],
        tools=results["tools"],
        agent_tools=results["agent_tools"],
        teams=results["teams"],
        kb_categories=results["kb_categories"],
        knowledge_bases=results["knowledge_bases"],
        catalog_documents=len(catalog),
        catalog_chunks=sum(r.chunks_persisted for r in catalog),
        project_templates=results["project_templates"],
        approval_policies=results["approval_policies"],
        marketplace_listings=listings.total,
        marketplace_listings_created=listings.created,
    )


if __name__ == "__main__":
    asyncio.run(main())
