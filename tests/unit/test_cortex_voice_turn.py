"""Córtex F5 — adaptador de turno de voz: frame afectivo + lectura del afecto.

Las dos costuras testeables del WS de voz del córtex (``routers/cortex_voice.py``):

  * :func:`affect_frame` — builder PURO ``AffectState -> {type:'affect', valence,
    arousal, dominance, mood_label, drives}`` para el avatar del front.
  * :func:`load_current_affect` — lee el afecto vigente (caché Redis con decay
    lazy → BD), **fail-open** a baseline neutro si todo falla (Redis vacío, BD
    sin snapshot). El reloj entra como ``now`` (determinismo).

El pipeline completo del turno (STT→cerebro→persistencia→TTS) se ejercita en el
test de integración del WS con DB real; aquí sólo los seams puros/casi-puros.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from api_server.cortex.affective import AffectState, Drives, PADState, neutral_affect_state
from api_server.cortex.voice_turn import affect_frame, load_current_affect


def _state(*, valence: float, arousal: float, dominance: float) -> AffectState:
    return AffectState(
        emotion=PADState(valence=valence, arousal=arousal, dominance=dominance, intensity=0.4),
        mood=PADState(valence=valence, arousal=arousal, dominance=dominance, intensity=0.0),
        drives=Drives(curiosity=0.7, bonding=0.6, coherence=0.5, competence=0.4),
    )


# ---------------------------------------------------------------------------
# affect_frame — builder puro
# ---------------------------------------------------------------------------
def test_affect_frame_has_pad_mood_and_drives() -> None:
    frame = affect_frame(_state(valence=0.5, arousal=0.6, dominance=0.2))
    assert frame["type"] == "affect"
    assert frame["valence"] == pytest.approx(0.5)
    assert frame["arousal"] == pytest.approx(0.6)
    assert frame["dominance"] == pytest.approx(0.2)
    assert isinstance(frame["mood_label"], str) and frame["mood_label"]
    assert set(frame["drives"]) == {"curiosity", "bonding", "coherence", "competence"}
    assert frame["drives"]["curiosity"] == pytest.approx(0.7)


def test_affect_frame_mood_label_is_bilingual() -> None:
    happy = _state(valence=0.5, arousal=0.6, dominance=0.0)
    assert affect_frame(happy, language="es")["mood_label"] == "alegría"
    assert affect_frame(happy, language="en")["mood_label"] == "joy"


def test_affect_frame_neutral_baseline() -> None:
    frame = affect_frame(neutral_affect_state())
    assert frame["type"] == "affect"
    assert frame["mood_label"] == "neutral"


# ---------------------------------------------------------------------------
# load_current_affect — Redis → BD, fail-open a neutro
# ---------------------------------------------------------------------------
class _FakeRedis:
    """Redis-like con sólo ``get``: devuelve lo que se le inyecte (o None)."""

    def __init__(self, value: object = None) -> None:
        self._value = value

    async def get(self, _key: str) -> object:
        return self._value


@pytest.mark.asyncio
async def test_load_current_affect_reads_redis_first() -> None:
    """Con clave Redis viva, NO toca la BD (sessionmaker que lanzaría si se usa)."""
    import json

    from api_server.cortex.affect_cache import affect_cache_key  # noqa: F401

    now = datetime.now(UTC)
    payload = json.dumps(
        {
            "updated_at": now.isoformat(),
            "emotion": {"valence": 0.4, "arousal": 0.7, "dominance": 0.1, "intensity": 0.5},
            "mood": {"valence": 0.4, "arousal": 0.7, "dominance": 0.1},
            "drives": {"curiosity": 0.8, "bonding": 0.5, "coherence": 0.5, "competence": 0.5},
        }
    )

    def _boom_sessionmaker() -> object:  # pragma: no cover - must NOT be called
        raise AssertionError("DB must not be touched when Redis has the state")

    state = await load_current_affect(
        _FakeRedis(payload), _boom_sessionmaker, owner_user_id=uuid4(), now=now
    )
    assert state.emotion.arousal == pytest.approx(0.7)
    assert state.emotion.valence == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_load_current_affect_fail_open_to_neutral() -> None:
    """Redis vacío + BD que falla ⇒ baseline neutro (nunca lanza)."""

    class _BoomSession:
        async def __aenter__(self) -> object:
            raise RuntimeError("db down")

        async def __aexit__(self, *a: object) -> bool:  # pragma: no cover
            return False

    def _failing_sessionmaker() -> _BoomSession:
        return _BoomSession()

    state = await load_current_affect(
        _FakeRedis(None), _failing_sessionmaker, owner_user_id=uuid4(), now=datetime.now(UTC)
    )
    # Baseline neutro (lo que devuelve neutral_affect_state): arousal 0.3, valence 0.
    assert state.emotion.valence == pytest.approx(0.0)
    assert state.emotion.arousal == pytest.approx(0.3)
