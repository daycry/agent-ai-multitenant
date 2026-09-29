"""GET /projects/{id}/capabilities — la sección MCP/Tools/Skills (`task_ui_20`).

El endpoint ya existía (Hub de Capacidad, SABER/RECORDAR/HACER, task_06_17_08); `task_ui_20`
lo AMPLÍA de forma aditiva para el proyecto con:

* ``mcp``: cada servidor con **procedencia** (declarado / desplegado desde el listing X vY),
  **conexión** OAuth, «N tools importadas» (``count_imported_tools``) y **egress** (D11);
* ``tools`` y ``skills``: lo desplegado en el proyecto desde el marketplace (ADR 0142).

Lo que fija: los datos salen del dominio (nada inventado), un despliegue retirado no cuenta,
y NADA de otro tenant asoma (RLS + ``tenant_id`` explícito).
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

_SERVERS = [
    {
        "name": "atlassian",
        "transport": "streamable_http",
        "url": "https://mcp.atlassian.com/v1/mcp",
    },
    {"name": "docs", "transport": "http", "url": "https://docs.example.com/mcp"},
    {"name": "interno", "transport": "http", "url": "http://mcp-interno:8080/mcp"},
]


async def _seed_tenant(conn: asyncpg.Connection, *, slug: str, source: UUID) -> dict[str, UUID]:
    keys = (
        "tenant",
        "user",
        "project",
        "other",
        "listing_mcp",
        "listing_tool",
        "listing_skill",
        "listing_old",
    )
    ids = {k: uuid4() for k in keys}
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $2)", ids["tenant"], slug
    )
    await conn.execute(
        "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'h')",
        ids["user"],
        f"{slug}@t.test",
    )
    await conn.execute(
        "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
        " VALUES ($1, $2, $3, 'tenant_member')",
        uuid4(),
        ids["tenant"],
        ids["user"],
    )
    await conn.execute(
        "INSERT INTO projects (id, tenant_id, name, mcp_servers) VALUES ($1, $2, $3, $4::jsonb)",
        ids["project"],
        ids["tenant"],
        f"Proyecto {slug}",
        json.dumps(_SERVERS),
    )
    await conn.execute(
        "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
        ids["other"],
        ids["tenant"],
        f"Otro {slug}",
    )
    # `docs.search` importada; `atlassian` sin importar.
    await conn.execute(
        "INSERT INTO tools (id, tenant_id, name, description, category,"
        " implementation_type, security_level, is_builtin)"
        " VALUES ($1, $2, 'docs.search', 'x', 'mcp', 'mcp_tool', 'safe', false)",
        uuid4(),
        ids["tenant"],
    )
    for key, kind, name in (
        ("listing_mcp", "mcp_server", f"Docs MCP {slug}"),
        ("listing_tool", "tool", f"Status checker {slug}"),
        ("listing_skill", "skill", f"Doc contracts {slug}"),
        ("listing_old", "tool", f"Herramienta retirada {slug}"),
    ):
        await conn.execute(
            "INSERT INTO marketplace_listings (id, source_id, tenant_id, kind, name, version,"
            " trust_level, manifest, requested_permissions)"
            " VALUES ($1,$2,NULL,$3,$4,'1.0.0','verified','{}'::jsonb,'[]'::jsonb)",
            ids[key],
            source,
            kind,
            name,
        )

    async def deploy(listing: UUID, version: str, status: str, refs: dict) -> UUID:  # type: ignore[type-arg]
        inst, dep = uuid4(), uuid4()
        await conn.execute(
            "INSERT INTO marketplace_installations (id, tenant_id, listing_id, version, status,"
            " installed_by) VALUES ($1,$2,$3,$4,'enabled',$5)",
            inst,
            ids["tenant"],
            listing,
            version,
            ids["user"],
        )
        await conn.execute(
            "INSERT INTO marketplace_deployments (id, tenant_id, installation_id, project_id,"
            " deployed_version, status, created_refs) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)",
            dep,
            ids["tenant"],
            inst,
            ids["project"],
            version,
            status,
            json.dumps(refs),
        )
        return dep

    ids["dep_mcp"] = await deploy(ids["listing_mcp"], "1.2.0", "active", {"mcp_servers": ["docs"]})
    ids["dep_tool"] = await deploy(
        ids["listing_tool"],
        "2.0.0",
        "active",
        {"agent_tools": [{"agent_id": str(uuid4()), "tool_id": str(uuid4())}]},
    )
    ids["dep_skill"] = await deploy(ids["listing_skill"], "0.3.0", "active", {"agent_skills": []})
    ids["dep_old"] = await deploy(ids["listing_old"], "1.0.0", "retired", {"agent_tools": []})
    return ids


async def _seed(dsn: str) -> dict[str, dict[str, UUID]]:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_deployments, marketplace_installations,"
            " marketplace_listing_versions, marketplace_listings, marketplace_sources,"
            " tools, projects, user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'P', 'cap-p')",
            _PLATFORM_TENANT_ID,
        )
        source = uuid4()
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type, is_trusted)"
            " VALUES ($1,'oficial-cap','official',true)",
            source,
        )
        a = await _seed_tenant(conn, slug="cap-a", source=source)
        b = await _seed_tenant(conn, slug="cap-b", source=source)
    finally:
        await conn.close()
    return {"a": a, "b": b}


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


async def _get(app, project: UUID, token: str, *, expect: int = 200) -> dict:  # type: ignore[no-untyped-def,type-arg]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{project}/capabilities", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == expect, resp.text
    return resp.json()  # type: ignore[no-any-return]


def _servers(body: dict) -> dict[str, dict]:  # type: ignore[type-arg]
    return {s["name"]: s for s in body["mcp"]}


@pytest.mark.asyncio
async def test_existing_contract_is_intact(configured_app, migrations_pg_dsn: str) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    body = await _get(configured_app, a["project"], await _token(a["user"], a["tenant"]))
    assert body["entity_type"] == "project"
    assert {"saber", "recordar", "hacer", "warnings"} <= set(body)


@pytest.mark.asyncio
async def test_mcp_provenance_declared_vs_deployed(configured_app, migrations_pg_dsn: str) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    body = await _get(configured_app, a["project"], await _token(a["user"], a["tenant"]))
    servers = _servers(body)
    assert list(servers) == ["atlassian", "docs", "interno"]
    assert servers["atlassian"]["origin"]["kind"] == "declared"
    assert servers["atlassian"]["origin"]["deployment_id"] is None
    docs = servers["docs"]["origin"]
    assert docs["kind"] == "marketplace"
    assert docs["listing_name"] == "Docs MCP cap-a"
    assert docs["version"] == "1.2.0"
    assert docs["deployment_id"] == str(a["dep_mcp"])
    assert docs["listing_id"] == str(a["listing_mcp"])


@pytest.mark.asyncio
async def test_mcp_connection_imported_tools_and_egress(
    configured_app, migrations_pg_dsn: str
) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    body = await _get(configured_app, a["project"], await _token(a["user"], a["tenant"]))
    s = _servers(body)
    # OAuth: atlassian lo exige y no hay token en Vault; docs no usa OAuth.
    assert s["atlassian"]["oauth"] == {"required": True, "connected": False}
    assert s["docs"]["oauth"] == {"required": False, "connected": None}
    # «N tools importadas» = filas `<server>.*`.
    assert s["docs"]["imported_tools"] == 1
    assert s["atlassian"]["imported_tools"] == 0
    # Egress D11: host externo fuera de la allowlist; el interno no avisa.
    assert s["docs"]["egress"]["blocked"] is True
    assert s["docs"]["egress"]["host"] == "docs.example.com"
    assert s["interno"]["egress"] == {"blocked": False, "host": None, "code": None}


@pytest.mark.asyncio
async def test_deployed_tools_and_skills_exclude_retired(
    configured_app, migrations_pg_dsn: str
) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    body = await _get(configured_app, a["project"], await _token(a["user"], a["tenant"]))
    assert [(t["name"], t["version"], t["agents"]) for t in body["tools"]] == [
        ("Status checker cap-a", "2.0.0", 1)
    ]
    assert [(t["name"], t["version"]) for t in body["skills"]] == [("Doc contracts cap-a", "0.3.0")]
    assert "Herramienta retirada" not in json.dumps(body)


@pytest.mark.asyncio
async def test_project_without_capabilities_has_empty_sections(
    configured_app, migrations_pg_dsn: str
) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    body = await _get(configured_app, a["other"], await _token(a["user"], a["tenant"]))
    assert body["mcp"] == [] and body["tools"] == [] and body["skills"] == []


@pytest.mark.asyncio
async def test_another_tenants_project_is_404_and_nothing_leaks(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    token = await _token(a["user"], a["tenant"])
    await _get(configured_app, b["project"], token, expect=404)
    text = json.dumps(await _get(configured_app, a["project"], token))
    assert "cap-b" not in text
    assert str(b["dep_mcp"]) not in text


@pytest.mark.asyncio
async def test_requires_authentication(configured_app, migrations_pg_dsn: str) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/projects/{a['project']}/capabilities")
    assert resp.status_code == 401
