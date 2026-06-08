"""CLI entry point: `python -m api_server.seeds`.

Runs every built-in seed in order under the BYPASSRLS admin engine.
Idempotent -- safe to re-run after a migration.
"""

from __future__ import annotations

import asyncio

import structlog

# Import the ORM models aggregator FIRST so every mapper is registered before
# any session/flush triggers mapper configuration. Without it a standalone
# `python -m api_server.seeds` trips on an unresolved cross-module FK (e.g.
# documents -> users) because only a subset of model modules got imported.
# Mirrors migrations/env.py, which imports this for the same reason.
from api_server.db import models as _models  # noqa: F401
from api_server.db.session import get_admin_sessionmaker
from api_server.logging import configure_logging
from api_server.marketplace.seed import seed_marketplace_listings
from api_server.seeds.builtin_agents import seed_builtin_agent_skills, seed_builtin_agents
from api_server.seeds.builtin_approval_policies import seed_builtin_approval_policies
from api_server.seeds.builtin_kb_categories import seed_builtin_kb_categories
from api_server.seeds.builtin_kbs import seed_builtin_kbs
from api_server.seeds.builtin_project_templates import seed_builtin_project_templates
from api_server.seeds.builtin_skills import seed_builtin_skills
from api_server.seeds.builtin_teams import seed_builtin_teams
from api_server.seeds.builtin_tools import seed_builtin_tools
from api_server.seeds.catalog_ingestion import seed_catalog_ingestion
from api_server.seeds.ci4_team import (
    seed_ci4_agent_tools,
    seed_ci4_agents,
    seed_ci4_project_template,
    seed_ci4_team,
)
from api_server.seeds.human_agent_templates import seed_human_agent_templates
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
        # Plan 16 task_16_07: five global Human-Agent templates (Security
        # Reviewer, Brand Lead, DBA, Legal Reviewer, UX Lead). Another set of
        # global_builtin platform agents — agent_type='human' — that tenants
        # clone-and-fork from the Human Agents gallery.
        n_human_templates = await seed_human_agent_templates(session)
        # Plan codeigniter-4-builtin-team: ten more global_builtin platform
        # agents (the ci4-* roster) seeded via their own loader to keep the
        # eleven-core count test_seed_agents pins stable. They carry NO
        # provider/model in model_config (they inherit the platform default,
        # ADR 0055 / f87ca62). MUST run before seed_builtin_teams (the
        # codeigniter-4 team's members FK to these agent ids by slug).
        n_ci4_agents = await seed_ci4_agents(session)
        n_skills = await seed_builtin_skills(session)
        # Agent<->skill links need both agents AND skills to exist first
        # (FKs on agent_skills). Wire them once both seeds have run.
        n_agent_skills = await seed_builtin_agent_skills(session)
        n_tools = await seed_builtin_tools(session)
        # Plan codeigniter-4-builtin-team: wire each ci4-* agent to its
        # built-in tools via the agent_tools junction (the table does NOT
        # restrict scope, so global_builtin agents can carry tools). MUST run
        # after seed_builtin_tools (FK agent_tools.tool_id) AND after
        # seed_ci4_agents (FK agent_tools.agent_id).
        n_ci4_agent_tools = await seed_ci4_agent_tools(session)
        # Teams depend on agents being present (FK on team_members.agent_id).
        n_teams = await seed_builtin_teams(session)
        # Plan codeigniter-4-builtin-team: the CodeIgniter 4 built-in team
        # (1 team + 10 members) seeded via its own loader so the five-team
        # count test_seed_teams pins stays stable. MUST run after
        # seed_ci4_agents (FK team_members.agent_id resolves the ci4-* slugs).
        n_ci4_team = await seed_ci4_team(session)
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
        # Plan codeigniter-4-builtin-team: the codeigniter-4-app project
        # template seeded via its own loader (keeps the eight-template count
        # test_seed_project_templates pins stable). MUST run after
        # seed_ci4_team (FK projects.team_id) and after seed_builtin_kbs (its
        # default_kb_grants reference the CI4 built-in KB slugs).
        n_ci4_template = await seed_ci4_project_template(session)
        n_policies = await seed_builtin_approval_policies(session)
        # Plan 09.1 task_09_1_01: fill the official marketplace catalog so it
        # is not empty on a fresh install. Publishes the VERIFIED + GLOBAL
        # listings under the ``official-catalog`` source — the flagship
        # Playwright tool (task_09_13) + a curated set of SKILL listings built
        # from the platform's own convention docs. Idempotent (upsert by
        # listing identity); writes the SKILL.md artifacts under the official
        # catalog root the install LocalArtifactFetcher reads.
        catalog_listings = await seed_marketplace_listings(session)

    log.info(
        "seed.completed",
        agents=n_agents,
        qa_e2e_automator=n_qa_e2e,
        human_agent_templates=n_human_templates,
        ci4_agents=n_ci4_agents,
        ci4_agent_tools=n_ci4_agent_tools,
        ci4_team=n_ci4_team,
        ci4_project_template=n_ci4_template,
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
        marketplace_listings=catalog_listings.total,
        marketplace_listings_created=catalog_listings.created,
    )


if __name__ == "__main__":
    asyncio.run(main())
