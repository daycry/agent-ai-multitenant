"""Córtex F2 — caché Redis del estado afectivo vivo + stream de telemetría.

Ejercita contra el Redis de test (DB 15):

  * la caché ``cortex:affect:{owner}``: round-trip write→read, decay lazy en
    lectura (la emoción decae hacia el baseline, el mood no), TTL puesto,
    ausencia ⇒ ``None``, y **aislamiento por-owner** (un owner no lee la clave
    de otro);
  * el stream de telemetría ``cortex:telemetry:{owner}``:
    ``publish_cortex_affect_event`` hace ``xadd`` con el frame ``type:'affect'``
    + ``payload`` JSON, y ``delete_cortex_affect_stream`` lo limpia.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture()
async def redis_client(test_redis_url: str):
    client: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


# ---------------------------------------------------------------------------
# Caché viva: round-trip + decay lazy + TTL + ausencia + cross-owner
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_write_read_roundtrip_and_decay(redis_client: Redis) -> None:
    from api_server.cortex.affect_cache import (
        AFFECT_CACHE_TTL_S,
        affect_cache_key,
        read_affect_state,
        write_affect_state,
    )
    from api_server.cortex.affective import AffectState, Drives, PADState

    owner = str(uuid4())
    state = AffectState(
        emotion=PADState(valence=0.8, arousal=0.9, dominance=0.5, intensity=0.7),
        mood=PADState(valence=0.4, arousal=0.5, dominance=0.2, intensity=0.0),
        drives=Drives(curiosity=0.7, bonding=0.6, coherence=0.5, competence=0.8),
    )
    now = datetime.now(UTC)
    await write_affect_state(redis_client, owner, state, now=now)

    # TTL puesto (≈ AFFECT_CACHE_TTL_S).
    ttl = await redis_client.ttl(affect_cache_key(owner))
    assert 0 < ttl <= AFFECT_CACHE_TTL_S

    # Lectura sin tiempo transcurrido: igual a lo escrito.
    same = await read_affect_state(redis_client, owner, now=now)
    assert same is not None
    assert same.emotion.valence == pytest.approx(0.8)
    assert same.mood.valence == pytest.approx(0.4)
    assert same.drives.competence == pytest.approx(0.8)

    # Lectura con tiempo transcurrido: emoción decae hacia el baseline (lazy),
    # el mood NO decae, los drives sí.
    decayed = await read_affect_state(redis_client, owner, now=now + timedelta(hours=100))
    assert decayed is not None
    assert decayed.emotion.valence < state.emotion.valence
    assert decayed.emotion.valence == pytest.approx(0.0, abs=1e-2)
    assert decayed.mood.valence == pytest.approx(0.4)
    assert decayed.drives.competence < 0.8


@pytest.mark.asyncio
async def test_read_missing_returns_none(redis_client: Redis) -> None:
    from api_server.cortex.affect_cache import read_affect_state

    got = await read_affect_state(redis_client, str(uuid4()), now=datetime.now(UTC))
    assert got is None


@pytest.mark.asyncio
async def test_cache_is_owner_scoped(redis_client: Redis) -> None:
    from api_server.cortex.affect_cache import read_affect_state, write_affect_state
    from api_server.cortex.affective import AffectState, Drives, PADState

    owner_a = str(uuid4())
    owner_b = str(uuid4())
    now = datetime.now(UTC)
    await write_affect_state(
        redis_client,
        owner_a,
        AffectState(
            emotion=PADState(valence=0.9, arousal=0.5, dominance=0.0),
            mood=PADState(valence=0.5, arousal=0.5, dominance=0.0),
            drives=Drives(curiosity=0.9, bonding=0.9, coherence=0.9, competence=0.9),
        ),
        now=now,
    )
    # B nunca ve el estado de A (clave-por-owner).
    assert await read_affect_state(redis_client, owner_b, now=now) is None


@pytest.mark.asyncio
async def test_delete_affect_state(redis_client: Redis) -> None:
    from api_server.cortex.affect_cache import (
        delete_affect_state,
        read_affect_state,
        write_affect_state,
    )
    from api_server.cortex.affective import neutral_affect_state

    owner = str(uuid4())
    now = datetime.now(UTC)
    await write_affect_state(redis_client, owner, neutral_affect_state(), now=now)
    assert await read_affect_state(redis_client, owner, now=now) is not None
    await delete_affect_state(redis_client, owner)
    assert await read_affect_state(redis_client, owner, now=now) is None


# ---------------------------------------------------------------------------
# Stream de telemetría: publish (xadd) + delete
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_publish_and_read_telemetry_frame(redis_client: Redis) -> None:
    from api_server.events import (
        cortex_telemetry_stream_key,
        delete_cortex_affect_stream,
        publish_cortex_affect_event,
    )

    owner = str(uuid4())
    payload = {
        "valence": 0.5,
        "arousal": 0.4,
        "dominance": 0.1,
        "intensity": 0.6,
        "mood_label": "calma",
        "drives": {"curiosity": 0.5, "bonding": 0.5, "coherence": 0.5, "competence": 0.5},
        "appraisal_reason": "elogio del owner",
    }
    await publish_cortex_affect_event(redis_client, owner, payload=payload)

    entries = await redis_client.xrange(cortex_telemetry_stream_key(owner))
    assert len(entries) == 1
    _entry_id, fields = entries[0]
    assert fields["type"] == "affect"
    assert "occurred_at" in fields
    import json

    assert json.loads(fields["payload"])["mood_label"] == "calma"

    # delete limpia el stream.
    await delete_cortex_affect_stream(redis_client, owner)
    assert await redis_client.xrange(cortex_telemetry_stream_key(owner)) == []
