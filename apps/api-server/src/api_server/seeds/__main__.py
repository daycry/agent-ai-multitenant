"""CLI entry point: `python -m api_server.seeds`.

Runs every built-in seed in order under the BYPASSRLS admin engine.
Idempotent -- safe to re-run after a migration.
"""

from __future__ import annotations

import asyncio

import structlog

from api_server.db.session import get_admin_sessionmaker
from api_server.logging import configure_logging
from api_server.seeds.builtin_agents import seed_builtin_agent_skills, seed_builtin_agents
from api_server.seeds.builtin_approval_policies import seed_builtin_approval_policies
from api_server.seeds.builtin_kb_categories import seed_builtin_kb_categories
from api_server.seeds.builtin_kbs import seed_builtin_kbs
from api_server.seeds.builtin_project_templates import seed_builtin_project_templates
from api_server.seeds.builtin_skills import seed_builtin_skills
from api_server.seeds.builtin_teams import seed_builtin_teams
from api_server.seeds.builtin_tools import seed_builtin_tools
from api_server.seeds.catalog_ingestion import seed_catalog_ingestion
from api_server.seeds.platform import ensure_platform_tenant
from api_server.seeds.qa_e2e_automator import seed_qa_e2e_automator


async def main() -> None:
    configure_logging(service="seed-runner")
    log = structlog.get_logger("seed-runner")

    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        await ensure_platform_tenant(session)
        n_agents = await seed_builtin_agents(session)
        # Plan 09 task_09_14: the QA E2E Automator template references the
        # Playwright marketplace listing (task_09_13). It is one more
        # global_builtin platform agent under the same model as the eleven
        # core built-ins, seeded via its own loader to keep that count stable.
        n_qa_e2e = await seed_qa_e2e_automator(session)
        n_skills = await seed_builtin_skills(session)
        # Agent<->skill links need both agents AND skills to exist first
        # (FKs on agent_skills). Wire them once both seeds have run.
        n_agent_skills = await seed_builtin_agent_skills(session)
        n_tools = await seed_builtin_tools(session)
        # Teams depend on agents being present (FK on team_members.agent_id).
        n_teams = await seed_builtin_teams(session)
        # Plan 06.10: las categorías deben existir antes que las KBs
        # built-in (seed_builtin_kbs resuelve category_slug -> FK).
        n_kb_categories = await seed_builtin_kb_categories(session)
        # Plan 06.9: canonical KBs must exist before project templates
        # reference them via default_kb_grants.
        n_kbs = await seed_builtin_kbs(session)
        # Plan 06.13: ingest the curated corpus into the (now-existing)
        # built-in KBs so granting one actually feeds the RAG. Idempotent;
        # uses the real Ollama embedder in production. MUST run after
        # seed_builtin_kbs (the KB rows must exist first).
        catalog = await seed_catalog_ingestion(session)
        # Project templates depend on teams (FK on projects.team_id).
        n_proj_templates = await seed_builtin_project_templates(session)
        n_policies = await seed_builtin_approval_policies(session)

    log.info(
        "seed.completed",
        agents=n_agents,
        qa_e2e_automator=n_qa_e2e,
        skills=n_skills,
        agent_skills=n_agent_skills,
        tools=n_tools,
        teams=n_teams,
        kb_categories=n_kb_categories,
        knowledge_bases=n_kbs,
        catalog_documents=len(catalog),
        catalog_chunks=sum(r.chunks_persisted for r in catalog),
        project_templates=n_proj_templates,
        approval_policies=n_policies,
    )


if __name__ == "__main__":
    asyncio.run(main())
