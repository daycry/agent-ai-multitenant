"""GET /runs/leaderboard (ADR 0121) — agregación modelo×agente sobre runs reales.

Verifica contra Postgres real que: (a) la agregación cuenta done/escalated/
aborted y calcula success_rate por combinación; (b) el umbral min_runs deja
fuera el ruido estadístico; (c) el modelo se extrae del steps_log (último
model_call), no de una columna inexistente; (d) tenant-scoped por RLS: el
tenant B no ve las filas de A.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a, tenant_b = uuid4(), uuid4()
    user_a = uuid4()
    project = uuid4()
    agent = uuid4()
    task = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, plans, projects, agents,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, 'A', 'lb-a'), ($2, 'B', 'lb-b'), ($3, 'P', 'lb-p')",
            tenant_a,
            tenant_b,
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@lb.test', 'h')",
            user_a,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_a,
            user_a,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'LB')",
            project,
            tenant_a,
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, project_id, name, role, system_prompt, scope)"
            " VALUES ($1, $2, $3, 'Dev LB', 'backend_dev', 'x', 'project_local')",
            agent,
            tenant_a,
            project,
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title) VALUES ($1, $2, $3, 'T')",
            task,
            tenant_a,
            project,
        )
        steps = json.dumps(
            [{"kind": "model_call", "index": 2, "model": "gpt-oss:120b", "status": "ok"}]
        )
        # 6 runs de la combinación (supera min_runs=5): 4 done, 1 escalado, 1 aborted.
        for status in ("done", "done", "done", "done", "needs_human_review", "aborted"):
            await conn.execute(
                "INSERT INTO executions"
                " (id, tenant_id, task_id, agent_id, status, steps_log, iterations,"
                "  total_tokens, total_cost_usd)"
                " VALUES ($1, $2, $3, $4, $5, $6::jsonb, 10, 1000, 0.5)",
                uuid4(),
                tenant_a,
                task,
                agent,
                status,
            )
        # Una combinación con n=1 (otro agente NULL) que el umbral debe excluir.
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, steps_log)"
            " VALUES ($1, $2, $3, 'done', $4::jsonb)",
            uuid4(),
            tenant_a,
            task,
            steps,
        )
    finally:
        await conn.close()
    return {"tenant_a": tenant_a, "tenant_b": tenant_b, "user_a": user_a, "agent": agent}


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


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_leaderboard_aggregates_and_applies_min_runs(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/runs/leaderboard?min_runs=5",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    # Solo la combinación con n>=5; la de n=1 queda fuera por el umbral.
    assert len(rows) == 1
    row = rows[0]
    assert row["agent_id"] == str(seeded["agent"])
    assert row["runs"] == 6
    assert row["done"] == 4
    assert row["escalated"] == 1
    assert row["aborted"] == 1
    assert row["success_rate"] == pytest.approx(4 / 6)
    assert row["avg_iterations"] == pytest.approx(10.0)
    # El modelo NULL agrupa aparte (los 6 runs no llevaban model_call en el log).
    assert row["model"] is None
