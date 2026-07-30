"""hallazgo #4 (QA 2026-07-07): sesión de review SIN app-preview configurada.

Antes, un proyecto sin ``repository_config.review_image`` lanzaba el placeholder
``alpine:3.20`` (que sale con exit 0 al instante) y el proxy moría con un
críptico ``review app unreachable: Name or service not known``. Ahora la sesión
se crea SIN contenedor (``spec.app_configured=false``) y el proxy responde un
409 honesto; ``session.json`` expone el flag para que el SPA lo muestre.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str, *, app_configured: bool | None) -> dict[str, UUID]:
    """Plan + sesión de review `running`. ``app_configured=None`` = spec legacy
    (sin el flag) — debe comportarse como configurada (retrocompatible)."""
    ids = {
        "tenant": uuid4(),
        "user": uuid4(),
        "project": uuid4(),
        "plan": uuid4(),
        "session": uuid4(),
    }
    spec = (
        "{}"
        if app_configured is None
        else ('{"app_configured": true}' if app_configured else '{"app_configured": false}')
    )
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE review_sessions, tasks, plans, projects, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Org', 'org-nc'),"
            " ($2, 'Platform', 'platform-nc')",
            ids["tenant"],
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, slug, status, is_template)"
            " VALUES ($1, $2, 'P', 'p-nc', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, slug, status)"
            " VALUES ($1, $2, $3, 'Plan', 'plan-nc', 'pending_human_validation')",
            ids["plan"],
            ids["tenant"],
            ids["project"],
        )
        await conn.execute(
            "INSERT INTO review_sessions (id, tenant_id, plan_id, spec, status, expires_at)"
            f" VALUES ($1, $2, $3, '{spec}'::jsonb, 'running', $4)",
            ids["session"],
            ids["tenant"],
            ids["plan"],
            datetime.now(UTC) + timedelta(hours=48),
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


def _signed_query(session_id: UUID) -> str:
    """exp=&sig= válidos para la sesión, con la misma firma del producto."""
    from api_server.routers.review import build_review_urls

    expires = (datetime.now(UTC) + timedelta(hours=1)).timestamp()
    urls = build_review_urls(session_id, expires)
    parsed = parse_qs(urlparse(urls["review_url"]).query)
    return f"exp={parsed['exp'][0]}&sig={parsed['sig'][0]}"


@pytest.mark.asyncio
async def test_proxy_returns_honest_409_when_app_not_configured(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn, app_configured=False)
    query = _signed_query(ids["session"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://t"
    ) as client:
        resp = await client.get(f"/review/{ids['session']}/app/?{query}")
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    # Mensaje accionable, no un error de DNS.
    assert "review_image" in detail
    assert "unreachable" not in detail


@pytest.mark.asyncio
async def test_session_json_exposes_app_configured_flag(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn, app_configured=False)
    query = _signed_query(ids["session"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://t"
    ) as client:
        resp = await client.get(f"/review/{ids['session']}/session.json?{query}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["app_configured"] is False


@pytest.mark.asyncio
async def test_legacy_session_without_flag_behaves_as_configured(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Una sesión anterior al flag (spec sin `app_configured`) NO recibe el 409:
    el proxy intenta el upstream como siempre (aquí, sin contenedor, 502)."""
    ids = await _seed(migrations_pg_dsn, app_configured=None)
    query = _signed_query(ids["session"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://t"
    ) as client:
        json_resp = await client.get(f"/review/{ids['session']}/session.json?{query}")
        app_resp = await client.get(f"/review/{ids['session']}/app/?{query}")
    assert json_resp.json()["app_configured"] is True
    assert app_resp.status_code == 502
