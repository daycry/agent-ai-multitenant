"""Córtex — endpoints ``GET/PUT /owner/cortex/autonomy`` (kill-switch + web, UI).

El owner gestiona desde la UI el kill-switch de los bucles autónomos y el gate
de la web del córtex (``cortex.web_enabled``, ADR 0067 — hasta ahora sin setter
ni UI). PUT parcial: cada campo es opcional; sin ninguno ⇒ 422. Gated
``require_system_owner`` (DB-authoritative).
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
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
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _seed_owner(dsn: str) -> dict[str, UUID]:
    owner_id = uuid4()
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE platform_settings, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Autonomy Tenant",
            "autonomy-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner, is_system_admin)"
            " VALUES ($1, $2, 'h', true, true)",
            owner_id,
            "owner@autonomy.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_id,
            owner_id,
        )
    finally:
        await conn.close()
    return {"owner_id": owner_id, "tenant_id": tenant_id}


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_owner=True)


@pytest.mark.asyncio
async def test_put_autonomy_togglea_kill_switch_y_web(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_owner(migrations_pg_dsn)
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Defaults seguros: todo OFF.
        snapshot = await client.get("/owner/cortex/autonomy", headers=headers)
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.json()["autonomy_enabled"] is False
        assert snapshot.json()["web_enabled"] is False

        # PUT parcial: solo la web (el kill-switch no cambia).
        resp = await client.put(
            "/owner/cortex/autonomy", json={"web_enabled": True}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["web_enabled"] is True
        assert body["autonomy_enabled"] is False

        # PUT parcial: solo el kill-switch (la web se conserva).
        resp = await client.put(
            "/owner/cortex/autonomy", json={"autonomy_enabled": True}, headers=headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["autonomy_enabled"] is True
        assert body["web_enabled"] is True

        # Sin ningún campo ⇒ 422 honesto.
        resp = await client.put("/owner/cortex/autonomy", json={}, headers=headers)
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_autonomy_expone_el_gasto_usd_del_dia_no_solo_las_busquedas(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    """El budget del snapshot lleva las DOS dimensiones: búsquedas y dólares.

    `record_spend` (lo que el bucle llama tras investigar) escribe dos contadores
    en Redis —búsquedas y USD— porque un tope de búsquedas NO es un tope de coste:
    una sola pasada con razonamiento profundo puede costar más que veinte
    búsquedas baratas (ADR 0078). Hasta ahora el snapshot solo leía la clave de
    búsquedas, así que el dinero gastado no lo enseñaba ninguna ruta: el owner
    veía «3 de 5 búsquedas» mientras el cap de 0.50 USD podía estar agotado.

    El test escribe con el PRODUCTOR real (`record_spend`), no con un `SET`
    manual: si la clave del gasto cambiase de forma, el lector dejaría de verla y
    esto se pone rojo — que es justo el acoplamiento que hay que fijar."""
    from datetime import UTC, datetime

    from api_server.cortex.autonomy import record_spend
    from redis.asyncio import Redis

    seed = await _seed_owner(migrations_pg_dsn)
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    now = datetime.now(UTC)
    redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        # Dos pasadas del bucle: 3 búsquedas y 0.12 USD en total.
        await record_spend(
            redis, owner_user_id=str(seed["owner_id"]), cost_usd=0.07, searches=2, now=now
        )
        await record_spend(
            redis, owner_user_id=str(seed["owner_id"]), cost_usd=0.05, searches=1, now=now
        )
    finally:
        await redis.aclose()

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/owner/cortex/autonomy", headers=headers)

    assert resp.status_code == 200, resp.text
    budget = resp.json()["budget"]
    assert budget["searches_today"] == 3
    assert budget["searches_cap"] == 5
    # La dimensión que faltaba: gasto del día y su tope (default 0.50, ADR 0078).
    assert budget["cost_usd_today"] == pytest.approx(0.12)
    assert budget["cost_usd_cap"] == pytest.approx(0.50)


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_el_gasto_de_otro_owner_no_se_ve_en_el_snapshot(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    """El budget es por clave-por-owner: el gasto ajeno no cuenta en el mío.

    El córtex es tenant-less y su eje de aislamiento es la clave-por-owner (ADR
    0074), así que la única cosa que separa el gasto de dos owners es el
    `owner_user_id` que entra en la clave Redis. Sin este caso, un snapshot que
    leyera una clave global (o la del primer owner) pasaría el test de arriba y
    le enseñaría al owner el dinero de otra persona."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from api_server.cortex.autonomy import record_spend
    from redis.asyncio import Redis

    seed = await _seed_owner(migrations_pg_dsn)
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    otro_owner = uuid4()
    now = datetime.now(UTC)
    redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        await record_spend(redis, owner_user_id=str(otro_owner), cost_usd=0.49, searches=4, now=now)
    finally:
        await redis.aclose()

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/owner/cortex/autonomy", headers=headers)

    assert resp.status_code == 200, resp.text
    budget = resp.json()["budget"]
    assert budget["cost_usd_today"] == pytest.approx(0.0)
    assert budget["searches_today"] == 0
