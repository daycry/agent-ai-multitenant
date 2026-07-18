"""P1-03/P1-10 (auditoría proyecto 2026-07-17): settings honestos.

- `execution_budgets` y `guardrails_config` estaban APLICADOS (clamp en
  dispatch, merge en worker) pero eran inconfigurables: ninguna API los
  exponía. Ahora viajan en GET/PUT /projects (el PUT ya exige tenant_admin).
- `repository_config` mezclaba claves de plataforma (last_git_sync,
  review_image) con claves del cliente: un PUT del cliente las pisaba.
  Merge server-side: las claves de plataforma se preservan.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


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


async def _token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant, admin, proj = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE projects, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)",
            tenant,
            f"sc-{tenant.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@sc.test', 'h')", admin
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant,
            admin,
        )
        # repository_config con claves de PLATAFORMA ya escritas por el sistema.
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, repository_config)"
            " VALUES ($1, $2, 'P', 'active',"
            ' \'{"language": "php", "last_git_sync": "2026-07-09T10:00:00Z",'
            ' "review_image": "agent-runtime-php:1"}\'::jsonb)',
            proj,
            tenant,
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "admin": admin, "proj": proj}


@pytest.mark.asyncio
async def test_execution_budgets_and_guardrails_round_trip(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    budgets = {"max_total_tokens": 500000, "max_iterations": 40}
    guardrails = {"pre_llm": [{"rule": "pii_scrub"}]}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        put = await client.put(
            f"/projects/{seeded['proj']}",
            json={"execution_budgets": budgets, "guardrails_config": guardrails},
            headers=headers,
        )
        assert put.status_code == 200, put.text
        got = await client.get(f"/projects/{seeded['proj']}", headers=headers)
    body = got.json()
    assert body["execution_budgets"] == budgets
    assert body["guardrails_config"] == guardrails


@pytest.mark.asyncio
async def test_repository_config_put_preserves_platform_keys(
    configured_app, migrations_pg_dsn: str
) -> None:
    """P1-10: el PUT del cliente no pisa last_git_sync/review_image."""
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        put = await client.put(
            f"/projects/{seeded['proj']}",
            json={"repository_config": {"language": "python", "framework": "fastapi"}},
            headers=headers,
        )
        assert put.status_code == 200, put.text
    body = put.json()
    assert body["repository_config"]["language"] == "python"
    assert body["repository_config"]["framework"] == "fastapi"
    # Las claves de plataforma sobreviven al PUT del cliente.
    assert body["repository_config"]["last_git_sync"] == "2026-07-09T10:00:00Z"
    assert body["repository_config"]["review_image"] == "agent-runtime-php:1"
