"""Córtex F3 (bloque 2) — endpoints de identidad ``/owner/cortex/identity``.

Ejercita el router de identidad end-to-end sobre el app real (DB + Redis):

  * ``GET /owner/cortex/identity``: owner → 200 con la identidad default honesta
    (``ensure_identity``), ``onboarded_at=null``; non-owner → 403 (gate
    DB-authoritative, incluso forjando ``own``).
  * ``PUT /owner/cortex/identity``: onboarding co-diseñado — el owner fija
    name/core_values/narrative/language → versiona en ``cortex_identity_history``
    (updated_by='owner_override') y marca ``onboarded_at`` (era NULL); un PUT
    parcial posterior NO re-marca onboarded_at ni borra campos no enviados.
  * **Campos no editables protegidos**: un PUT con ``traits``/``mood_baseline`` →
    422 (``extra=forbid``); el owner nunca pisa los derivados por la reflexión.
  * **cross-owner**: el owner solo ve/edita su propia fila.

Fixtures espejo de ``test_cortex_mind_endpoints.py``.
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


async def _seed_two_owners(dsn: str, *, owner_is_owner: bool = True) -> dict[str, UUID]:
    owner_id = uuid4()
    other_id = uuid4()
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE cortex_identity_history, cortex_identity, cortex_turns,"
            " cortex_conversations, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex Identity Tenant",
            "cortex-identity-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, $3, $4), ($5, $6, $7, false)",
            owner_id,
            "owner@identity.test",
            "h",
            owner_is_owner,
            other_id,
            "other@identity.test",
            "h",
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
    return {"owner_id": owner_id, "other_id": other_id, "tenant_id": tenant_id}


async def _mint(user_id: UUID, tenant_id: UUID, *, owner_claim: bool = True) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(
        user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_owner=owner_claim
    )


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ===========================================================================
# GET — default honesto + gate
# ===========================================================================
@pytest.mark.asyncio
async def test_get_identity_non_owner_gets_403(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn, owner_is_owner=False)
    # Forja el claim `own`; el gate DB-authoritative debe rechazar igualmente.
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner_claim=True)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.get("/owner/cortex/identity", headers=headers)
        assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_get_identity_returns_default_with_null_onboarded(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn)
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.get("/owner/cortex/identity", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Default honesto: nombre neutro, valores vacíos, narrativa vacía.
        assert body["name"]  # nombre por defecto no vacío
        assert body["core_values"] == []
        assert body["narrative"] == ""
        assert body["language"] == "es"
        # Derivados presentes (Big-Five + baseline PAD neutros).
        assert set(body["traits"]) == {
            "openness",
            "conscientiousness",
            "extraversion",
            "agreeableness",
            "neuroticism",
        }
        assert set(body["mood_baseline"]) == {"valence", "arousal", "dominance"}
        # onboarding pendiente.
        assert body["onboarded_at"] is None
        assert body["version"] == 0


# ===========================================================================
# PUT — onboarding co-diseñado + versionado + onboarded_at
# ===========================================================================
@pytest.mark.asyncio
async def test_put_identity_onboards_versions_and_marks_onboarded(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    token = await _mint(owner_id, seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.put(
            "/owner/cortex/identity",
            json={
                "name": "Atlas",
                "core_values": ["honestidad", "curiosidad"],
                "narrative": "Soy Atlas, el córtex del owner.",
                "language": "es",
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Atlas"
        assert body["core_values"] == ["honestidad", "curiosidad"]
        assert body["version"] == 1
        assert body["updated_by"] == "owner_override"
        # onboarded_at se marcó (era NULL).
        assert body["onboarded_at"] is not None
        first_onboarded = body["onboarded_at"]

        # Un PUT parcial posterior (solo language) NO borra name/core_values ni
        # re-marca onboarded_at.
        resp2 = await client.put("/owner/cortex/identity", json={"language": "en"}, headers=headers)
        assert resp2.status_code == 200, resp2.text
        body2 = resp2.json()
        assert body2["language"] == "en"
        assert body2["name"] == "Atlas"  # preservado
        assert body2["core_values"] == ["honestidad", "curiosidad"]
        assert body2["version"] == 2
        assert body2["onboarded_at"] == first_onboarded  # idempotente

    # Versionado en history: v1 (onboarding) + v2 (override).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        versions = await conn.fetch(
            "SELECT version, updated_by FROM cortex_identity_history"
            " WHERE owner_user_id = $1 ORDER BY version ASC",
            owner_id,
        )
    finally:
        await conn.close()
    assert [r["version"] for r in versions] == [1, 2]
    assert all(r["updated_by"] == "owner_override" for r in versions)


@pytest.mark.asyncio
async def test_put_identity_rejects_non_editable_fields(
    configured_app, migrations_pg_dsn: str
) -> None:
    """traits/mood_baseline NO son editables por el owner (extra=forbid → 422)."""
    seed = await _seed_two_owners(migrations_pg_dsn)
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.put(
            "/owner/cortex/identity",
            json={"name": "X", "traits": {"openness": 1.0}},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        resp2 = await client.put(
            "/owner/cortex/identity",
            json={"mood_baseline": {"valence": 0.9, "arousal": 0.5, "dominance": 0.0}},
            headers=headers,
        )
        assert resp2.status_code == 422, resp2.text


@pytest.mark.asyncio
async def test_put_identity_non_owner_gets_403(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn, owner_is_owner=False)
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner_claim=True)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.put("/owner/cortex/identity", json={"name": "Hack"}, headers=headers)
        assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_identity_endpoints_cross_owner_isolated(
    configured_app, migrations_pg_dsn: str
) -> None:
    """El owner solo ve/edita su propia fila; la de otro usuario nunca se cruza."""
    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    other_id = seed["other_id"]

    # El OTRO usuario (no-owner) ya tiene una identidad propia con un nombre.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO cortex_identity"
            " (id, owner_user_id, identity_state, version, updated_by, onboarded_at)"
            " VALUES ($1, $2, $3::jsonb, 5, 'reflection', now())",
            uuid4(),
            other_id,
            '{"name": "Eco", "core_values": ["secreto"], "narrative": "no tuya"}',
        )
    finally:
        await conn.close()

    token = await _mint(owner_id, seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        # El owner ve SU default, no la de Eco.
        resp = await client.get("/owner/cortex/identity", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] != "Eco"
        # Un PUT del owner NO toca la fila de Eco.
        await client.put("/owner/cortex/identity", json={"name": "Atlas"}, headers=headers)

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        eco = await conn.fetchval(
            "SELECT identity_state->>'name' FROM cortex_identity WHERE owner_user_id = $1",
            other_id,
        )
        eco_version = await conn.fetchval(
            "SELECT version FROM cortex_identity WHERE owner_user_id = $1", other_id
        )
    finally:
        await conn.close()
    assert eco == "Eco"  # intacta
    assert eco_version == 5


# ===========================================================================
# POST /reflect — disparo manual (best-effort, gated)
# ===========================================================================
@pytest.mark.asyncio
async def test_reflect_now_enqueues_for_owner(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn)
    captured: list[UUID] = []

    async def _fake_enqueue(owner_user_id: UUID) -> bool:
        captured.append(owner_user_id)
        return True

    import api_server.celery_client as cc

    monkeypatch.setattr(cc, "enqueue_cortex_reflection", _fake_enqueue)

    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.post("/owner/cortex/reflect", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["enqueued"] is True
    assert captured == [seed["owner_id"]]


@pytest.mark.asyncio
async def test_reflect_now_non_owner_gets_403(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn, owner_is_owner=False)
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner_claim=True)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.post("/owner/cortex/reflect", headers=headers)
        assert resp.status_code == 403, resp.text


# ===========================================================================
# relationship_model — "lo que sé de ti" visible para el owner (identidad real)
# ===========================================================================
@pytest.mark.asyncio
async def test_get_identity_expone_relationship_model(
    configured_app, migrations_pg_dsn: str
) -> None:
    import json as _json
    from uuid import uuid4 as _uuid4

    import asyncpg as _asyncpg

    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]

    conn = await _asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO cortex_identity (id, owner_user_id, identity_state, version,"
            " updated_by, created_at, updated_at) VALUES ($1, $2, $3::jsonb, 2,"
            " 'reflection', now(), now())",
            _uuid4(),
            owner_id,
            _json.dumps({"name": "Lumen", "relationship_model": {"prefiere": "evidencia primero"}}),
        )
    finally:
        await conn.close()

    token = await _mint(owner_id, seed["tenant_id"])
    async with _client(configured_app) as client:
        resp = await client.get(
            "/owner/cortex/identity", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # El owner VE lo que el córtex cree saber de él (deriva de la reflexión).
    assert body["relationship_model"] == {"prefiere": "evidencia primero"}


@pytest.mark.asyncio
async def test_get_identity_default_relationship_model_vacio(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn)
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    async with _client(configured_app) as client:
        resp = await client.get(
            "/owner/cortex/identity", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200
    assert resp.json()["relationship_model"] == {}
