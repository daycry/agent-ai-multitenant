"""Motivo de no-memorización consultable + fin del skip silencioso para IA
(Plan 06.17 task_06_17_04).

Antes de esta tarea el Memorizer (``workers/memorizer.py:233-237`` y
``policy.py:68-72``) decidía no memorizar y solo lo dejaba en los LOGS: un
agente IA con ``memory_scope=private`` (o ``team_shared`` sin equipo) caía en un
``skip`` silencioso, imposible de consultar desde la UI. Aquí se verifica contra
Postgres real que:

  * el worker persiste un CÓDIGO canónico de motivo en
    ``executions.memorize_skip_reason`` (``not_done`` / ``skip_private`` /
    ``no_team`` / ``llm_empty``), no solo un log;
  * una memorización correcta deja ``memorize_skip_reason`` a NULL;
  * el endpoint ``GET /memories/skip-reasons`` lista las ejecuciones con motivo
    de skip, tenant-scoped (RLS), para que la UI las muestre;
  * los estados elegibles de memorización son operator-configurable
    (``memory.memorizable_statuses``): un estado fuera de la lista → ``not_done``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Worker harness (mirror de test_memorizer): inyecta un LLM y embedder fakes
# ---------------------------------------------------------------------------
class _FakeLLM:
    name = "fake"

    def __init__(self, candidates_json: str) -> None:
        self._json = candidates_json

    async def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        from shared_llm.types import CompletionResponse, Usage

        return CompletionResponse(
            content=self._json,
            model="fake-model",
            provider=self.name,
            usage=Usage(),
            tool_calls=None,
            raw={},
        )

    async def stream(self, messages, **kwargs):  # type: ignore[no-untyped-def]  # pragma: no cover
        from shared_llm.types import StreamChunk

        yield StreamChunk(delta="", usage=None, raw={})

    async def aclose(self) -> None:  # pragma: no cover
        pass


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str):
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import get_settings, reset_settings_cache

    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


async def _seed(
    dsn: str,
    *,
    memory_scope: str,
    execution_status: str = "done",
    with_team: bool = True,
) -> dict[str, UUID]:
    tenant_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    agent_id = uuid4()
    task_id = uuid4()
    execution_id = uuid4()
    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, executions, tasks, plans, conversations,"
            " projects, agents, teams, user_org_memberships, organizations,"
            " platform_settings, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Skip",
            "tenant-skip",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-skip",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "admin@skip.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_id,
            user_id,
        )
        team_value: UUID | None = None
        if with_team:
            await conn.execute(
                "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, $3)",
                team_id,
                tenant_id,
                "Team Skip",
            )
            team_value = team_id
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, team_id) VALUES ($1, $2, $3, $4)",
            project_id,
            tenant_id,
            "Skip Project",
            team_value,
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, project_id, name, role, system_prompt, memory_scope, scope)"
            " VALUES ($1, $2, $3, $4, 'backend_dev', $5, $6, 'project_local')",
            agent_id,
            tenant_id,
            project_id,
            "Skip Agent",
            "You are a backend dev.",
            memory_scope,
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title) VALUES ($1, $2, $3, $4)",
            task_id,
            tenant_id,
            project_id,
            "Skip task",
        )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, agent_id, status, output, steps_log)"
            " VALUES ($1, $2, $3, $4, $5, $6, '[]'::jsonb)",
            execution_id,
            tenant_id,
            task_id,
            agent_id,
            execution_status,
            "Did the work.",
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "team_id": team_id,
        "project_id": project_id,
        "agent_id": agent_id,
        "task_id": task_id,
        "execution_id": execution_id,
        "user_id": user_id,
    }


async def _run_worker(execution_id: UUID, settings, candidates_json: str) -> dict[str, Any]:
    from workers.memorizer import _memorize_execution_async

    return await _memorize_execution_async(
        execution_id,
        settings=settings,
        llm_factory=lambda _s: _FakeLLM(candidates_json),
        embedder_factory=None,
    )


async def _skip_reason(dsn: str, execution_id: UUID) -> str | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT memorize_skip_reason FROM executions WHERE id = $1", execution_id
        )
    finally:
        await conn.close()


_TWO_CANDIDATES = (
    '[{"content": "El proyecto usa asyncpg.", "type": "semantic", "tags": []},'
    ' {"content": "Se arregló un import.", "type": "episodic", "tags": []}]'
)


# ---------------------------------------------------------------------------
# 1. private (agente IA) → motivo 'skip_private' PERSISTIDO (no silencioso)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_private_ai_records_skip_private(
    schema_at_head, workers_settings, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn, memory_scope="private")
    result = await _run_worker(seeded["execution_id"], workers_settings, _TWO_CANDIDATES)
    assert "skip" in result["reason"], result
    assert await _skip_reason(migrations_pg_dsn, seeded["execution_id"]) == "skip_private"


# ---------------------------------------------------------------------------
# 2. team_shared sin equipo → motivo 'no_team'
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_team_shared_without_team_records_no_team(
    schema_at_head, workers_settings, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn, memory_scope="team_shared", with_team=False)
    await _run_worker(seeded["execution_id"], workers_settings, _TWO_CANDIDATES)
    assert await _skip_reason(migrations_pg_dsn, seeded["execution_id"]) == "no_team"


# ---------------------------------------------------------------------------
# 3. estado no elegible → motivo 'not_done'
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_not_done_records_not_done(
    schema_at_head, workers_settings, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn, memory_scope="global", execution_status="aborted")
    await _run_worker(seeded["execution_id"], workers_settings, _TWO_CANDIDATES)
    assert await _skip_reason(migrations_pg_dsn, seeded["execution_id"]) == "not_done"


# ---------------------------------------------------------------------------
# 4. memorización OK → motivo NULL
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ok_leaves_reason_null(
    schema_at_head, workers_settings, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn, memory_scope="global")
    result = await _run_worker(seeded["execution_id"], workers_settings, _TWO_CANDIDATES)
    assert result["reason"] == "ok", result
    assert await _skip_reason(migrations_pg_dsn, seeded["execution_id"]) is None


# ---------------------------------------------------------------------------
# 5. LLM sin candidatos → motivo 'llm_empty'
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_candidates_records_llm_empty(
    schema_at_head, workers_settings, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn, memory_scope="global")
    await _run_worker(seeded["execution_id"], workers_settings, "[]")
    assert await _skip_reason(migrations_pg_dsn, seeded["execution_id"]) == "llm_empty"


# ---------------------------------------------------------------------------
# 6. estados elegibles operator-configurable: 'aborted' añadido → memoriza
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_eligible_statuses_operator_configurable(
    schema_at_head, workers_settings, migrations_pg_dsn: str
) -> None:
    import json

    seeded = await _seed(migrations_pg_dsn, memory_scope="global", execution_status="aborted")
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_settings (key, value, updated_by)"
            " VALUES ('memory.memorizable_statuses', $1::jsonb, $2)",
            json.dumps(["done", "aborted"]),
            seeded["user_id"],
        )
    finally:
        await conn.close()

    result = await _run_worker(seeded["execution_id"], workers_settings, _TWO_CANDIDATES)
    assert result["reason"] == "ok", result
    assert await _skip_reason(migrations_pg_dsn, seeded["execution_id"]) is None


# ---------------------------------------------------------------------------
# 7. GET /memories/skip-reasons lista los motivos, tenant-scoped (RLS)
# ---------------------------------------------------------------------------
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
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint_user_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_skip_reasons_endpoint_tenant_scoped(
    configured_app, workers_settings, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn, memory_scope="private")
    await _run_worker(seeded["execution_id"], workers_settings, _TWO_CANDIDATES)

    token = await _mint_user_token(seeded["user_id"], seeded["tenant_id"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/memories/skip-reasons", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert any(
        it["execution_id"] == str(seeded["execution_id"]) and it["reason"] == "skip_private"
        for it in items
    ), items

    # Tenant B no ve el motivo del tenant A (RLS).
    other_tenant = uuid4()
    other_user = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            other_tenant,
            "Tenant B Skip",
            "tenant-b-skip",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            other_user,
            "b@skip.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            other_tenant,
            other_user,
        )
    finally:
        await conn.close()
    token_b = await _mint_user_token(other_user, other_tenant)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp_b = await client.get(
            "/memories/skip-reasons", headers={"Authorization": f"Bearer {token_b}"}
        )
    assert resp_b.status_code == 200, resp_b.text
    assert resp_b.json() == []
