"""P0-1 (investigación 2026-07-11): el dispatch threadea la persona del agente.

La persona (`agents.system_prompt` / `model_config.system_prompts`) existía en BD
pero nunca entraba al payload del run — todos los agentes ejecutaban con el system
genérico. Ahora `_assemble_run_request` emite `agent_persona` {prompt, role, name}
con la precedencia del frontend (es → en → plano). Mismo andamiaje que el test de
`skill_prompt_fragments` (tests/integration/test_agent_skills.py).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from tests.integration.test_agent_skills import (
    TEST_REDIS_URL,
    _dispatcher,
    _drain_request,
    _ready_event,
    _scripted,
)

pytestmark = [pytest.mark.integration]


async def _seed_agent_with_persona(dsn: str, *, bilingual: bool) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "agent": uuid4(), "task": uuid4()}
    model_config: dict[str, Any] = dict(_scripted())
    if bilingual:
        model_config["system_prompts"] = {
            "es": "PERSONA-ES: eres el backend CI4, experto en HMVC.",
            "en": "PERSONA-EN: you are the CI4 backend.",
        }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agent_skills, agent_tools, skills, tools, executions,"
            " task_dependencies, tasks, agents, projects, organizations"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'D', 'disp-persona')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template, worker_config)"
            " VALUES ($1, $2, 'P', 'active', false, '{\"assignment_policy\": \"load_balanced\"}')",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt, project_id,"
            "  model_config)"
            " VALUES ($1, $2, 'ci4-backend', 'backend_dev', 'project_local', 'ai',"
            "         'PERSONA-PLANA legacy', $3, $4)",
            ids["agent"],
            ids["tenant"],
            ids["project"],
            json.dumps(model_config),
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, description, status, priority)"
            " VALUES ($1, $2, $3, 'T', 'd', 'ready', 'medium')",
            ids["task"],
            ids["tenant"],
            ids["project"],
        )
    finally:
        await conn.close()
    return ids


async def _dispatch_and_drain(ids: dict[str, UUID], admin_database_url: str) -> dict[str, Any]:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(admin_database_url)
    redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        await redis.delete("default")
        await _dispatcher(sm).handle(_ready_event(ids["tenant"], ids["project"], ids["task"]))
        return await _drain_request(redis, "default")
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_threads_bilingual_persona_preferring_spanish(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    ids = await _seed_agent_with_persona(migrations_pg_dsn, bilingual=True)
    request = await _dispatch_and_drain(ids, admin_database_url)
    persona = request["agent_persona"]
    assert persona["prompt"].startswith("PERSONA-ES")
    assert persona["role"] == "backend_dev"
    assert persona["name"] == "ci4-backend"


@pytest.mark.asyncio
async def test_dispatch_falls_back_to_flat_system_prompt(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    ids = await _seed_agent_with_persona(migrations_pg_dsn, bilingual=False)
    request = await _dispatch_and_drain(ids, admin_database_url)
    assert request["agent_persona"]["prompt"] == "PERSONA-PLANA legacy"
