"""Córtex F2 — tabla cortex_affect_snapshots + persistencia owner-scoped.

Ejercita (a) la migración 0093: ``alembic upgrade head`` crea
``cortex_affect_snapshots`` + sus índices y NO le pone RLS (tenant-less sobre
BYPASSRLS, ADR 0074), el UNIQUE parcial por turno rechaza un duplicado, y
``alembic downgrade 0092_cortex_threads`` la elimina (reversible); (b) la capa
de persistencia: ``save_affect_snapshot`` → ``load_affect_state`` round-trip con
decay lazy aplicado en lectura; (c) **cross-owner**: un owner ajeno NUNCA lee el
snapshot de otro (filtro ``owner_user_id`` explícito; no hay RLS de respaldo).

Patrón de fixtures + admin sessionmaker BYPASSRLS tomado de
``test_cortex_threads.py``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

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
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


def _admin_sessionmaker(admin_database_url: str):
    import api_server.db.session as session_mod
    from api_server.config import get_settings

    get_settings.cache_clear()
    session_mod.reset_engine_cache()
    return session_mod.get_admin_sessionmaker()


async def _seed_two_owners(dsn: str) -> dict[str, UUID]:
    owner_id = uuid4()
    other_id = uuid4()
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE cortex_affect_snapshots, cortex_turns, cortex_conversations,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex Tenant",
            "cortex-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            owner_id,
            "owner@cortex.test",
            "h",
            other_id,
            "other@cortex.test",
            "h",
        )
    finally:
        await conn.close()
    return {"owner_id": owner_id, "other_id": other_id, "tenant_id": tenant_id}


# ---------------------------------------------------------------------------
# Migración 0093: tabla + índices + UNIQUE parcial + NO RLS + reversible
# ---------------------------------------------------------------------------
async def _table_exists(conn: asyncpg.Connection, name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = $1)",
            name,
        )
    )


async def _index_exists(conn: asyncpg.Connection, name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public'"
            " AND indexname = $1)",
            name,
        )
    )


@pytest.mark.asyncio
async def test_affect_snapshot_migration_indexes_no_rls_and_reversible(
    configured_app, migrations_pg_dsn: str, alembic_config
) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert await _table_exists(conn, "cortex_affect_snapshots")
        assert await _index_exists(conn, "ix_cortex_affect_snapshots_owner_created")
        assert await _index_exists(conn, "ix_cortex_affect_snapshots_owner_mood_label")

        # tenant-less BYPASSRLS: la tabla NO debe llevar RLS activada.
        relrowsecurity = await conn.fetchval(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'cortex_affect_snapshots'"
        )
        assert relrowsecurity is False

        # Sembramos una conversación + turno reales (el FK source_turn_id apunta
        # a cortex_turns.id).
        conv_id = uuid4()
        turn_id = uuid4()
        await conn.execute(
            "INSERT INTO cortex_conversations (id, owner_user_id, tenant_id)"
            " VALUES ($1, $2, $3)",
            conv_id,
            owner_id,
            seed["tenant_id"],
        )
        await conn.execute(
            "INSERT INTO cortex_turns (id, conversation_id, owner_user_id, role, content)"
            " VALUES ($1, $2, $3, 'cortex', 'hola')",
            turn_id,
            conv_id,
            owner_id,
        )

        # UNIQUE parcial por turno: re-insertar el MISMO source_turn_id viola.
        await conn.execute(
            "INSERT INTO cortex_affect_snapshots"
            " (id, owner_user_id, valence, arousal, dominance, intensity,"
            "  mood_valence, mood_arousal, mood_dominance, mood_label, source_turn_id)"
            " VALUES ($1,$2,0,0.3,0,0,0,0.3,0,'neutral',$3)",
            uuid4(),
            owner_id,
            turn_id,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO cortex_affect_snapshots"
                " (id, owner_user_id, valence, arousal, dominance, intensity,"
                "  mood_valence, mood_arousal, mood_dominance, mood_label, source_turn_id)"
                " VALUES ($1,$2,0,0.3,0,0,0,0.3,0,'neutral',$3)",
                uuid4(),
                owner_id,
                turn_id,
            )
        # Pero DOS snapshots con source_turn_id NULL conviven (parcial).
        for _ in range(2):
            await conn.execute(
                "INSERT INTO cortex_affect_snapshots"
                " (id, owner_user_id, valence, arousal, dominance, intensity,"
                "  mood_valence, mood_arousal, mood_dominance, mood_label)"
                " VALUES ($1,$2,0,0.3,0,0,0,0.3,0,'neutral')",
                uuid4(),
                owner_id,
            )
    finally:
        await conn.close()

    # downgrade -1 elimina la tabla (env.py corre asyncio.run → hilo aparte).
    await asyncio.to_thread(command.downgrade, alembic_config, "0092_cortex_threads")
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert not await _table_exists(conn, "cortex_affect_snapshots")
    finally:
        await conn.close()
    # Re-upgrade para no dejar la DB de sesión a medias para tests posteriores.
    await asyncio.to_thread(command.upgrade, alembic_config, "head")


# ---------------------------------------------------------------------------
# Persistencia: save → load round-trip + decay lazy en lectura + cross-owner
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_save_and_load_affect_state_owner_scoped(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from api_server.cortex.affect_store import load_affect_state, save_affect_snapshot
    from api_server.cortex.affective import AffectState, Drives, PADState, neutral_affect_state

    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    other_id = seed["other_id"]

    sessionmaker = _admin_sessionmaker(admin_database_url)

    # Sin snapshot → baseline neutro.
    async with sessionmaker() as session:
        loaded = await load_affect_state(session, owner_id, now=datetime.now(UTC))
        assert loaded == neutral_affect_state()

    state = AffectState(
        emotion=PADState(valence=0.8, arousal=0.9, dominance=0.5, intensity=0.7),
        mood=PADState(valence=0.4, arousal=0.5, dominance=0.2, intensity=0.0),
        drives=Drives(curiosity=0.7, bonding=0.6, coherence=0.5, competence=0.8),
    )
    written_at = datetime.now(UTC)
    async with sessionmaker() as session:
        snap = await save_affect_snapshot(
            session,
            owner_user_id=owner_id,
            state=state,
            appraisal_reason="elogio del owner",
        )
        await session.commit()
        assert snap.owner_user_id == owner_id
        assert snap.mood_label  # etiqueta derivada persistida

    # load SIN tiempo transcurrido (now == written_at): igual al guardado.
    async with sessionmaker() as session:
        same = await load_affect_state(session, owner_id, now=written_at)
        assert same.emotion.valence == pytest.approx(state.emotion.valence)
        assert same.mood.valence == pytest.approx(state.mood.valence)
        assert same.drives.competence == pytest.approx(0.8)

    # load CON tiempo transcurrido: la emoción decae hacia el baseline (lazy).
    async with sessionmaker() as session:
        decayed = await load_affect_state(session, owner_id, now=written_at + timedelta(hours=100))
        # La emoción se acercó al baseline (valence baja desde 0.8 hacia 0.0).
        assert decayed.emotion.valence < state.emotion.valence
        assert decayed.emotion.valence == pytest.approx(0.0, abs=1e-2)
        # El mood NO decae (capa lenta): se conserva tal cual se guardó.
        assert decayed.mood.valence == pytest.approx(state.mood.valence)
        # Los drives sí decaen hacia 0.
        assert decayed.drives.competence < 0.8

    # --- cross-owner: el otro owner NO ve el snapshot del primero ---
    async with sessionmaker() as session:
        other = await load_affect_state(session, other_id, now=written_at)
        assert other == neutral_affect_state()


@pytest.mark.asyncio
async def test_load_returns_latest_snapshot(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from api_server.cortex.affect_store import load_affect_state, save_affect_snapshot
    from api_server.cortex.affective import AffectState, Drives, PADState

    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    sessionmaker = _admin_sessionmaker(admin_database_url)

    now = datetime.now(UTC)
    async with sessionmaker() as session:
        await save_affect_snapshot(
            session,
            owner_user_id=owner_id,
            state=AffectState(
                emotion=PADState(valence=-0.5, arousal=0.4, dominance=-0.2),
                mood=PADState(valence=-0.2, arousal=0.4, dominance=0.0),
                drives=Drives(curiosity=0.3, bonding=0.3, coherence=0.3, competence=0.3),
            ),
        )
        await session.commit()
    # El segundo snapshot debe ganar (created_at más reciente).
    async with sessionmaker() as session:
        await save_affect_snapshot(
            session,
            owner_user_id=owner_id,
            state=AffectState(
                emotion=PADState(valence=0.9, arousal=0.8, dominance=0.6),
                mood=PADState(valence=0.5, arousal=0.6, dominance=0.3),
                drives=Drives(curiosity=0.9, bonding=0.9, coherence=0.9, competence=0.9),
            ),
        )
        await session.commit()

    async with sessionmaker() as session:
        latest = await load_affect_state(session, owner_id, now=now)
        assert latest.emotion.valence == pytest.approx(0.9)
        assert latest.drives.curiosity == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Baseline evolutivo: el decay converge al mood_baseline de la IDENTIDAD
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_decay_converges_to_identity_baseline(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from api_server.cortex.affect_store import load_affect_state, save_affect_snapshot
    from api_server.cortex.affective import BASELINE_PAD, AffectState, Drives, PADState
    from api_server.cortex.identity import ensure_identity, update_identity

    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    other_id = seed["other_id"]
    sessionmaker = _admin_sessionmaker(admin_database_url)

    # El owner tiene un baseline EVOLUTIVO calibrado por la reflexión.
    async with sessionmaker() as session:
        identity = await ensure_identity(session, owner_id)
        new_state = dict(identity.identity_state)
        new_state["mood_baseline"] = {"valence": 0.3, "arousal": 0.45, "dominance": 0.1}
        await update_identity(session, owner_id, new_state=new_state, reason="calibración test")
        await session.commit()

    extreme = AffectState(
        emotion=PADState(valence=-0.8, arousal=0.95, dominance=-0.6, intensity=0.9),
        mood=PADState(valence=-0.3, arousal=0.5, dominance=0.0),
        drives=Drives(curiosity=0.5, bonding=0.5, coherence=0.5, competence=0.5),
    )
    written_at = datetime.now(UTC)
    async with sessionmaker() as session:
        await save_affect_snapshot(session, owner_user_id=owner_id, state=extreme)
        await save_affect_snapshot(session, owner_user_id=other_id, state=extreme)
        await session.commit()

    # El decay lazy converge al baseline de la identidad, no al neutro del motor.
    async with sessionmaker() as session:
        decayed = await load_affect_state(session, owner_id, now=written_at + timedelta(hours=100))
        assert decayed.emotion.valence == pytest.approx(0.3, abs=1e-2)
        assert decayed.emotion.arousal == pytest.approx(0.45, abs=1e-2)
        assert decayed.emotion.dominance == pytest.approx(0.1, abs=1e-2)

    # Cross-owner: el baseline calibrado de A JAMÁS tiñe el decay de B (sin
    # identidad propia, B converge al neutro del motor BASELINE_PAD).
    async with sessionmaker() as session:
        other = await load_affect_state(session, other_id, now=written_at + timedelta(hours=100))
        assert other.emotion.valence == pytest.approx(BASELINE_PAD.valence, abs=1e-2)
        assert other.emotion.arousal == pytest.approx(BASELINE_PAD.arousal, abs=1e-2)
        assert other.emotion.dominance == pytest.approx(BASELINE_PAD.dominance, abs=1e-2)
