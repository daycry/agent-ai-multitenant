"""Córtex F2 — endpoints del Panel de Mente ``/owner/cortex/*`` (FASE F).

Ejercita el router ``cortex_mind`` end-to-end sobre el app real (DB + Redis):

  * ``/mind``: no-owner → 403 (gate DB-authoritative, incluso forjando ``own``);
    owner → 200 con PAD/mood/drives/intensity + bloque honesty; **cross-owner**:
    el owner A nunca ve el estado de B.
  * ``/affect/timeseries``: N snapshots del owner + 1 de otro → sólo los del owner
    en orden cronológico, respetando ``limit``; cross-owner aislado.
  * ``/episodes``: memorias episódicas emocionales del owner en ``memory_entries``
    (scope=private, metadata_.cortex=true, emotion.mood_label) → filtra por
    ``emotion``, incluye ``appraisal_reason``, nunca devuelve memorias de otro user.

Fixtures espejo de ``test_cortex_turns_endpoint.py``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
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
    """Owner (flag is_system_owner) + un segundo user + tenant + membership."""
    owner_id = uuid4()
    other_id = uuid4()
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE cortex_affect_snapshots, cortex_turns, cortex_conversations,"
            " memory_entries, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex Mind Tenant",
            "cortex-mind-tenant",
        )
        # Sólo UN system_owner posible (uq_users_system_owner): el `owner` lo es
        # (si owner_is_owner); `other` es un usuario normal cuyo estado afectivo NO
        # debe filtrarse al owner (prueba de aislamiento por owner_user_id/user_id).
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, $3, $4), ($5, $6, $7, false)",
            owner_id,
            "owner@mind.test",
            "h",
            owner_is_owner,
            other_id,
            "other@mind.test",
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


async def _insert_snapshot(
    dsn: str,
    *,
    owner_id: UUID,
    valence: float,
    mood_label: str,
    created_at: datetime,
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO cortex_affect_snapshots"
            " (id, owner_user_id, valence, arousal, dominance, intensity,"
            "  mood_valence, mood_arousal, mood_dominance, mood_label, drives, created_at)"
            " VALUES ($1,$2,$3,0.4,0.0,0.2,$4,0.4,0.0,$5,"
            '  \'{"curiosity":0.6,"bonding":0.5,"coherence":0.5,"competence":0.5}\'::jsonb,$6)',
            uuid4(),
            owner_id,
            valence,
            valence * 0.5,
            mood_label,
            created_at,
        )
    finally:
        await conn.close()


async def _insert_episode(
    dsn: str,
    *,
    user_id: UUID,
    tenant_id: UUID,
    mood_label: str,
    appraisal_reason: str,
    cortex: bool = True,
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        meta = json.dumps(
            {
                "cortex": cortex,
                "emotion": {
                    "valence": 0.5,
                    "arousal": 0.4,
                    "dominance": 0.1,
                    "intensity": 0.6,
                    "mood_label": mood_label,
                    "appraisal_reason": appraisal_reason,
                },
            }
        )
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, user_id, metadata)"
            " VALUES ($1, $2, 'private', 'episodic', $3, $4, $5::jsonb)",
            uuid4(),
            tenant_id,
            f"episodio: {appraisal_reason}",
            user_id,
            meta,
        )
    finally:
        await conn.close()


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
# /mind
# ===========================================================================
@pytest.mark.asyncio
async def test_mind_non_owner_gets_403(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn, owner_is_owner=False)
    # Forja el claim `own`; el gate DB-authoritative debe rechazar igualmente.
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner_claim=True)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.get("/owner/cortex/mind", headers=headers)
        assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_mind_owner_gets_200_with_honesty(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn)
    await _insert_snapshot(
        migrations_pg_dsn,
        owner_id=seed["owner_id"],
        valence=0.6,
        mood_label="alegría",
        created_at=datetime.now(UTC),
    )
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.get("/owner/cortex/mind", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "valence" in body and "arousal" in body and "dominance" in body
        assert "intensity" in body
        assert body["mood_label"]
        assert set(body["drives"]) >= {"curiosity", "bonding", "coherence", "competence"}
        # Copy honesto SIEMPRE presente.
        assert "no sentimientos reales" in body["honesty"]["note_es"].lower()
        assert "not real feelings" in body["honesty"]["note_en"].lower()


@pytest.mark.asyncio
async def test_mind_cross_owner_isolated(configured_app, migrations_pg_dsn: str) -> None:
    """El owner (sin snapshot) ve baseline; el snapshot de OTRO usuario (con afecto
    propio en la tabla) nunca se filtra al owner (filtro owner_user_id explícito)."""
    seed = await _seed_two_owners(migrations_pg_dsn)
    await _insert_snapshot(
        migrations_pg_dsn,
        owner_id=seed["other_id"],
        valence=0.9,
        mood_label="alegría",
        created_at=datetime.now(UTC),
    )
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.get("/owner/cortex/mind", headers=headers)
        assert resp.status_code == 200, resp.text
        # A no tiene snapshot ⇒ baseline neutro (valence 0), NO el 0.9 de B.
        assert resp.json()["valence"] == pytest.approx(0.0, abs=1e-6)


# ===========================================================================
# /affect/timeseries
# ===========================================================================
@pytest.mark.asyncio
async def test_timeseries_owner_scoped_and_chronological(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn)
    base = datetime.now(UTC) - timedelta(hours=3)
    # 3 snapshots del owner + 1 de otro.
    await _insert_snapshot(
        migrations_pg_dsn,
        owner_id=seed["owner_id"],
        valence=0.1,
        mood_label="neutral",
        created_at=base,
    )
    await _insert_snapshot(
        migrations_pg_dsn,
        owner_id=seed["owner_id"],
        valence=0.5,
        mood_label="calma",
        created_at=base + timedelta(hours=1),
    )
    await _insert_snapshot(
        migrations_pg_dsn,
        owner_id=seed["owner_id"],
        valence=0.7,
        mood_label="alegría",
        created_at=base + timedelta(hours=2),
    )
    await _insert_snapshot(
        migrations_pg_dsn,
        owner_id=seed["other_id"],
        valence=-0.9,
        mood_label="tensión",
        created_at=base + timedelta(hours=1),
    )
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.get("/owner/cortex/affect/timeseries", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Sólo los 3 del owner, en orden cronológico ascendente.
        assert len(body) == 3
        assert [p["valence"] for p in body] == pytest.approx([0.1, 0.5, 0.7])
        ts = [p["created_at"] for p in body]
        assert ts == sorted(ts)


# ===========================================================================
# /episodes
# ===========================================================================
@pytest.mark.asyncio
async def test_episodes_filtered_and_user_scoped(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn)
    # Owner: dos episodios cortex (mood alegría + calma) + uno NO-cortex (excluido).
    await _insert_episode(
        migrations_pg_dsn,
        user_id=seed["owner_id"],
        tenant_id=seed["tenant_id"],
        mood_label="alegría",
        appraisal_reason="el owner me elogió",
    )
    await _insert_episode(
        migrations_pg_dsn,
        user_id=seed["owner_id"],
        tenant_id=seed["tenant_id"],
        mood_label="calma",
        appraisal_reason="charla tranquila",
    )
    await _insert_episode(
        migrations_pg_dsn,
        user_id=seed["owner_id"],
        tenant_id=seed["tenant_id"],
        mood_label="alegría",
        appraisal_reason="memoria normal",
        cortex=False,
    )
    # Otro user: un episodio cortex que NUNCA debe aparecer.
    await _insert_episode(
        migrations_pg_dsn,
        user_id=seed["other_id"],
        tenant_id=seed["tenant_id"],
        mood_label="alegría",
        appraisal_reason="secreto del otro",
    )
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        # Sin filtro: los 2 episodios cortex del owner (no el no-cortex, no el del otro).
        all_resp = await client.get("/owner/cortex/episodes", headers=headers)
        assert all_resp.status_code == 200, all_resp.text
        all_body = all_resp.json()
        assert len(all_body) == 2
        reasons = {e["appraisal_reason"] for e in all_body}
        assert reasons == {"el owner me elogió", "charla tranquila"}
        assert "secreto del otro" not in reasons

        # Filtrado por emotion=alegría: sólo ese.
        filt = await client.get(
            "/owner/cortex/episodes", params={"emotion": "alegría"}, headers=headers
        )
        assert filt.status_code == 200, filt.text
        filt_body = filt.json()
        assert len(filt_body) == 1
        assert filt_body[0]["mood_label"] == "alegría"
        assert filt_body[0]["appraisal_reason"] == "el owner me elogió"
