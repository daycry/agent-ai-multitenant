"""Córtex F2 — WS de telemetría ``/ws/owner/cortex/telemetry`` (FASE G).

Gate DB-authoritative (ADR 0074) + tail del stream Redis ``cortex:telemetry:{owner}``:

  * sin token / token de un NO-owner ⇒ cierre 1008 (aunque forje el claim ``own``);
  * con token del System Owner ⇒ acepta y, tras un ``publish_cortex_affect_event``,
    recibe el frame ``{type:'affect', payload:{…}}`` serializado.

Patrón TestClient + seed tomado de ``test_ws_tenant_isolation.py`` /
``test_cortex_turns_endpoint.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
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


@pytest.fixture()
def ws_client(configured_app, test_redis_url: str) -> Iterator[TestClient]:
    from api_server.auth.deps import get_redis
    from redis.asyncio import Redis

    configured_app.dependency_overrides[get_redis] = lambda: Redis.from_url(
        test_redis_url, decode_responses=True
    )
    try:
        yield TestClient(configured_app)
    finally:
        configured_app.dependency_overrides.clear()


async def _seed(dsn: str, *, owner_is_owner: bool = True) -> dict[str, UUID]:
    owner_id = uuid4()
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex WS Tenant",
            "cortex-ws-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, $3, $4)",
            owner_id,
            "owner@ws-cortex.test",
            "h",
            owner_is_owner,
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


async def _mint_token(user_id: UUID, tenant_id: UUID, *, owner_claim: bool) -> str:
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore
    from redis.asyncio import Redis

    from tests.integration.conftest import TEST_REDIS_URL

    sid = uuid7()
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        await SessionStore(redis).create(
            sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
        )
    finally:
        await redis.aclose()
    return encode_jwt(
        user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_owner=owner_claim
    )


def _mint(user_id: UUID, tenant_id: UUID, *, owner_claim: bool = True) -> str:
    return asyncio.run(_mint_token(user_id, tenant_id, owner_claim=owner_claim))


def _expect_1008(ws_client: TestClient, url: str) -> None:
    with (
        ws_client.websocket_connect(url) as ws,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        ws.receive_json()
    assert exc.value.code == 1008


# ===========================================================================
# Gate
# ===========================================================================
def test_ws_rejects_missing_token(ws_client) -> None:
    _expect_1008(ws_client, "/ws/owner/cortex/telemetry")


def test_ws_rejects_non_owner(ws_client, migrations_pg_dsn: str) -> None:
    """Un usuario que NO es system owner en la BD (aunque forje el claim ``own``)
    recibe cierre 1008 (gate DB-authoritative)."""
    seed = asyncio.run(_seed(migrations_pg_dsn, owner_is_owner=False))
    token = _mint(seed["owner_id"], seed["tenant_id"], owner_claim=True)
    _expect_1008(ws_client, f"/ws/owner/cortex/telemetry?token={token}")


# ===========================================================================
# Happy path — el owner recibe su frame de afecto
# ===========================================================================
def test_ws_owner_receives_affect_frame(
    ws_client, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    seed = asyncio.run(_seed(migrations_pg_dsn, owner_is_owner=True))
    token = _mint(seed["owner_id"], seed["tenant_id"], owner_claim=True)

    async def _publish() -> None:
        from api_server.events import publish_cortex_affect_event
        from redis.asyncio import Redis

        redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
        try:
            await publish_cortex_affect_event(
                redis,
                str(seed["owner_id"]),
                payload={
                    "valence": 0.6,
                    "arousal": 0.4,
                    "dominance": 0.1,
                    "intensity": 0.5,
                    "mood_label": "alegría",
                    "drives": {
                        "curiosity": 0.6,
                        "bonding": 0.5,
                        "coherence": 0.5,
                        "competence": 0.5,
                    },
                    "appraisal_reason": "el owner me elogió",
                },
            )
        finally:
            await redis.aclose()

    asyncio.run(_publish())

    with ws_client.websocket_connect(f"/ws/owner/cortex/telemetry?token={token}") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "affect"
    assert frame["payload"]["mood_label"] == "alegría"
    assert frame["payload"]["appraisal_reason"] == "el owner me elogió"
    assert frame["payload"]["valence"] == pytest.approx(0.6)
