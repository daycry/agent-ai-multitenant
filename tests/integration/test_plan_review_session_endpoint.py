"""GET /plans/{id}/review-session — la sesión de review ACTIVA de un plan.

QA humano 2026-07-07: el detalle del plan enlaza a `/admin/review/active?plan=…`
pero esa ruta no existía y la dinámica `[id]` tragaba "active" como session id
(error boundary). El panel necesita resolver plan → sesión activa; este endpoint
es esa resolución (la página `active` del panel redirige con su respuesta).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str, *, with_active_session: bool) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "user": uuid4(),
        "project": uuid4(),
        "plan": uuid4(),
        "session_terminal": uuid4(),
        "session_active": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE review_sessions, tasks, plans, projects, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Org', 'org-rs'),"
            " ($2, 'Platform', 'platform-rs')",
            ids["tenant"],
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@rs.test', 'x')",
            ids["user"],
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            ids["tenant"],
            ids["user"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, slug, status, is_template)"
            " VALUES ($1, $2, 'P', 'p-rs', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, slug, status)"
            " VALUES ($1, $2, $3, 'Plan', 'plan-rs', 'pending_human_validation')",
            ids["plan"],
            ids["tenant"],
            ids["project"],
        )
        expires = datetime.now(UTC) + timedelta(hours=48)
        # Una sesión TERMINAL siempre presente: el endpoint debe ignorarla.
        await conn.execute(
            "INSERT INTO review_sessions (id, tenant_id, plan_id, spec, status, expires_at)"
            " VALUES ($1, $2, $3, '{}'::jsonb, 'approved', $4)",
            ids["session_terminal"],
            ids["tenant"],
            ids["plan"],
            expires,
        )
        if with_active_session:
            await conn.execute(
                "INSERT INTO review_sessions (id, tenant_id, plan_id, spec, status, expires_at)"
                " VALUES ($1, $2, $3, '{}'::jsonb, 'running', $4)",
                ids["session_active"],
                ids["tenant"],
                ids["plan"],
                expires,
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


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_returns_the_active_session_ignoring_terminal_ones(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn, with_active_session=True)
    token = await _mint_token(ids["user"], ids["tenant"])
    transport = ASGITransport(app=configured_app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get(
            f"/plans/{ids['plan']}/review-session",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == str(ids["session_active"])
    assert body["status"] == "running"
    # Las URLs firmadas viajan en la respuesta — el panel las ofrece tal cual.
    assert body["review_url"].startswith("http")
    assert "sig=" in body["review_url"]


@pytest.mark.asyncio
async def test_without_active_session_falls_back_to_the_newest(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Contrato existente: sin sesión viva devuelve la MÁS RECIENTE (el panel
    puede enseñar el veredicto de una sesión ya terminal), nunca 404 si hubo
    alguna sesión."""
    ids = await _seed(migrations_pg_dsn, with_active_session=False)
    token = await _mint_token(ids["user"], ids["tenant"])
    transport = ASGITransport(app=configured_app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get(
            f"/plans/{ids['plan']}/review-session",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["session_id"] == str(ids["session_terminal"])
    assert resp.json()["status"] == "approved"
    # ADR 0107: el contrato incluye el motivo de rechazo (None si la sesión
    # terminal fue aprobada) — la tarjeta de correcciones lo consume.
    assert "rejection_reason" in resp.json()
    assert resp.json()["rejection_reason"] is None
