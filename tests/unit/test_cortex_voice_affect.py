"""Córtex F5 — mapeo puro afecto → prosodia de voz (ADR 0073/0075).

``voice_params_from_affect`` traduce el estado afectivo PAD del córtex (la
simulación determinista de F2) en los parámetros de síntesis Kokoro de un turno:
la **velocidad** del habla (modulada por ``arousal``, con ``valence`` como matiz
secundario) y la **voz** elegida (passthrough — la autoridad del allowlist vive
en el WS, no aquí). Es PURO: sin I/O, sin LLM, 100% determinista y testeable,
de modo que el WS de voz sólo lo invoca y reenvía el ``speed`` a la TTS.

> Honestidad (ADR 0075 §6): el afecto es un modelo computacional determinista,
> NO sentimientos reales; aquí sólo modula prosodia de forma auditable.
"""

from __future__ import annotations

import pytest
from api_server.cortex.affective import AffectState, Drives, PADState, neutral_affect_state
from api_server.cortex.voice_affect import (
    SPEED_MAX,
    SPEED_MIN,
    voice_params_from_affect,
)


def _affect(*, arousal: float, valence: float = 0.0) -> AffectState:
    """Un AffectState con la emoción fijada al PAD pedido (mood/drives neutros)."""
    return AffectState(
        emotion=PADState(valence=valence, arousal=arousal, dominance=0.0, intensity=0.0),
        mood=PADState(valence=0.0, arousal=0.3, dominance=0.0, intensity=0.0),
        drives=Drives(curiosity=0.5, bonding=0.5, coherence=0.5, competence=0.5),
    )


def test_arousal_zero_maps_to_floor_speed() -> None:
    params = voice_params_from_affect(_affect(arousal=0.0), voice="ef_dora")
    assert params["speed"] == pytest.approx(SPEED_MIN)


def test_arousal_one_maps_to_ceiling_speed() -> None:
    params = voice_params_from_affect(_affect(arousal=1.0), voice="ef_dora")
    assert params["speed"] == pytest.approx(SPEED_MAX)


def test_arousal_midpoint_is_deterministic_centre() -> None:
    # speed = 0.85 + 0.40*arousal at valence 0 → 0.5 → 1.05 (centre of the band).
    params = voice_params_from_affect(_affect(arousal=0.5), voice="ef_dora")
    assert params["speed"] == pytest.approx(1.05)


def test_speed_is_monotonic_in_arousal() -> None:
    speeds = [
        voice_params_from_affect(_affect(arousal=a), voice="ef_dora")["speed"]
        for a in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    assert speeds == sorted(speeds)
    assert len(set(speeds)) == len(speeds)  # strictly increasing


def test_out_of_range_arousal_is_clamped() -> None:
    # PADState already clamps arousal to [0,1]; the mapping must still stay in band
    # even if a raw value slipped through, so assert the band holds at the extremes.
    low = voice_params_from_affect(_affect(arousal=-5.0), voice="ef_dora")
    high = voice_params_from_affect(_affect(arousal=5.0), voice="ef_dora")
    assert SPEED_MIN <= low["speed"] <= SPEED_MAX
    assert SPEED_MIN <= high["speed"] <= SPEED_MAX
    assert low["speed"] == pytest.approx(SPEED_MIN)
    assert high["speed"] == pytest.approx(SPEED_MAX)


def test_valence_is_a_secondary_modifier_within_band() -> None:
    # Same arousal, opposite valence: a positive valence nudges speed up a touch,
    # a negative one nudges it down — but never outside the clamp band.
    base = voice_params_from_affect(_affect(arousal=0.5, valence=0.0), voice="ef_dora")
    happy = voice_params_from_affect(_affect(arousal=0.5, valence=0.8), voice="ef_dora")
    sad = voice_params_from_affect(_affect(arousal=0.5, valence=-0.8), voice="ef_dora")
    assert sad["speed"] <= base["speed"] <= happy["speed"]
    for p in (base, happy, sad):
        assert SPEED_MIN <= p["speed"] <= SPEED_MAX


def test_voice_is_passed_through_unchanged() -> None:
    params = voice_params_from_affect(_affect(arousal=0.5), voice="am_michael")
    assert params["voice"] == "am_michael"


def test_neutral_state_speaks_near_baseline() -> None:
    # The neutral baseline arousal is 0.3 → a calm, slightly-below-centre pace.
    params = voice_params_from_affect(neutral_affect_state(), voice="ef_dora")
    assert SPEED_MIN < params["speed"] < 1.05
    assert params["voice"] == "ef_dora"


def test_is_deterministic() -> None:
    a = _affect(arousal=0.42, valence=0.3)
    first = voice_params_from_affect(a, voice="bf_emma")
    second = voice_params_from_affect(a, voice="bf_emma")
    assert first == second
