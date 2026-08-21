"""prod-03 task_prod03_08 — la mitad negativa: un candado dice que NO, con 422.

`resolve_config(..., strict=True)` y `LockedFieldOverrideError` existían desde el
Plan 11 y **no tenían un solo llamante fuera de tests**. En producción el motor
resolvía siempre en modo laxo, donde un intento de relajar un guardrail
bloqueado se ignora y queda anotado en `rejected_overrides` — una lista que
nadie leía. El resultado práctico: un tenant admin podía «desactivar el PII» en
su capa, ver que la petición devolvía 200, y creer que lo había desactivado.
Nada se lo desmentía.

Aquí se fija que la API dice que no, y que lo dice CON NOMBRE: qué guardrail, en
qué hook y qué capa lo intentó. Un 422 opaco en esta ruta no sirve — el que la
recibe está intentando apagar una guarda de seguridad y tiene que saber cuál no
puede.

Tres maneras de intentar saltarse un candado, y las tres tienen que fallar:
sobrescribirlo, degradar su acción, y borrarlo con `remove: true`. La tercera es
la que más fácil se cuela: no «modifica» nada, lo hace desaparecer.
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
    ids = {"tenant": uuid4(), "admin": uuid4(), "project": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE guardrail_configs, user_org_memberships, projects, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            ids["tenant"],
            "Locked T",
            f"locked-{ids['tenant'].hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'h')",
            ids["admin"],
            f"locked-{ids['admin'].hex[:8]}@gc.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            ids["tenant"],
            ids["admin"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
    finally:
        await conn.close()
    return ids


async def _seed_platform_baseline(admin_database_url: str) -> None:
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


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
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


_LOCKED_KEY = "platform_pii"
_LOCKED_HOOK = "post_llm"


def _override(entry: dict) -> dict:
    return {"config": {"guardrails": {_LOCKED_HOOK: [entry]}}}


@pytest.mark.parametrize(
    ("label", "entry"),
    [
        # Sobrescribirlo con otra config.
        ("overwrite", {"type": "pii", "id": _LOCKED_KEY, "config": {"entities": []}}),
        # Degradar su acción a un warn inocuo.
        ("weaken", {"type": "pii", "id": _LOCKED_KEY, "action": "warn"}),
        # Hacerlo desaparecer. La que más fácil se cuela: no modifica nada.
        ("remove", {"type": "pii", "id": _LOCKED_KEY, "config": {"remove": True}}),
    ],
)
@pytest.mark.asyncio
async def test_a_tenant_cannot_touch_a_platform_locked_guardrail(
    configured_app, migrations_pg_dsn: str, admin_database_url: str, label: str, entry: dict
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _seed_platform_baseline(admin_database_url)
    token = await _mint_token(ids["admin"], ids["tenant"])

    async with _client(configured_app) as client:
        resp = await client.put(
            "/guardrails/config/layers/tenant",
            json=_override(entry),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 422, f"{label}: {resp.status_code} {resp.text}"
    detail = resp.json()["detail"]
    assert detail["error"] == "locked_guardrail_override"
    assert detail["key"] == _LOCKED_KEY
    assert detail["hook"] == _LOCKED_HOOK
    assert detail["layer"] == "tenant"
    assert _LOCKED_KEY in detail["message"]


@pytest.mark.asyncio
async def test_the_project_layer_is_gated_by_the_same_lock(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Bajar una capa más no abre el candado: la plataforma manda en las dos."""
    ids = await _seed(migrations_pg_dsn)
    await _seed_platform_baseline(admin_database_url)
    token = await _mint_token(ids["admin"], ids["tenant"])

    async with _client(configured_app) as client:
        resp = await client.put(
            f"/guardrails/config/layers/project/{ids['project']}",
            json=_override({"type": "pii", "id": _LOCKED_KEY, "action": "warn"}),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 422
    assert resp.json()["detail"]["layer"] == "project"


@pytest.mark.asyncio
async def test_a_permitted_override_does_persist(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """La guarda de la guarda: el candado no puede convertirse en «nada se toca».

    Añadir un check propio —o endurecer uno no bloqueado— es exactamente lo que
    las capas existen para permitir. Si esto fallara, el 422 de arriba estaría
    midiendo un CRUD roto en vez de un candado.
    """
    ids = await _seed(migrations_pg_dsn)
    await _seed_platform_baseline(admin_database_url)
    token = await _mint_token(ids["admin"], ids["tenant"])

    async with _client(configured_app) as client:
        resp = await client.put(
            "/guardrails/config/layers/tenant",
            json={
                "config": {
                    "guardrails": {
                        "pre_tool": [
                            {
                                "type": "allowed_domains",
                                "config": {"allowed_domains": ["intranet.example"]},
                            }
                        ]
                    }
                }
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        effective = await client.get(
            "/guardrails/config", headers={"Authorization": f"Bearer {token}"}
        )

    assert resp.status_code == 200, resp.text
    body = effective.json()
    domains = body["config"]["guardrails"]["pre_tool"][0]["config"]["allowed_domains"]
    assert domains == ["intranet.example"]
    # Y el baseline sigue ahí: endurecer no puede haber borrado los candados.
    assert "platform_pii" in {k for keys in body["locked_keys"].values() for k in keys}
