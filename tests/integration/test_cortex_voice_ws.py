"""Córtex F5 — WS de voz ``/ws/owner/cortex/voice`` (ADR 0073 voz + 0075 afecto).

Clona el protocolo del WS de voz del asistente cambiando el gate
(``require_system_owner`` DB-authoritative), el cerebro (el córtex) y añadiendo el
frame afectivo ``{type:'affect'}`` que pinta el avatar. STT/TTS/cerebro van
FAKEADOS vía override de dependencias — ningún servicio real se contacta.

  * happy path (owner): conecta → ``ready`` → envía audio + ``eot`` → recibe
    ``transcript`` (del STT fake), ``answer`` (del córtex fake), ``affect`` (las
    5 claves PAD + drives), el binario de audio (del TTS fake) y ``turn_end``;
  * **cross-owner (regla dura BYPASSRLS)**: un NO-owner (aunque forje el claim
    ``own``) cierra con 1008 y NUNCA ejecuta turno;
  * voz no soportada en ``{type:'config'}`` se ignora (cae al allowlist).

Patrón TestClient + seed de ``test_cortex_telemetry_ws.py`` /
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


# ===========================================================================
# Fakes — STT / TTS / cerebro
# ===========================================================================
class _FakeSTT:
    """STT que devuelve un texto fijo y RECUERDA el content_type que recibió."""

    def __init__(self, text: str = "hola córtex") -> None:
        self.text = text
        self.seen_content_type: str | None = None

    async def transcribe(
        self, audio: bytes, *, content_type: str = "audio/wav", language: str | None = None
    ) -> str:
        self.seen_content_type = content_type
        return self.text


class _FakeTTS:
    """TTS que devuelve bytes fijos y RECUERDA voz + speed (afecto → prosodia)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def synthesize(
        self, text: str, *, voice: str, response_format: str = "mp3", speed: float = 1.0
    ) -> bytes:
        self.calls.append({"text": text, "voice": voice, "speed": speed})
        return b"FAKE-AUDIO"


def _scripted_answer(answer: str):
    from api_server.assistant.graph import ModelTurn, ScriptedAssistantModel

    return ScriptedAssistantModel(turns=[ModelTurn(content=answer)])


# ===========================================================================
# App + seed
# ===========================================================================
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
            "TRUNCATE cortex_turns, cortex_conversations, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex Voice Tenant",
            "cortex-voice-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, $3, $4)",
            owner_id,
            "owner@voice-cortex.test",
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


def _override_media(configured_app, stt: _FakeSTT, tts: _FakeTTS) -> None:
    from api_server.routers.cortex_voice import (
        get_cortex_stt,
        get_cortex_tts,
        get_cortex_voice_model,
    )

    configured_app.dependency_overrides[get_cortex_voice_model] = lambda: _scripted_answer(
        "Hola, soy tu córtex."
    )
    configured_app.dependency_overrides[get_cortex_stt] = lambda: stt
    configured_app.dependency_overrides[get_cortex_tts] = lambda: tts


