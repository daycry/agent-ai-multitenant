"""ADR 0100 (pieza 2) — el install ENABLED materializa capacidad NATIVA.

Sobre BD real: un listing verified kind=skill instala ENABLED y produce una
fila ``skills`` con provenance (source_listing/installation/version); un tool
de red (mcp_tool) produce su fila ``tools``; un python_function queda DIFERIDO
honesto (sin fila); el uninstall soft-borra la fila materializada en la misma
transacción (no-orfandad); y el flujo consent (community) materializa solo al
ENABLE. El endpoint fresco corre además el gate de análisis estático previo
(skip honesto sin artifact root — comportamiento existente).
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

pytestmark = [pytest.mark.integration]

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant = uuid4()
    user = uuid4()
    source = uuid4()
    skill_listing = uuid4()
    tool_listing = uuid4()
    deferred_listing = uuid4()
    community_listing = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_audit_entries, marketplace_installations,"
            " marketplace_listings, marketplace_sources, skills, tools,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,$2,$3), ($4,$5,$6)",
            tenant,
            "Tenant M",
            "tenant-m",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1,$2,$3)",
            user,
            "meg@m.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1,$2,$3,'tenant_admin')",
            uuid4(),
            tenant,
            user,
        )
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type, is_trusted)"
            " VALUES ($1,'oficial','official',true)",
            source,
        )

        async def _listing(lid: UUID, kind: str, name: str, trust: str, manifest: dict) -> None:
            await conn.execute(
                "INSERT INTO marketplace_listings"
                " (id, source_id, tenant_id, kind, name, version, trust_level,"
                "  manifest, requested_permissions)"
                " VALUES ($1,$2,NULL,$3,$4,'1.0.0',$5,$6::jsonb,$7::jsonb)",
                lid,
                source,
                kind,
                name,
                trust,
                json.dumps(manifest),
                json.dumps(
                    [{"type": "network_policy", "value": "egress-allowlist"}]
                    if trust != "verified"
                    else []
                ),
            )

        await _listing(
            skill_listing,
            "skill",
            "skill-revision-api",
            "verified",
            {
                "prompt_fragment": "Revisa SIEMPRE los contratos de la API pública.",
                "category": "backend",
                "description": "Skill de revisión de contratos",
            },
        )
        await _listing(
            tool_listing,
            "tool",
            "http-status-checker",
            "verified",
            {
                "implementation_type": "http_endpoint",
                "implementation_ref": "https://status.example.com/api/check",
                "category": "network",
                "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}},
            },
        )
        await _listing(
            deferred_listing,
            "tool",
            "py-batch-runner",
            "verified",
            {"implementation_type": "python_function", "implementation_ref": "runner:main"},
        )
        await _listing(
            community_listing,
            "skill",
            "skill-community",
            "community",
            {"prompt_fragment": "Usa siempre tabs.", "category": "docs"},
        )
    finally:
        await conn.close()

    return {
        "tenant": tenant,
        "user": user,
        "skill_listing": skill_listing,
        "tool_listing": tool_listing,
        "deferred_listing": deferred_listing,
        "community_listing": community_listing,
    }


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


async def _fetch_one(dsn: str, query: str, *args: object):
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(query, *args)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_verified_skill_install_materializes_row(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(seeded["skill_listing"])},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        installation_id = resp.json()["id"]

    row = await _fetch_one(
        migrations_pg_dsn,
        "SELECT name, prompt_fragment, category, source_listing_id,"
        " source_installation_id, source_version, deleted_at"
        " FROM skills WHERE source_installation_id = $1",
        UUID(installation_id),
    )
    assert row is not None, "el install ENABLED debe materializar la fila skills"
    assert row["prompt_fragment"].startswith("Revisa SIEMPRE")
    assert row["category"] == "backend"
    assert row["source_listing_id"] == seeded["skill_listing"]
    assert row["source_version"] == "1.0.0"
    assert row["deleted_at"] is None

    # Uninstall → la fila cae con su instalación (no-orfandad).
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        gone = await client.delete(f"/marketplace/installations/{installation_id}", headers=headers)
        assert gone.status_code == 204, gone.text
    row = await _fetch_one(
        migrations_pg_dsn,
        "SELECT deleted_at FROM skills WHERE source_installation_id = $1",
        UUID(installation_id),
    )
    assert row is not None and row["deleted_at"] is not None


@pytest.mark.asyncio
async def test_network_tool_install_materializes_tool(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(seeded["tool_listing"])},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        installation_id = resp.json()["id"]

    row = await _fetch_one(
        migrations_pg_dsn,
        "SELECT name, implementation_type, implementation_ref, security_level, input_schema"
        " FROM tools WHERE source_installation_id = $1",
        UUID(installation_id),
    )
    assert row is not None
    assert row["implementation_type"] == "http_endpoint"
    assert row["implementation_ref"] == "https://status.example.com/api/check"
    # Mínimo privilegio por defecto para material de terceros.
    assert row["security_level"] == "sandboxed"
    assert "url" in json.loads(row["input_schema"])["properties"]


@pytest.mark.asyncio
async def test_python_tool_is_deferred_not_materialized(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(seeded["deferred_listing"])},
            headers=headers,
        )
        # El install SIGUE aceptándose (intent honesto)…
        assert resp.status_code == 201, resp.text
        installation_id = resp.json()["id"]

    # …pero NO hay fila de catálogo (diferido hasta el sandbox, ADR 0081 B/C).
    row = await _fetch_one(
        migrations_pg_dsn,
        "SELECT id FROM tools WHERE source_installation_id = $1",
        UUID(installation_id),
    )
    assert row is None


@pytest.mark.asyncio
async def test_community_skill_materializes_only_on_consent_enable(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(seeded["community_listing"])},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        installation_id = body["id"]
        assert body["status"] == "disabled"

        # Sin consent → sin capacidad.
        row = await _fetch_one(
            migrations_pg_dsn,
            "SELECT id FROM skills WHERE source_installation_id = $1",
            UUID(installation_id),
        )
        assert row is None

        # Grant de todos los permisos → ENABLED → materializa.
        consent = await client.post(
            f"/marketplace/installations/{installation_id}/consent",
            json={"decisions": [{"type": "network_policy", "decision": "grant"}]},
            headers=headers,
        )
        assert consent.status_code == 200, consent.text

    row = await _fetch_one(
        migrations_pg_dsn,
        "SELECT prompt_fragment, deleted_at FROM skills WHERE source_installation_id = $1",
        UUID(installation_id),
    )
    assert row is not None and row["deleted_at"] is None
    assert row["prompt_fragment"] == "Usa siempre tabs."
