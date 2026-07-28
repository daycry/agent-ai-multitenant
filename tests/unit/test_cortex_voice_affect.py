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

from itertools import pairwise

import pytest
from api_server.cortex.affective import AffectState, Drives, PADState, neutral_affect_state
from api_server.cortex.voice_affect import (
    SPEED_MAX,
    SPEED_MIN,
    arousal_to_speed,
    voice_params_from_affect,
)

pytestmark = pytest.mark.unit


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


# ---------------------------------------------------------------------------
# `arousal_to_speed` — la función PURA, invocada directamente (criterio A2:
# «función pura cubierta al 100%, monótona y clampeada»).
#
# Los tests de arriba entran por `voice_params_from_affect`, es decir con un
# `PADState` de por medio, y ese dataclass YA recorta valence/arousal a su rango
# en `__post_init__` (cortex/affective.py). Consecuencia medida (auditoría
# 2026-07-27): las dos ramas del clamp de este módulo NUNCA se ejecutaban en la
# suite — cobertura 85.7%, líneas 57 y 59 sin tocar — pese a ser la guarda que
# impide que la voz salga catatónica o atropellada. Estos tests llaman al mapeo
# SIN intermediario, que es donde el criterio del plan se puede comprobar.
# ---------------------------------------------------------------------------
def test_arousal_to_speed_ancla_la_banda_sin_pasar_por_padstate() -> None:
    """La firma real (`arousal`, kwarg `valence`) mapea la banda con valence neutro.

    No es un duplicado de los tests de `voice_params_from_affect`: aquellos
    verifican el cableado afecto→params; éste fija el contrato de la función pura
    que el WS reutiliza, que hasta ahora sólo se ejercitaba de rebote.
    """
    assert arousal_to_speed(0.0) == pytest.approx(SPEED_MIN)
    assert arousal_to_speed(1.0) == pytest.approx(SPEED_MAX)
    assert arousal_to_speed(0.5) == pytest.approx(1.05)


def test_clamp_inferior_lo_dispara_el_valence_negativo() -> None:
    """El suelo del clamp es alcanzable EN PRODUCCIÓN, no sólo con basura de test.

    Con `arousal=0` (mínimo legal de PADState) y `valence=-1` (mínimo legal), el
    valor crudo es 0.85 - 0.05 = 0.80, por debajo de la banda: sin el recorte, el
    córtex triste hablaría más lento de lo que Kokoro suena natural. Es la rama
    `if x < lo` que la suite no ejecutaba nunca.
    """
    raw = 0.85 + 0.40 * 0.0 + 0.05 * -1.0
    assert raw < SPEED_MIN  # el crudo se sale por abajo: hay algo que recortar
    assert arousal_to_speed(0.0, valence=-1.0) == pytest.approx(SPEED_MIN)


def test_clamp_superior_lo_dispara_el_valence_positivo() -> None:
    """Idem por arriba: `arousal=1` + `valence=1` da 1.30 crudo, fuera de banda.

    Cubre la rama `if x > hi`. Sin ella, un córtex eufórico le pediría a la TTS
    una velocidad que ya suena atropellada.
    """
    raw = 0.85 + 0.40 * 1.0 + 0.05 * 1.0
    assert raw > SPEED_MAX  # el crudo se sale por arriba
    assert arousal_to_speed(1.0, valence=1.0) == pytest.approx(SPEED_MAX)


def test_arousal_fuera_de_rango_se_clampa_en_el_propio_mapeo() -> None:
    """Un arousal fuera de [0,1] tampoco saca la voz de la banda.

    El test que decía cubrir esto (`test_out_of_range_arousal_is_clamped`) pasa
    ±5.0 a `PADState`, que lo recorta antes: lo que verificaba era el clamp del
    dataclass, no el del mapeo. Aquí el valor entra crudo al mapeo, que es lo que
    hará cualquier caller futuro que lea el arousal de otra fuente (caché Redis,
    un frame del avatar) sin construir un PADState.
    """
    assert arousal_to_speed(-5.0) == pytest.approx(SPEED_MIN)
    assert arousal_to_speed(5.0) == pytest.approx(SPEED_MAX)


def test_speed_es_monotona_en_arousal_en_todo_el_dominio() -> None:
    """Más arousal NUNCA da menos speed, ni en la zona clampeada ni fuera de [0,1].

    La monotonía es el criterio que hace auditable el mapeo (ADR 0075: dinámica
    determinista): si alguien invirtiera un signo o metiera un `abs()` en la
    ganancia, un córtex excitado podría hablar más despacio que uno apagado. El
    barrido incluye valores fuera de rango a propósito, porque la función los
    tolera por contrato.
    """
    grid = [-1.0, -0.25, 0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0, 1.5]
    for valence in (-1.0, -0.5, 0.0, 0.5, 1.0):
        speeds = [arousal_to_speed(a, valence=valence) for a in grid]
        assert speeds == sorted(speeds), f"no monótona con valence={valence}: {speeds}"


def test_speed_es_estrictamente_creciente_dentro_de_la_banda() -> None:
    """En la zona NO clampeada la monotonía es estricta (el arousal se nota).

    Complementa al test anterior: sin esto, un mapeo constante (p. ej. ganancia 0)
    seguiría siendo «monótono» y pasaría. Con valence neutro, [0,1] cae entero
    dentro de la banda, así que cada paso debe subir de verdad.
    """
    speeds = [arousal_to_speed(a) for a in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]
    assert all(b > a for a, b in pairwise(speeds)), speeds


def test_speed_nunca_sale_de_la_banda_para_ninguna_entrada() -> None:
    """El clamp es total: ninguna combinación (ni ilegal) escapa de [MIN, MAX].

    Es la propiedad que protege a Kokoro de un `speed` absurdo, y la que permite
    al WS reenviar el resultado sin validar nada más.
    """
    for arousal in (-100.0, -1.0, 0.0, 0.3, 0.5, 0.7, 1.0, 2.0, 100.0):
        for valence in (-100.0, -1.0, -0.5, 0.0, 0.5, 1.0, 100.0):
            speed = arousal_to_speed(arousal, valence=valence)
            assert SPEED_MIN <= speed <= SPEED_MAX, (arousal, valence, speed)
