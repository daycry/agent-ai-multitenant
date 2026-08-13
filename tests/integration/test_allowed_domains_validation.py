"""task_prod12_ssrf_03 — el api-server valida las entradas de `allowed_domains`.

Primera capa de la defensa SSRF (prod-12 Fase A): al persistir la allowlist del
proyecto se rechazan IPs literales, `localhost`, hostnames internos del compose
y nombres no-FQDN, con mensaje claro; las entradas válidas se normalizan
(minúsculas, sin esquema/puerto) para que el match textual del runtime sea
exacto. El ssrf_guard del runtime re-valida ADEMÁS cada resolución.
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

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "user": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE projects, user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Org', 'org-ad'),"
            " ($2, 'Platform', 'platform-ad')",
            ids["tenant"],
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@ad.test', 'x')",
            ids["user"],
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            ids["tenant"],
            ids["user"],
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
async def test_valid_entries_are_normalised_and_persisted(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint_token(ids["user"], ids["tenant"])
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://t"
    ) as client:
        resp = await client.post(
            "/projects",
            json={
                "name": "P1",
                "allowed_domains": [
                    "Api.Example.COM",
                    "https://docs.example.com/some/path",
                    "api.example.com:443",
                ],
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # Minúsculas, sin esquema/puerto/ruta, dedupe (la 3ª colapsa con la 1ª).
        assert body["allowed_domains"] == ["api.example.com", "docs.example.com"]

        fetched = await client.get(f"/projects/{body['id']}", headers=headers)
        assert fetched.json()["allowed_domains"] == ["api.example.com", "docs.example.com"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry", "fragment"),
    [
        ("169.254.169.254", "literal IP"),
        ("10.0.0.5", "literal IP"),
        ("[::1]", "literal IP"),
        ("localhost", "internal"),
        ("app.localhost", "internal"),
        ("vault", "internal"),
        ("redis", "internal"),
        ("host.docker.internal", "internal"),
        ("myhost", "not a fully-qualified"),
        ("*.example.com", "not a valid domain"),
    ],
)
async def test_invalid_entries_are_rejected_with_a_clear_message(
    configured_app, migrations_pg_dsn: str, entry: str, fragment: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint_token(ids["user"], ids["tenant"])
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://t"
    ) as client:
        resp = await client.post(
            "/projects",
            json={"name": "P2", "allowed_domains": [entry]},
            headers=headers,
        )
    assert resp.status_code == 422, resp.text
    assert fragment in resp.text


@pytest.mark.asyncio
async def test_update_validates_and_clears_to_deny_all(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint_token(ids["user"], ids["tenant"])
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://t"
    ) as client:
        created = await client.post(
            "/projects",
            json={"name": "P3", "allowed_domains": ["api.example.com"]},
            headers=headers,
        )
        pid = created.json()["id"]
        # PUT con entrada inválida → 422, sin tocar la lista.
        bad = await client.put(
            f"/projects/{pid}", json={"allowed_domains": ["127.0.0.1"]}, headers=headers
        )
        assert bad.status_code == 422
        # `[]` explícito limpia a deny-all.
        cleared = await client.put(
            f"/projects/{pid}", json={"allowed_domains": []}, headers=headers
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["allowed_domains"] == []
