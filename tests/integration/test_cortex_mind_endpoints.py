"""Córtex F2 — endpoints del Panel de Mente ``/owner/cortex/*`` (FASE F).

Ejercita el router ``cortex_mind`` end-to-end sobre el app real (DB + Redis):

  * ``/mind``: no-owner → 403 (gate DB-authoritative, incluso forjando ``own``);
    owner → 200 con PAD/mood/drives/intensity + bloque honesty; **cross-owner**:
    el owner A nunca ve el estado de B.
  * ``/affect/timeseries``: N snapshots del owner + 1 de otro → sólo los del owner
    en orden cronológico ASC; cross-owner aislado; y el contrato paramétrico del
    plan (``since``/``until`` acotan la ventana, ``limit`` recorta a los MÁS
    recientes dentro de ella).
  * ``/episodes``: memorias episódicas emocionales del owner en ``memory_entries``
    (scope=private, metadata_.cortex=true, metadata_.emotion PRESENTE, mood_label)
    → filtra por ``emotion``, respeta ``limit``, incluye ``appraisal_reason``, nunca
    devuelve memorias de otro user ni memorias del córtex sin afecto.

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
    created_at: datetime | None = None,
) -> None:
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
    await _insert_memory_row(
        dsn,
        user_id=user_id,
        tenant_id=tenant_id,
        content=f"episodio: {appraisal_reason}",
        metadata_json=meta,
        created_at=created_at,
    )


async def _insert_cortex_memory_without_emotion(
    dsn: str,
    *,
    user_id: UUID,
    tenant_id: UUID,
    content: str,
    kind: str,
    mem_type: str = "semantic",
) -> None:
    """Siembra una memoria del córtex marcada ``cortex=true`` pero SIN bloque
    ``emotion`` — la forma exacta que escriben ``cortex/memory.py:249``
    (cortex_remember), ``cortex/curiosity.py:169`` (kind='learning') y
    ``workers/cortex_reflection.py:464``/``:524`` (kind='reflection'/'owner_model'):
    scope=private, user_id=owner, cortex=True y ningún dato afectivo.

    ``mem_type`` importa: la reflexión y la curiosidad escriben ``semantic``, pero
    cortex_remember admite ``episodic`` (``cortex/memory.py:53,215``), así que filtrar
    por ``type`` NO sería un sustituto del filtro que pide el contrato."""
    await _insert_memory_row(
        dsn,
        user_id=user_id,
        tenant_id=tenant_id,
        content=content,
        metadata_json=json.dumps({"cortex": True, "kind": kind, "source": "cortex_test"}),
        created_at=None,
        mem_type=mem_type,
    )


async def _insert_memory_row(
    dsn: str,
    *,
    user_id: UUID,
    tenant_id: UUID,
    content: str,
    metadata_json: str,
    created_at: datetime | None,
    mem_type: str = "episodic",
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        if created_at is None:
            await conn.execute(
                "INSERT INTO memory_entries"
                " (id, tenant_id, scope, type, content, user_id, metadata)"
                " VALUES ($1, $2, 'private', $3, $4, $5, $6::jsonb)",
                uuid4(),
                tenant_id,
                mem_type,
                content,
                user_id,
                metadata_json,
            )
        else:
            await conn.execute(
                "INSERT INTO memory_entries"
                " (id, tenant_id, scope, type, content, user_id, metadata, created_at)"
                " VALUES ($1, $2, 'private', $3, $4, $5, $6::jsonb, $7)",
                uuid4(),
                tenant_id,
                mem_type,
                content,
                user_id,
                metadata_json,
                created_at,
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


async def _seed_hourly_series(dsn: str, owner_id: UUID, base: datetime, n: int) -> None:
    """``n`` snapshots del owner, uno por hora desde ``base``, con valence
    identificable (0.0, 0.1, 0.2…) para poder afirmar CUÁLES vuelven."""
    for i in range(n):
        await _insert_snapshot(
            dsn,
            owner_id=owner_id,
            valence=round(i / 10, 1),
            mood_label=f"m{i}",
            created_at=base + timedelta(hours=i),
        )


@pytest.mark.asyncio
async def test_timeseries_since_and_until_bound_the_window(
    configured_app, migrations_pg_dsn: str
) -> None:
    """El plan exigía que la serie respetase ``since``/``until``; el test que existía
    llamaba al endpoint SIN parámetros, así que la parametrización estaba implementada
    pero sin ejercer. Defecto que atrapa: que un filtro se ignore (devolvería los 5
    puntos) o que se aplique al revés (devolvería el complemento de la ventana).

    Los cortes van a medio camino entre dos snapshots, para no depender de si los
    límites son inclusivos o exclusivos (el plan no lo especifica)."""
    seed = await _seed_two_owners(migrations_pg_dsn)
    base = datetime.now(UTC) - timedelta(hours=10)
    await _seed_hourly_series(migrations_pg_dsn, seed["owner_id"], base, 5)
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        # Ventana (h0.5, h3.5) ⇒ los snapshots de h1, h2 y h3.
        both = await client.get(
            "/owner/cortex/affect/timeseries",
            params={
                "since": (base + timedelta(minutes=30)).isoformat(),
                "until": (base + timedelta(hours=3, minutes=30)).isoformat(),
            },
            headers=headers,
        )
        assert both.status_code == 200, both.text
        assert [p["valence"] for p in both.json()] == pytest.approx([0.1, 0.2, 0.3])

        # Sólo `since` ⇒ cola de la serie.
        only_since = await client.get(
            "/owner/cortex/affect/timeseries",
            params={"since": (base + timedelta(hours=2, minutes=30)).isoformat()},
            headers=headers,
        )
        assert only_since.status_code == 200, only_since.text
        assert [p["valence"] for p in only_since.json()] == pytest.approx([0.3, 0.4])

        # Sólo `until` ⇒ cabeza de la serie.
        only_until = await client.get(
            "/owner/cortex/affect/timeseries",
            params={"until": (base + timedelta(hours=1, minutes=30)).isoformat()},
            headers=headers,
        )
        assert only_until.status_code == 200, only_until.text
        assert [p["valence"] for p in only_until.json()] == pytest.approx([0.0, 0.1])

        # Ventana vacía (posterior a todo) ⇒ lista vacía, no error ni serie entera.
        empty = await client.get(
            "/owner/cortex/affect/timeseries",
            params={"since": (base + timedelta(days=1)).isoformat()},
            headers=headers,
        )
        assert empty.status_code == 200, empty.text
        assert empty.json() == []


@pytest.mark.asyncio
async def test_timeseries_limit_keeps_the_most_recent_in_ascending_order(
    configured_app, migrations_pg_dsn: str
) -> None:
    """``limit`` acota a los MÁS RECIENTES y el resultado sigue en orden ASC (contrato
    del handler y del cliente, ``apps/admin-panel/lib/cortex.ts:237-239``). Defecto que
    atrapa: recortar por la cabeza (devolvería los más antiguos, que en un gráfico de
    mood es la ventana equivocada) o devolver el recorte en DESC por olvidar el
    ``reverse()``. El snapshot MÁS NUEVO de la tabla es de OTRO owner: comprueba que
    el ``limit`` se aplica DESPUÉS del filtro por owner y no lo desplaza."""
    seed = await _seed_two_owners(migrations_pg_dsn)
    base = datetime.now(UTC) - timedelta(hours=10)
    await _seed_hourly_series(migrations_pg_dsn, seed["owner_id"], base, 5)
    await _insert_snapshot(
        migrations_pg_dsn,
        owner_id=seed["other_id"],
        valence=-0.9,
        mood_label="tensión",
        created_at=base + timedelta(hours=9),
    )
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.get(
            "/owner/cortex/affect/timeseries", params={"limit": 2}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [p["valence"] for p in body] == pytest.approx([0.3, 0.4])
        ts = [p["created_at"] for p in body]
        assert ts == sorted(ts)

        # `limit` mayor que la serie no inventa puntos ni rompe el orden.
        wide = await client.get(
            "/owner/cortex/affect/timeseries", params={"limit": 50}, headers=headers
        )
        assert wide.status_code == 200, wide.text
        assert [p["valence"] for p in wide.json()] == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.4])


@pytest.mark.asyncio
async def test_timeseries_limit_applies_inside_the_since_until_window(
    configured_app, migrations_pg_dsn: str
) -> None:
    """La combinación de los tres parámetros: ``limit`` recorta DENTRO de la ventana,
    no sobre la serie completa. Defecto que atrapa: aplicar ``limit`` antes de los
    cortes temporales (la respuesta saldría vacía o con puntos de fuera)."""
    seed = await _seed_two_owners(migrations_pg_dsn)
    base = datetime.now(UTC) - timedelta(hours=10)
    await _seed_hourly_series(migrations_pg_dsn, seed["owner_id"], base, 5)
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.get(
            "/owner/cortex/affect/timeseries",
            params={
                "since": (base + timedelta(minutes=30)).isoformat(),
                "until": (base + timedelta(hours=3, minutes=30)).isoformat(),
                "limit": 2,
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        # Ventana = h1,h2,h3; los 2 más recientes de ESA ventana, en ASC.
        assert [p["valence"] for p in resp.json()] == pytest.approx([0.2, 0.3])


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


@pytest.mark.asyncio
async def test_episodes_excludes_cortex_memories_without_emotion(
    configured_app, migrations_pg_dsn: str
) -> None:
    """El contrato del plan (cortex-f2-afectivo.md:78) exige CUATRO condiciones para que
    una memoria sea un "episodio": scope=private, user_id=owner, ``metadata_.cortex=true``
    y ``metadata_.emotion`` PRESENTE. El test que existía sólo sembraba episodios
    afectivos, así que no podía detectar la falta de la cuarta.

    Defecto que atrapa: ``cortex=true`` no es exclusivo del distilador afectivo — lo
    escriben también cortex_remember (``cortex/memory.py:249``), las memorias de
    curiosidad (``cortex/curiosity.py:169``) y la reflexión
    (``workers/cortex_reflection.py:464`` y ``:524``), todas sin bloque ``emotion``. Si
    el handler no exige ``emotion``, esas memorias entran en el mapa afectivo como
    episodios con valence/arousal/dominance/mood_label a ``null`` y lo contaminan."""
    seed = await _seed_two_owners(migrations_pg_dsn)
    await _insert_episode(
        migrations_pg_dsn,
        user_id=seed["owner_id"],
        tenant_id=seed["tenant_id"],
        mood_label="alegría",
        appraisal_reason="el owner me elogió",
    )
    # Las tres formas reales de memoria del córtex SIN afecto (la primera con
    # type='episodic', como puede escribirla cortex_remember).
    for content, kind, mem_type in (
        ("el owner prefiere el castellano", "fact", "episodic"),
        ("aprendí qué es un DAG", "learning", "semantic"),
        ("reflexión periódica del córtex", "reflection", "semantic"),
    ):
        await _insert_cortex_memory_without_emotion(
            migrations_pg_dsn,
            user_id=seed["owner_id"],
            tenant_id=seed["tenant_id"],
            content=content,
            kind=kind,
            mem_type=mem_type,
        )
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.get("/owner/cortex/episodes", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Ningún episodio sin datos afectivos: el mapa se dibuja con PAD.
        assert all(e["mood_label"] is not None for e in body), body
        assert all(e["valence"] is not None for e in body), body
        assert len(body) == 1
        assert body[0]["appraisal_reason"] == "el owner me elogió"


@pytest.mark.asyncio
async def test_episodes_limit_keeps_the_most_recent(configured_app, migrations_pg_dsn: str) -> None:
    """``limit`` es parte de la firma del endpoint en el contrato del plan
    (``?emotion=&limit=``) y el cliente lo documenta como "los más recientes primero"
    (``apps/admin-panel/lib/cortex.ts:256-258``); ningún test lo pasaba. Defecto que
    atrapa: recortar por la cabeza (el mapa mostraría los episodios más viejos) o
    ignorar el parámetro."""
    seed = await _seed_two_owners(migrations_pg_dsn)
    base = datetime.now(UTC) - timedelta(hours=5)
    for i, reason in enumerate(("el más viejo", "el de en medio", "el más nuevo")):
        await _insert_episode(
            migrations_pg_dsn,
            user_id=seed["owner_id"],
            tenant_id=seed["tenant_id"],
            mood_label="alegría",
            appraisal_reason=reason,
            created_at=base + timedelta(hours=i),
        )
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.get("/owner/cortex/episodes", params={"limit": 2}, headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [e["appraisal_reason"] for e in body] == ["el más nuevo", "el de en medio"]

        # `limit` se aplica DESPUÉS del filtro por emotion, no antes.
        await _insert_episode(
            migrations_pg_dsn,
            user_id=seed["owner_id"],
            tenant_id=seed["tenant_id"],
            mood_label="tensión",
            appraisal_reason="el más nuevo de todos, pero tenso",
            created_at=base + timedelta(hours=4),
        )
        filt = await client.get(
            "/owner/cortex/episodes",
            params={"emotion": "alegría", "limit": 2},
            headers=headers,
        )
        assert filt.status_code == 200, filt.text
        assert [e["appraisal_reason"] for e in filt.json()] == ["el más nuevo", "el de en medio"]
