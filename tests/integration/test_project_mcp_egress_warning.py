"""Guardar un servidor MCP con host externo: fail-OPEN con aviso, fail-CLOSED de forma
(`task_mk_02`, ADR 0165 D11), y el sondeo de la allowlist es sólo de System Admin (D7.3).

Con base de datos real, porque lo que se prueba es el contrato HTTP completo del
`PUT /projects/{id}` y del `GET`: que el 200 lleve `mcp_server_warnings` cuando el
host no está en `egress.mcp_allowed_hosts`, que deje de llevarlo cuando sí está,
que un host interno (sin punto) nunca avise, y que la FORMA prohibida —IP literal,
`http://` externo— sea 422 con motivo. La regla de fondo del ADR: bloquear el
guardado dejaría al `tenant_admin` sin poder declarar justo el artefacto con el
que pide la apertura.
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
_ALLOWLIST_KEY = "egress.mcp_allowed_hosts"


async def _seed(dsn: str, *, allowed_hosts: list[str] | None = None) -> dict[str, UUID]:
    tenant = uuid4()
    user = uuid4()
    project = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE skills, tools, agents, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant,
            "Tenant A",
            "tenant-a",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user,
            "alice@a.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant,
            user,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, mcp_servers)"
            " VALUES ($1, $2, $3, 'active', '[]'::jsonb)",
            project,
            tenant,
            "Project A",
        )
        # El ajuste de plataforma se fija POR DEBAJO de la API (no hay System Admin
        # en este seed): lo que se prueba aquí es el guardado del tenant, no el PUT
        # del ajuste, que tiene su propia cobertura.
        await conn.execute("DELETE FROM platform_settings WHERE key = $1", _ALLOWLIST_KEY)
        if allowed_hosts is not None:
            await conn.execute(
                "INSERT INTO platform_settings (key, value) VALUES ($1, $2::jsonb)",
                _ALLOWLIST_KEY,
                json.dumps(allowed_hosts),
            )
    finally:
        await conn.close()
    return {"tenant": tenant, "user": user, "project": project}


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


def _remote(url: str = "https://mcp.atlassian.com/v1/mcp") -> dict[str, object]:
    return {"name": "atlassian", "transport": "streamable_http", "url": url}


async def _client_for(configured_app, dsn: str, **seed_kw):
    seeded = await _seed(dsn, **seed_kw)
    token = await _mint_token(seeded["user"], seeded["tenant"])
    client = AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return client, seeded


@pytest.mark.asyncio
async def test_saving_a_remote_host_outside_the_allowlist_is_200_with_a_typed_warning(
    configured_app, migrations_pg_dsn: str
) -> None:
    client, seeded = await _client_for(configured_app, migrations_pg_dsn)
    async with client:
        resp = await client.put(f"/projects/{seeded['project']}", json={"mcp_servers": [_remote()]})
        assert resp.status_code == 200, resp.text
        warnings = resp.json()["mcp_server_warnings"]
        assert len(warnings) == 1
        assert warnings[0]["server"] == "atlassian"
        assert warnings[0]["host"] == "mcp.atlassian.com"
        assert warnings[0]["code"] == "EGRESS_HOST_NOT_ALLOWLISTED"
        assert "System Admin" in warnings[0]["message"]
        # D7.2: el aviso afirma MENOS que «permitido» / «pendiente de aplicar».
        assert "permitido" not in warnings[0]["message"].lower()

        # Y la ficha lo repite: el diálogo se cierra al guardar, la página es donde
        # el operador lo va a leer.
        got = await client.get(f"/projects/{seeded['project']}")
        assert got.status_code == 200
        assert [w["host"] for w in got.json()["mcp_server_warnings"]] == ["mcp.atlassian.com"]


@pytest.mark.asyncio
async def test_an_allowlisted_host_saves_without_warning(
    configured_app, migrations_pg_dsn: str
) -> None:
    client, seeded = await _client_for(
        configured_app, migrations_pg_dsn, allowed_hosts=["mcp.atlassian.com"]
    )
    async with client:
        resp = await client.put(f"/projects/{seeded['project']}", json={"mcp_servers": [_remote()]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["mcp_server_warnings"] == []


@pytest.mark.asyncio
async def test_an_internal_compose_host_never_warns(configured_app, migrations_pg_dsn: str) -> None:
    """Sin punto = servicio del compose: se exime por NO_PROXY, y `http://` es lo
    normal ahí (`http://docling:5001/mcp`)."""
    client, seeded = await _client_for(configured_app, migrations_pg_dsn)
    async with client:
        resp = await client.put(
            f"/projects/{seeded['project']}",
            json={
                "mcp_servers": [
                    {
                        "name": "docling",
                        "transport": "streamable_http",
                        "url": "http://docling:5001/mcp",
                    }
                ]
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["mcp_server_warnings"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("http://mcp.atlassian.com/v1/mcp", "https"),
        ("https://10.0.0.5/mcp", "IP literal"),
        ("https://169.254.169.254/latest", "IP literal"),
        ("https://mcp.atlassian.com:9443/mcp", "443 y 8443"),
    ],
)
async def test_a_prohibited_url_shape_is_422_with_the_reason(
    configured_app, migrations_pg_dsn: str, url: str, fragment: str
) -> None:
    client, seeded = await _client_for(configured_app, migrations_pg_dsn)
    async with client:
        resp = await client.put(
            f"/projects/{seeded['project']}", json={"mcp_servers": [_remote(url)]}
        )
        assert resp.status_code == 422, resp.text
        assert fragment in resp.text


@pytest.mark.asyncio
async def test_the_allowlist_probe_is_not_for_tenant_admins(
    configured_app, migrations_pg_dsn: str
) -> None:
    """D7.3: el sondeo convierte al api-server en un cliente que abre conexiones
    hacia fuera; lo dispara el mismo actor que puede escribir el ajuste, y nadie más."""
    client, _seeded = await _client_for(configured_app, migrations_pg_dsn)
    async with client:
        resp = await client.post("/admin/egress/mcp-allowlist/probe", json={})
        assert resp.status_code == 403, resp.text
