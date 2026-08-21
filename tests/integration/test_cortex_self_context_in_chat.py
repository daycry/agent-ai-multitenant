"""Córtex — self-context unificado cableado en el hot-path del turno (identidad real).

Comprueba que ``POST /owner/cortex/turns`` compone el prompt vía el self-context
ÚNICO (identidad + afecto vivo + recall + relationship_model) y que el afecto
modula el ``reasoning_effort`` de forma acotada y auditable:

  * el prompt capturado contiene narrativa (DATOS), "lo que sé de mi owner"
    (DATOS), guía de tono (fuera de DATOS) y el recuerdo recallado — y el recall
    corre EXACTAMENTE una vez (el router ya no lo llama por su cuenta);
  * el turno persiste ``reasoning_effort`` EFECTIVO + ``metadata.self_context``
    (mood, effort base/efectivo/razones) — antes siempre quedaba NULL;
  * cross-owner: el self-context de A jamás lee la identidad/afecto de B.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
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
        # `platform_settings` entra en el TRUNCATE a propósito: los gates del
        # córtex (`cortex.web_enabled`) son globales, así que un test que los
        # encienda dejaría el siguiente corriendo con la web puesta. La caché
        # Redis de esas claves la limpia el `_flush_redis` del fixture.
        await conn.execute(
            "TRUNCATE platform_settings, memory_entries, cortex_identity_history,"
            " cortex_identity, cortex_affect_snapshots, cortex_turns, cortex_conversations,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex SelfCtx Tenant",
            "cortex-selfctx-tenant",
        )
        # System Owner Y System Admin: son dos columnas distintas y el dueño del
        # despliegue es las dos cosas (ADR 0074). Sin `is_system_admin`,
        # `set_platform_setting` —el camino por el que el panel flipa los gates—
        # rechazaría al owner con un 403.
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner, is_system_admin)"
            " VALUES ($1, $2, $3, true, true)",
            owner_id,
            "owner@selfctx.test",
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
    return {"owner_id": owner_id, "tenant_id": tenant_id}


async def _seed_identity(dsn: str, owner_id: UUID, identity_state: dict) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO cortex_identity (id, owner_user_id, identity_state, version,"
            " updated_by, created_at, updated_at)"
            " VALUES ($1, $2, $3::jsonb, 1, 'reflection', now(), now())",
            uuid4(),
            owner_id,
            json.dumps(identity_state),
        )
    finally:
        await conn.close()


async def _seed_live_affect(
    test_redis_url: str, owner_id: UUID, *, valence: float, arousal: float, intensity: float
) -> None:
    from api_server.cortex.affect_cache import write_affect_state
    from api_server.cortex.affective import AffectState, Drives, PADState
    from redis.asyncio import Redis

    client = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        state = AffectState(
            emotion=PADState(valence=valence, arousal=arousal, dominance=0.0, intensity=intensity),
            mood=PADState(valence=valence, arousal=arousal, dominance=0.0),
            drives=Drives(curiosity=0.8, bonding=0.5, coherence=0.5, competence=0.5),
        )
        await write_affect_state(client, str(owner_id), state, now=datetime.now(UTC))
    finally:
        await client.aclose()


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_owner=True)


class _CapturingModel:
    """Records the system prompt it was handed, then answers (no tools).

    Captura también ``enabled_tools``: el prompt y el catálogo son las dos
    mitades de la misma affordance, y un test que sólo mire el texto no
    distingue «te anuncio la web y la tienes» de «te la anuncio y no la tienes».
    """

    def __init__(self) -> None:
        self.system_prompt: str | None = None
        self.enabled_tools: tuple[str, ...] = ()

    async def decide(self, state):
        from api_server.assistant.graph import ModelTurn

        self.system_prompt = state.system_prompt
        self.enabled_tools = tuple(state.enabled_tools)
        return ModelTurn(content="entendido")


class _CapturingModelWithEffort(_CapturingModel):
    """Doble con los metadatos de resolución que el córtex estampa (ADR 0070)."""

    provider_kind = "claude_sdk"
    reasoning_effort = "high"


# ---------------------------------------------------------------------------
# El prompt del turno = self-context unificado (y el recall corre UNA vez)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_self_context_unifica_identidad_afecto_y_recall(
    configured_app, migrations_pg_dsn: str, test_redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = await _seed_owner(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    tenant_id = seed["tenant_id"]

    await _seed_identity(
        migrations_pg_dsn,
        owner_id,
        {
            "name": "Lumen",
            "core_values": ["honestidad"],
            "narrative": "He aprendido que el owner construye una plataforma multi-tenant.",
            "relationship_model": {"prefiere": "evidencia primero"},
            "language": "es",
        },
    )
    await _seed_live_affect(test_redis_url, owner_id, valence=0.5, arousal=0.45, intensity=0.3)

    import api_server.db.session as session_mod
    from api_server.config import get_settings
    from api_server.cortex.memory import cortex_remember
    from api_server.routers.cortex import get_cortex_model

    get_settings.cache_clear()
    session_mod.reset_engine_cache()
    async with session_mod.get_admin_sessionmaker()() as session:
        await cortex_remember(
            session,
            owner_user_id=owner_id,
            tenant_id=tenant_id,
            content="Al owner le interesa la arquitectura hexagonal",
        )
        await session.commit()

    # Contador de invocaciones del recall (vía el seam del self-context).
    import api_server.cortex.self_context as self_ctx_mod

    real_recall = self_ctx_mod.cortex_recall
    calls: list[str] = []

    async def counting_recall(*args, **kwargs):
        calls.append(kwargs.get("query", ""))
        return await real_recall(*args, **kwargs)

    monkeypatch.setattr(self_ctx_mod, "cortex_recall", counting_recall)

    captured = _CapturingModel()
    configured_app.dependency_overrides[get_cortex_model] = lambda: captured
    token = await _mint(owner_id, tenant_id)

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/owner/cortex/turns",
            json={"message": "cuéntame sobre arquitectura hexagonal"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text

    prompt = captured.system_prompt
    assert prompt is not None
    # Identidad (DATOS): nombre + narrativa.
    assert "Lumen" in prompt
    assert "plataforma multi-tenant" in prompt
    # Lo que sé de mi owner (DATOS).
    assert "evidencia primero" in prompt
    # Guía de tono derivada del afecto vivo (fuera de DATOS, copy honesto).
    assert "cálido" in prompt
    assert "simulado" in prompt
    # Recall presente…
    assert "Al owner le interesa la arquitectura hexagonal" in prompt
    # …y corre EXACTAMENTE una vez (el router ya no lo llama por su cuenta).
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# El afecto modula el effort (acotado) y el turno lo AUDITA en metadata
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_turno_persiste_effort_efectivo_y_metadata_self_context(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    seed = await _seed_owner(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    tenant_id = seed["tenant_id"]

    # Afecto vivo con evento fuerte y reciente ⇒ el effort sube un paso.
    await _seed_live_affect(test_redis_url, owner_id, valence=0.2, arousal=0.9, intensity=0.5)

    from api_server.routers.cortex import get_cortex_model

    captured = _CapturingModelWithEffort()
    configured_app.dependency_overrides[get_cortex_model] = lambda: captured
    token = await _mint(owner_id, tenant_id)

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/owner/cortex/turns",
            json={"message": "qué opinas de este diseño"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reasoning_effort"] == "xhigh"

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT reasoning_effort, metadata FROM cortex_turns"
            " WHERE owner_user_id = $1 AND role = 'cortex'"
            " ORDER BY created_at DESC LIMIT 1",
            owner_id,
        )
    finally:
        await conn.close()
    assert row is not None
    # El effort EFECTIVO (modulado) queda persistido — antes siempre era NULL.
    assert row["reasoning_effort"] == "xhigh"
    meta = json.loads(row["metadata"])
    self_ctx = meta["self_context"]
    assert self_ctx["effort_base"] == "high"
    assert self_ctx["effort_effective"] == "xhigh"
    assert any(r.startswith("arousal_high") for r in self_ctx["effort_reasons"])
    assert "mood_label" in self_ctx
    assert self_ctx["valence"] == pytest.approx(0.2, abs=0.05)


# ---------------------------------------------------------------------------
# Cross-owner: el self-context de A jamás lee identidad/afecto de B
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_self_context_cross_owner_aislado(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    seed = await _seed_owner(migrations_pg_dsn)
    owner_a = seed["owner_id"]
    tenant_id = seed["tenant_id"]

    # Owner B con una identidad DISTINTIVA.
    owner_b = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'h')",
            owner_b,
            "otro@selfctx.test",
        )
    finally:
        await conn.close()
    await _seed_identity(
        migrations_pg_dsn,
        owner_b,
        {"name": "Umbra", "narrative": "Narrativa secreta del owner B.", "language": "es"},
    )

    import api_server.db.session as session_mod
    from api_server.config import get_settings
    from api_server.cortex.self_context import load_self_context

    get_settings.cache_clear()
    session_mod.reset_engine_cache()
    async with session_mod.get_admin_sessionmaker()() as session:
        ctx = await load_self_context(
            session,
            None,
            owner_user_id=owner_a,
            tenant_id=tenant_id,
            query="hola",
            now=datetime.now(UTC),
        )
    # A recibe SU identidad (default recién creada), nunca la de B.
    assert ctx.identity_state.get("name") != "Umbra"
    assert "secreta" not in json.dumps(ctx.identity_state)


# ---------------------------------------------------------------------------
# Affordance de la web: si está habilitada, el prompt LO DICE (el modelo no
# puede usar lo que no sabe que tiene — reporte del operador 2026-07-06)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_prompt_anuncia_web_solo_cuando_esta_habilitada(
    configured_app, migrations_pg_dsn: str
) -> None:
    """El anuncio de la web sigue al INTERRUPTOR, y en los dos sentidos.

    El interruptor se flipa por donde lo flipa el owner —``PUT /owner/cortex/
    autonomy``, que es lo que llama el panel— y no escribiendo la fila de
    ``platform_settings`` a pelo, como hacía este test. La diferencia no es de
    estilo: desde ``prod-13 task_prod13_21`` las lecturas de settings pasan por
    una caché Redis de 30 s cuya frescura la garantiza la invalidación que hace
    ``set_platform_setting``. Un ``INSERT`` directo se salta esa invalidación,
    así que el turno con la web apagada dejaba cacheada la AUSENCIA de la fila y
    el turno siguiente seguía leyendo OFF: el verde dependía de que entre los dos
    turnos pasaran más de 30 s de reloj. En local pasaban (el enqueue del afecto
    reintenta contra un broker que no está); en CI no, y por eso allí salió rojo.

    Ir por el endpoint no relaja nada: añade cobertura. Si alguien rompe la
    invalidación de la caché, el turno posterior al PUT sirve el valor rancio y
    esto se pone rojo — con el ``INSERT`` a pelo esa regresión era invisible. Y
    la mitad negativa se refuerza: el anuncio no sólo tiene que faltar antes de
    encender la web, tiene que DESAPARECER al apagarla, que es la dirección del
    kill-switch que importa (ADR 0067, deny-by-default).
    """
    seed = await _seed_owner(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    tenant_id = seed["tenant_id"]

    from api_server.routers.cortex import get_cortex_model

    captured = _CapturingModel()
    configured_app.dependency_overrides[get_cortex_model] = lambda: captured
    token = await _mint(owner_id, tenant_id)
    headers = {"Authorization": f"Bearer {token}"}

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Precondición COMPROBADA, no supuesta: el gate arranca apagado.
        snapshot = await client.get("/owner/cortex/autonomy", headers=headers)
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.json()["web_enabled"] is False

        # Web OFF: el prompt no promete web — ni buscar ni leer una URL.
        resp = await client.post("/owner/cortex/turns", json={"message": "hola"}, headers=headers)
        assert resp.status_code == 200, resp.text
        prompt_off = captured.system_prompt or ""
        assert "web_search" not in prompt_off
        assert "web_fetch" not in prompt_off
        # …y el catálogo tampoco las lleva: anuncio y affordance van juntos.
        assert "web_search" not in captured.enabled_tools
        assert "web_fetch" not in captured.enabled_tools
        # El catálogo no está vacío: sin esto, un turno que corriera SIN tools
        # (el camino del onboarding, que es cero-tools a propósito) pasaría estas
        # dos negativas por la razón equivocada.
        assert "cortex_remember" in captured.enabled_tools

        # El owner enciende la web por donde la enciende el panel.
        resp = await client.put(
            "/owner/cortex/autonomy", json={"web_enabled": True}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["web_enabled"] is True

        # Web ON: el prompt anuncia web_search/web_fetch (affordance explícita).
        resp = await client.post(
            "/owner/cortex/turns", json={"message": "hola de nuevo"}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        prompt_on = captured.system_prompt or ""
        assert "web_search" in prompt_on
        assert "web_fetch" in prompt_on
        # Y lo anunciado existe de verdad en el catálogo del turno.
        assert "web_search" in captured.enabled_tools
        assert "web_fetch" in captured.enabled_tools

        # Y al apagarla, el anuncio se retira del turno siguiente: el prompt no
        # puede quedarse prometiendo una capacidad que el gate ya no concede.
        resp = await client.put(
            "/owner/cortex/autonomy", json={"web_enabled": False}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["web_enabled"] is False

        resp = await client.post(
            "/owner/cortex/turns", json={"message": "y ahora"}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        prompt_off_again = captured.system_prompt or ""
        assert "web_search" not in prompt_off_again
        assert "web_fetch" not in prompt_off_again
        # El kill-switch retira la capacidad, no sólo su anuncio.
        assert "web_search" not in captured.enabled_tools
        assert "web_fetch" not in captured.enabled_tools
        assert "cortex_remember" in captured.enabled_tools
