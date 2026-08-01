"""prod-03 task_prod03_08 — el CRUD de capas y el baseline con candados.

Dos cosas que el Plan 11 prometió y nunca entregó, y que se prueban aquí por la
RUTA HTTP y contra PostgreSQL de verdad:

  1. **El baseline de plataforma existe.** `shared_guardrails/layers.py` lleva
     desde el Plan 11 diciendo en su primer párrafo que «PII / secret-leakage /
     prompt-injection baselines live here and are mandatory». No había ninguno:
     `LockedFieldOverrideError` no tenía un solo llamante fuera de tests, porque
     no había ningún guardrail `locked` que un tenant pudiera intentar relajar.
  2. **La capa TENANT existe.** Antes solo había plataforma y proyecto, así que
     endurecer los guardrails de un tenant obligaba a ir proyecto por proyecto.

El fichero hermano, `test_guardrail_locked_override_422.py`, se ocupa de la
mitad negativa (qué pasa cuando alguien intenta saltarse un candado).
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


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "member_a": uuid4(),
        "admin_b": uuid4(),
        "project_a": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE guardrail_configs, user_org_memberships, projects, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "GC Tenant A",
            f"gc-cfg-a-{ids['tenant_a'].hex[:8]}",
            ids["tenant_b"],
            "GC Tenant B",
            f"gc-cfg-b-{ids['tenant_b'].hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["admin_a"],
            f"admin-a-{ids['admin_a'].hex[:8]}@gc.test",
            "h",
            ids["member_a"],
            f"member-a-{ids['member_a'].hex[:8]}@gc.test",
            "h",
            ids["admin_b"],
            f"admin-b-{ids['admin_b'].hex[:8]}@gc.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'),"
            " ($4, $5, $6, 'tenant_user'),"
            " ($7, $8, $9, 'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
            uuid4(),
            ids["tenant_a"],
            ids["member_a"],
            uuid4(),
            ids["tenant_b"],
            ids["admin_b"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'Proyecto A', 'active', false)",
            ids["project_a"],
            ids["tenant_a"],
        )
    finally:
        await conn.close()
    return ids


async def _seed_platform_baseline(admin_database_url: str) -> None:
    """Siembra el baseline por el camino real (engine admin, BYPASSRLS)."""
    from api_server.seeds.guardrail_baseline import seed_platform_guardrail_baseline
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s, s.begin():
            await seed_platform_guardrail_baseline(s)
    finally:
        await engine.dispose()


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


async def _mint_token(user_id: UUID, tenant_id: UUID | None) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# El baseline
# ===========================================================================
@pytest.mark.asyncio
async def test_the_platform_baseline_seeds_three_locked_guardrails(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Los tres que el plan nombra, bloqueados, y visibles desde el tenant."""
    from api_server.seeds.guardrail_baseline import BASELINE_LOCKED_KEYS

    ids = await _seed(migrations_pg_dsn)
    await _seed_platform_baseline(admin_database_url)
    token = await _mint_token(ids["admin_a"], ids["tenant_a"])

    async with _client(configured_app) as client:
        resp = await client.get("/guardrails/config", headers=_auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    locked = {k for keys in body["locked_keys"].values() for k in keys}
    assert set(BASELINE_LOCKED_KEYS) <= locked
    # Y la procedencia dice de dónde vienen, que es lo que la UI necesita para
    # poder explicar por qué no se pueden tocar.
    platform_owned = {p["key"] for p in body["provenance"] if p["winning_layer"] == "platform"}
    assert set(BASELINE_LOCKED_KEYS) <= platform_owned


@pytest.mark.asyncio
async def test_seeding_twice_does_not_overwrite_an_operators_edits(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """El seed es idempotente y NO pisa: subir los tres a `block` es una
    decisión del operador, y un re-arranque que la revirtiera sería un rollback
    silencioso de su postura de seguridad."""
    from api_server.seeds.guardrail_baseline import seed_platform_guardrail_baseline
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    await _seed(migrations_pg_dsn)
    await _seed_platform_baseline(admin_database_url)

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s, s.begin():
            await s.execute(
                __import__("sqlalchemy").text(
                    "UPDATE guardrail_configs SET config = config" " WHERE scope = 'platform'"
                )
            )
        async with sm() as s, s.begin():
            seeded_again = await seed_platform_guardrail_baseline(s)
    finally:
        await engine.dispose()

    assert seeded_again is False


# ===========================================================================
# CRUD de las capas
# ===========================================================================
@pytest.mark.asyncio
async def test_a_tenant_admin_writes_reads_and_deletes_its_tenant_layer(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _seed_platform_baseline(admin_database_url)
    token = await _mint_token(ids["admin_a"], ids["tenant_a"])
    payload = {
        "config": {
            "guardrails": {
                "pre_tool": [
                    {"type": "allowed_domains", "config": {"allowed_domains": ["example.com"]}}
                ]
            }
        }
    }

    async with _client(configured_app) as client:
        created = await client.put(
            "/guardrails/config/layers/tenant", json=payload, headers=_auth(token)
        )
        assert created.status_code == 200, created.text
        assert created.json()["version"] == 1

        # Reescribir la misma capa incrementa `version` — es lo que permite
        # invalidar una caché sin releer el JSONB entero.
        again = await client.put(
            "/guardrails/config/layers/tenant", json=payload, headers=_auth(token)
        )
        assert again.json()["version"] == 2

        read = await client.get("/guardrails/config/layers/tenant", headers=_auth(token))
        assert read.status_code == 200
        assert read.json()["config"] == payload["config"]

        effective = await client.get("/guardrails/config", headers=_auth(token))
        keys = {p["key"] for p in effective.json()["provenance"]}
        assert "allowed_domains" in keys

        gone = await client.delete("/guardrails/config/layers/tenant", headers=_auth(token))
        assert gone.status_code == 204
        assert (
            await client.get("/guardrails/config/layers/tenant", headers=_auth(token))
        ).status_code == 404


@pytest.mark.asyncio
async def test_the_project_layer_wins_over_the_tenant_one(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """La capa más específica gana — mientras no toque un candado."""
    ids = await _seed(migrations_pg_dsn)
    await _seed_platform_baseline(admin_database_url)
    token = await _mint_token(ids["admin_a"], ids["tenant_a"])

    async with _client(configured_app) as client:
        await client.put(
            "/guardrails/config/layers/tenant",
            json={
                "config": {
                    "guardrails": {
                        "pre_tool": [
                            {
                                "type": "allowed_domains",
                                "config": {"allowed_domains": ["tenant.example"]},
                            }
                        ]
                    }
                }
            },
            headers=_auth(token),
        )
        await client.put(
            f"/guardrails/config/layers/project/{ids['project_a']}",
            json={
                "config": {
                    "guardrails": {
                        "pre_tool": [
                            {
                                "type": "allowed_domains",
                                "config": {"allowed_domains": ["project.example"]},
                            }
                        ]
                    }
                }
            },
            headers=_auth(token),
        )
        effective = await client.get(
            f"/guardrails/config?project_id={ids['project_a']}", headers=_auth(token)
        )

    body = effective.json()
    winner = next(p for p in body["provenance"] if p["key"] == "allowed_domains")
    assert winner["winning_layer"] == "project"
    domains = body["config"]["guardrails"]["pre_tool"][0]["config"]["allowed_domains"]
    assert domains == ["project.example"]


@pytest.mark.asyncio
async def test_a_plain_member_cannot_write_a_layer(configured_app, migrations_pg_dsn: str) -> None:
    """Editar guardrails es superficie de admin: un miembro lee, no escribe."""
    ids = await _seed(migrations_pg_dsn)
    token = await _mint_token(ids["member_a"], ids["tenant_a"])

    async with _client(configured_app) as client:
        resp = await client.put(
            "/guardrails/config/layers/tenant", json={"config": {}}, headers=_auth(token)
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_a_tenant_never_sees_another_tenants_layer(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Principio nº1, por la ruta: la RLS de la 0132 lo respalda abajo."""
    ids = await _seed(migrations_pg_dsn)
    await _seed_platform_baseline(admin_database_url)
    token_a = await _mint_token(ids["admin_a"], ids["tenant_a"])
    token_b = await _mint_token(ids["admin_b"], ids["tenant_b"])

    async with _client(configured_app) as client:
        await client.put(
            "/guardrails/config/layers/tenant",
            json={
                "config": {
                    "guardrails": {
                        "pre_tool": [
                            {"type": "allowed_domains", "config": {"allowed_domains": ["a.test"]}}
                        ]
                    }
                }
            },
            headers=_auth(token_a),
        )
        b_read = await client.get("/guardrails/config/layers/tenant", headers=_auth(token_b))
        b_effective = await client.get("/guardrails/config", headers=_auth(token_b))

    assert b_read.status_code == 404
    assert "allowed_domains" not in {p["key"] for p in b_effective.json()["provenance"]}
    # …pero el baseline de plataforma SÍ lo ve: es la capa que todos heredan.
    assert b_effective.json()["locked_keys"]


@pytest.mark.asyncio
async def test_a_malformed_config_is_rejected_before_it_reaches_the_sandbox(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Un hook inventado se rechaza aquí, no en el runtime de un run real."""
    ids = await _seed(migrations_pg_dsn)
    token = await _mint_token(ids["admin_a"], ids["tenant_a"])

    async with _client(configured_app) as client:
        resp = await client.put(
            "/guardrails/config/layers/tenant",
            json={"config": {"guardrails": {"mid_llm": [{"type": "pii"}]}}},
            headers=_auth(token),
        )

    assert resp.status_code == 422
