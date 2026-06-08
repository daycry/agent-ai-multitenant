"""Skills end-to-end (Plan 06.18 task_06_18_13, ADR 0050 Opción A).

Cablea el MVP de Skills declarativas:

  * ``GET /agents/{id}/skills`` (tenant_user) lista las skills asignadas.
  * ``PUT /agents/{id}/skills`` (tenant_admin) reemplaza el conjunto, con las
    MISMAS reglas de scope que ``agent_tools`` / grants de KB:
      - built-in asignable;
      - custom solo del tenant (otro tenant → 422, RLS lo oculta);
      - ``global_builtin`` → 403 (hay que forkear);
      - tenant B sobre el agente de A → 404 (aislamiento multi-tenant).
  * El ``prompt_fragment`` de las skills asignadas se inyecta en el system
    prompt EFECTIVO del runtime; el test lo verifica sobre el payload
    dispatchado (``request["skill_prompt_fragments"]``) — el mismo nivel en el
    que ``test_agent_tool_specs_serialization`` verifica el threading.
  * ``SkillCategory`` inválida en create → 422 (el enum se alineó al seed real).

Backward-compat: un agente SIN skills asignadas no emite la clave
``skill_prompt_fragments`` → el prompt actual queda intacto.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
TEST_REDIS_URL = "redis://localhost:6379/15"


# ---------------------------------------------------------------------------
# Seed: dos tenants. Tenant A con un agente project_local y un built-in agent.
# Skills: un built-in (platform), un custom de A y un custom de B.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "admin_b": uuid4(),
        "project_a": uuid4(),
        "project_b": uuid4(),
        "agent_a": uuid4(),
        "agent_builtin": uuid4(),
        "agent_b": uuid4(),
        "skill_builtin": uuid4(),
        "skill_custom_a": uuid4(),
        "skill_custom_b": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agent_skills, agent_tools, skills, tools, agents, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug)"
            " VALUES ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["tenant_a"],
            "Acme",
            "acme-skl",
            ids["tenant_b"],
            "Globex",
            "globex-skl",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1, 'a@acme.test', 'h'), ($2, 'b@globex.test', 'h')",
            ids["admin_a"],
            ids["admin_b"],
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
            uuid4(),
            ids["tenant_b"],
            ids["admin_b"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name)"
            " VALUES ($1, $2, 'A-app'), ($3, $4, 'B-app')",
            ids["project_a"],
            ids["tenant_a"],
            ids["project_b"],
            ids["tenant_b"],
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt, project_id)"
            " VALUES"
            " ($1, $2, 'a-dev', 'backend_dev', 'project_local', 'ai', 'base prompt', $3),"
            # global_builtin pero del tenant A: get_writable_or_404 lo encuentra,
            # el chequeo de scope lo rechaza con 403 (forkear primero).
            " ($4, $2, 'builtin-dev', 'backend_dev', 'global_builtin', 'ai', 'p', NULL),"
            " ($5, $6, 'b-dev', 'backend_dev', 'project_local', 'ai', 'p', $7)",
            ids["agent_a"],
            ids["tenant_a"],
            ids["project_a"],
            ids["agent_builtin"],
            ids["agent_b"],
            ids["tenant_b"],
            ids["project_b"],
        )
        # Skills: built-in (platform) + custom A + custom B.
        await conn.execute(
            "INSERT INTO skills (id, tenant_id, name, category, prompt_fragment, is_builtin)"
            " VALUES"
            " ($1, $2, 'pytest-skill', 'qa', 'FRAGMENTO BUILTIN pytest', true),"
            " ($3, $4, 'a-custom', 'backend', 'FRAGMENTO CUSTOM A', false),"
            " ($5, $6, 'b-custom', 'backend', 'FRAGMENTO CUSTOM B', false)",
            ids["skill_builtin"],
            _PLATFORM_TENANT_ID,
            ids["skill_custom_a"],
            ids["tenant_a"],
            ids["skill_custom_b"],
            ids["tenant_b"],
        )
    finally:
        await conn.close()
    return ids


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")

    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


# ===========================================================================
# 1. PUT persiste agent_skills (built-in + custom del propio tenant) y GET las
#    devuelve.
# ===========================================================================
@pytest.mark.asyncio
async def test_put_assigns_builtin_and_own_custom(configured_app, migrations_pg_dsn: str) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint(ids["admin_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/agents/{ids['agent_a']}/skills",
            headers=headers,
            json={
                "skills": [
                    {"skill_id": str(ids["skill_builtin"])},
                    {"skill_id": str(ids["skill_custom_a"])},
                ]
            },
        )
        assert resp.status_code == 200, resp.text
        names = {s["name"] for s in resp.json()}
        assert names == {"pytest-skill", "a-custom"}

        get_resp = await client.get(f"/agents/{ids['agent_a']}/skills", headers=headers)
        assert get_resp.status_code == 200, get_resp.text
        assert {s["name"] for s in get_resp.json()} == {"pytest-skill", "a-custom"}


# ===========================================================================
# 2. Scope: custom de OTRO tenant → 422 (RLS lo oculta, set declarativo inválido).
# ===========================================================================
@pytest.mark.asyncio
async def test_put_rejects_foreign_custom_skill(configured_app, migrations_pg_dsn: str) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint(ids["admin_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/agents/{ids['agent_a']}/skills",
            headers=headers,
            json={"skills": [{"skill_id": str(ids["skill_custom_b"])}]},
        )
        assert resp.status_code == 422, resp.text


# ===========================================================================
# 3. Scope: global_builtin agent → 403 (forkear primero).
# ===========================================================================
@pytest.mark.asyncio
async def test_put_global_builtin_agent_forbidden(configured_app, migrations_pg_dsn: str) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint(ids["admin_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/agents/{ids['agent_builtin']}/skills",
            headers=headers,
            json={"skills": [{"skill_id": str(ids["skill_builtin"])}]},
        )
        assert resp.status_code == 403, resp.text


# ===========================================================================
# 4. Aislamiento: tenant B no ve el agente de A → 404.
# ===========================================================================
@pytest.mark.asyncio
async def test_cross_tenant_404(configured_app, migrations_pg_dsn: str) -> None:
    ids = await _seed(migrations_pg_dsn)
    token_b = await _mint(ids["admin_b"], ids["tenant_b"])
    headers = {"Authorization": f"Bearer {token_b}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        get_resp = await client.get(f"/agents/{ids['agent_a']}/skills", headers=headers)
        assert get_resp.status_code == 404, get_resp.text
        put_resp = await client.put(
            f"/agents/{ids['agent_a']}/skills",
            headers=headers,
            json={"skills": []},
        )
        assert put_resp.status_code == 404, put_resp.text


# ===========================================================================
# 5. SkillCategory inválida en create → 422 (enum alineado al seed real).
# ===========================================================================
@pytest.mark.asyncio
async def test_create_skill_invalid_category_rejected(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint(ids["admin_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        bad = await client.post(
            "/skills",
            headers=headers,
            json={
                "name": "bad-skill",
                "category": "wizardry",  # fuera del enum
                "prompt_fragment": "haz magia",
            },
        )
        assert bad.status_code == 422, bad.text

        good = await client.post(
            "/skills",
            headers=headers,
            json={
                "name": "good-skill",
                "category": "backend",  # categoría real del seed
                "prompt_fragment": "haz backend",
            },
        )
        assert good.status_code == 201, good.text


# ===========================================================================
# 6. Inyección del prompt_fragment: el set asignado se threadea en el payload
#    dispatchado (skill_prompt_fragments); sin skills → clave ausente.
# ===========================================================================
def _ready_event(tenant: UUID, project: UUID, task: UUID):
    from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent

    return TaskEvent(
        stream_id="1-0",
        type=EVENT_TASK_STATUS_CHANGED,
        tenant_id=str(tenant),
        project_id=str(project),
        task_id=str(task),
        occurred_at="2026-06-01T00:00:00+00:00",
        payload={"old_status": "backlog", "new_status": "ready"},
    )


async def _drain_request(redis, queue: str) -> dict[str, Any]:
    raw = await redis.lrange(queue, 0, -1)
    await redis.delete(queue)
    assert len(raw) == 1
    message = json.loads(raw[0])
    body = json.loads(base64.b64decode(message["body"]))
    _args, kwargs, _embed = body
    return kwargs["request"]


def _scripted() -> dict[str, Any]:
    return {
        "kind": "scripted",
        "decisions": [{"kind": "finish", "output": "done"}],
        "reviews": [{"passed": True}],
    }


async def _seed_dispatchable(dsn: str, *, with_skills: bool) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "agent": uuid4(),
        "task": uuid4(),
        "skill": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agent_skills, agent_tools, skills, tools, executions,"
            " task_dependencies, tasks, agents, projects, organizations"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'D', 'disp-skl')",
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
            " VALUES ($1, $2, 'Disp', 'backend_dev', 'project_local', 'ai', 'x', $3, $4)",
            ids["agent"],
            ids["tenant"],
            ids["project"],
            json.dumps(_scripted()),
        )
        if with_skills:
            await conn.execute(
                "INSERT INTO skills (id, tenant_id, name, category, prompt_fragment, is_builtin)"
                " VALUES ($1, $2, 'disp-skill', 'backend', 'INYECTAME EN EL PROMPT', false)",
                ids["skill"],
                ids["tenant"],
            )
            await conn.execute(
                "INSERT INTO agent_skills (agent_id, skill_id) VALUES ($1, $2)",
                ids["agent"],
                ids["skill"],
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


def _dispatcher(sm):
    from orchestrator.config import Settings as OrchestratorSettings
    from orchestrator.dispatch import TaskDispatcher
    from workers.celery_app import build_celery_app
    from workers.config import Settings as WorkerSettings

    celery_app = build_celery_app(WorkerSettings(broker_url=TEST_REDIS_URL))
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=celery_app,
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL),
    )


@pytest.mark.asyncio
async def test_dispatch_threads_skill_prompt_fragments(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    ids = await _seed_dispatchable(migrations_pg_dsn, with_skills=True)
    engine = create_async_engine(admin_database_url)
    redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        await redis.delete("default")
        await _dispatcher(sm).handle(_ready_event(ids["tenant"], ids["project"], ids["task"]))
        request = await _drain_request(redis, "default")
        assert request["agent_id"] == str(ids["agent"])
        assert "skill_prompt_fragments" in request
        assert "INYECTAME EN EL PROMPT" in " ".join(request["skill_prompt_fragments"])
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_omits_skill_fragments_without_skills(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    ids = await _seed_dispatchable(migrations_pg_dsn, with_skills=False)
    engine = create_async_engine(admin_database_url)
    redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        await redis.delete("default")
        await _dispatcher(sm).handle(_ready_event(ids["tenant"], ids["project"], ids["task"]))
        request = await _drain_request(redis, "default")
        assert request["agent_id"] == str(ids["agent"])
        assert "skill_prompt_fragments" not in request
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


# ===========================================================================
# 7. La inyección llega al prompt EFECTIVO del runtime (unidad sobre el
#    constructor de mensajes): el fragmento aparece en el system prompt.
# ===========================================================================
def test_skill_fragment_injected_into_effective_system_prompt() -> None:
    """El fragmento de skill se prepende al system prompt que ve el modelo."""
    import sys

    sys.path.insert(0, "docker/agent-runtimes/agent-runtime")
    from agent_runtime.providers import _decide_messages  # type: ignore

    state = {
        "task": {"title": "t", "description": "d"},
        "context": [],
        "system_preamble": "FRAGMENTO DE SKILL",
    }
    messages = _decide_messages(state)
    system = next(m for m in messages if m.role == "system")
    assert "FRAGMENTO DE SKILL" in system.content

    # Sin preamble: el system prompt queda intacto (backward-compat).
    plain = _decide_messages({"task": {"title": "t"}, "context": []})
    plain_system = next(m for m in plain if m.role == "system")
    assert "FRAGMENTO DE SKILL" not in plain_system.content