# ===========================================================================
# Gate — cross-owner (regla dura BYPASSRLS)
# ===========================================================================
def test_ws_rejects_missing_token(ws_client) -> None:
    """Rechazo con DIAGNÓSTICO: un frame ``error`` con el motivo completo
    precede al cierre 1008 (antes un detail >123 bytes hacía fallar el close
    y el cliente veía un 1006 mudo — endurecimiento 2026-07-09)."""
    with (
        ws_client.websocket_connect("/ws/owner/cortex/voice") as ws,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        first = ws.receive_json()
        assert first["type"] == "error"
        assert "unauthenticated" in first["detail"]
        ws.receive_json()  # tras el frame de error solo queda el cierre
    assert exc.value.code == 1008


def test_ws_rejects_non_owner_even_with_forged_claim(
    ws_client, configured_app, migrations_pg_dsn: str
) -> None:
    """Un NO-owner (con claim ``own`` forjado) → frame error + cierre 1008 y
    NO ejecuta turno."""
    seed = asyncio.run(_seed(migrations_pg_dsn, owner_is_owner=False))
    stt, tts = _FakeSTT(), _FakeTTS()
    _override_media(configured_app, stt, tts)
    token = _mint(seed["owner_id"], seed["tenant_id"], owner_claim=True)

    with (
        ws_client.websocket_connect(f"/ws/owner/cortex/voice?token={token}") as ws,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        first = ws.receive_json()
        assert first["type"] == "error"
        assert "forbidden" in first["detail"]
        ws.receive_json()
    assert exc.value.code == 1008
    # El cerebro NUNCA se tocó (no hubo turno).
    assert tts.calls == []


# ===========================================================================
# Happy path — el owner completa un turno con frame afectivo
# ===========================================================================
def test_ws_owner_completes_voice_turn_with_affect_frame(
    ws_client, configured_app, migrations_pg_dsn: str
) -> None:
    seed = asyncio.run(_seed(migrations_pg_dsn, owner_is_owner=True))
    stt, tts = _FakeSTT(text="hola córtex"), _FakeTTS()
    _override_media(configured_app, stt, tts)
    token = _mint(seed["owner_id"], seed["tenant_id"], owner_claim=True)

    with ws_client.websocket_connect(f"/ws/owner/cortex/voice?token={token}") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["voice"]  # the default voice from settings

        # Send a webm utterance + the end-of-turn control frame.
        ws.send_bytes(b"webm-audio-bytes")
        ws.send_json({"type": "config", "audio_mime": "audio/webm"})
        # config replies with a ready ack (voice unchanged) — drain it.
        ack = ws.receive_json()
        assert ack["type"] == "ready"
        ws.send_bytes(b"more-webm")
        ws.send_json({"type": "eot"})

        transcript = ws.receive_json()
        assert transcript == {"type": "transcript", "text": "hola córtex"}
        # Frame `thinking` tras el STT y antes del cerebro (streaming: feedback
        # inmediato + tráfico intermedio que aleja el keepalive en turnos largos).
        assert ws.receive_json() == {"type": "thinking"}
        answer = ws.receive_json()
        assert answer == {"type": "answer", "text": "Hola, soy tu córtex."}
        affect = ws.receive_json()
        assert affect["type"] == "affect"
        for key in ("valence", "arousal", "dominance", "mood_label", "drives"):
            assert key in affect
        audio = ws.receive_bytes()
        assert audio == b"FAKE-AUDIO"
        end = ws.receive_json()
        assert end == {"type": "turn_end"}

    # The fake STT saw the propagated webm mime (shared content_type fix), not wav.
    assert stt.seen_content_type == "audio/webm"
    # The TTS got a speed derived from the affect (neutral baseline arousal 0.3 →
    # within the clamp band, never the forced 1.0 default).
    assert len(tts.calls) == 1
    assert 0.85 <= float(tts.calls[0]["speed"]) <= 1.25


def test_ws_unsupported_voice_is_ignored(ws_client, configured_app, migrations_pg_dsn: str) -> None:
    """Una voz fuera del allowlist en ``config`` se ignora (cae al default)."""
    seed = asyncio.run(_seed(migrations_pg_dsn, owner_is_owner=True))
    stt, tts = _FakeSTT(), _FakeTTS()
    _override_media(configured_app, stt, tts)
    token = _mint(seed["owner_id"], seed["tenant_id"], owner_claim=True)

    with ws_client.websocket_connect(f"/ws/owner/cortex/voice?token={token}") as ws:
        ready = ws.receive_json()
        default_voice = ready["voice"]
        ws.send_json({"type": "config", "voice": "evil_voice; rm -rf"})
        ack = ws.receive_json()
        assert ack["type"] == "ready"
        # The malicious voice never took effect — still the default.
        assert ack["voice"] == default_voice
