"""Córtex F2 — motor PAD determinista (código puro, FUERA del LLM).

Suite de calibración determinista del motor afectivo (ADR 0075): decay lazy hacia
baseline, update por evento (clampeado), EWMA del mood con piso/techo, decay y
saciado de drives, y la etiqueta de mood derivada del cuadrante PAD. SIN BD, SIN
Redis, SIN LLM, SIN reloj real — el tiempo entra como parámetro (``now`` /
``elapsed_s``) para que todo sea determinista y testeable.

Honestidad (ADR 0075 §6): esto es simulación afectiva determinista, NO emociones
reales; las etiquetas son derivadas SOLO para UI, nunca fuente de verdad.
"""

from __future__ import annotations

import dataclasses
import math

import pytest
from api_server.cortex.affective import (
    BASELINE_PAD,
    MOOD_CEIL,
    MOOD_EWMA_ALPHA,
    MOOD_FLOOR,
    AffectState,
    Drives,
    PADState,
    apply_event,
    decay_drives,
    decay_emotion,
    derive_mood_label,
    neutral_affect_state,
    satisfy_drive,
    update_mood,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Construcción + clamps por construcción
# ---------------------------------------------------------------------------
def test_padstate_clamps_each_axis_to_its_range() -> None:
    # valence/dominance ∈ [-1, 1]; arousal/intensity ∈ [0, 1].
    s = PADState(valence=5.0, arousal=9.0, dominance=-7.0, intensity=-3.0)
    assert s.valence == 1.0
    assert s.arousal == 1.0
    assert s.dominance == -1.0
    assert s.intensity == 0.0

    s2 = PADState(valence=-2.0, arousal=-2.0, dominance=2.0, intensity=2.0)
    assert s2.valence == -1.0
    assert s2.arousal == 0.0
    assert s2.dominance == 1.0
    assert s2.intensity == 1.0


def test_padstate_is_frozen() -> None:
    s = PADState(valence=0.1, arousal=0.2, dominance=0.3, intensity=0.4)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.valence = 0.9  # type: ignore[misc]


def test_drives_clamps_to_unit_interval() -> None:
    d = Drives(curiosity=2.0, bonding=-1.0, coherence=0.5, competence=1.5)
    assert d.curiosity == 1.0
    assert d.bonding == 0.0
    assert d.coherence == 0.5
    assert d.competence == 1.0


def test_neutral_affect_state_is_baseline() -> None:
    st = neutral_affect_state()
    assert st.emotion == BASELINE_PAD
    assert st.mood == BASELINE_PAD
    # Los drives neutros arrancan a media saturación (capacidad de subir y bajar).
    assert st.drives == Drives(curiosity=0.5, bonding=0.5, coherence=0.5, competence=0.5)


# ---------------------------------------------------------------------------
# decay_emotion — decay lazy hacia baseline (homeostasis)
# ---------------------------------------------------------------------------
def test_decay_emotion_zero_elapsed_is_identity() -> None:
    s = PADState(valence=0.8, arousal=0.7, dominance=-0.4, intensity=0.9)
    out = decay_emotion(s, BASELINE_PAD, elapsed_s=0.0)
    assert out == s


def test_decay_emotion_converges_to_baseline_over_time() -> None:
    s = PADState(valence=0.9, arousal=0.9, dominance=0.9, intensity=0.9)
    out = decay_emotion(s, BASELINE_PAD, elapsed_s=3600.0 * 100)  # 100 h
    assert math.isclose(out.valence, BASELINE_PAD.valence, abs_tol=1e-3)
    assert math.isclose(out.arousal, BASELINE_PAD.arousal, abs_tol=1e-3)
    assert math.isclose(out.dominance, BASELINE_PAD.dominance, abs_tol=1e-3)
    assert math.isclose(out.intensity, 0.0, abs_tol=1e-3)


def test_decay_emotion_half_life_property() -> None:
    # Tras 2*half_life la distancia al baseline es ≈ 1/4 (decay exponencial).
    from api_server.cortex.affective import DECAY_HALF_LIFE_S

    s = PADState(valence=1.0, arousal=1.0, dominance=-1.0, intensity=1.0)
    out = decay_emotion(s, BASELINE_PAD, elapsed_s=2.0 * DECAY_HALF_LIFE_S)
    d0 = s.valence - BASELINE_PAD.valence
    d = out.valence - BASELINE_PAD.valence
    assert math.isclose(d / d0, 0.25, abs_tol=1e-3)


def test_decay_emotion_is_monotone_and_never_crosses_baseline() -> None:
    s = PADState(valence=0.9, arousal=0.9, dominance=-0.9, intensity=0.9)
    prev = s.valence
    crossed = False
    for k in range(1, 50):
        out = decay_emotion(s, BASELINE_PAD, elapsed_s=60.0 * k)
        # Monótono hacia el baseline (valence baja), nunca por debajo de él.
        assert out.valence <= prev + 1e-9
        assert out.valence >= BASELINE_PAD.valence - 1e-9
        prev = out.valence
        if out.valence < BASELINE_PAD.valence:
            crossed = True
    assert not crossed


# ---------------------------------------------------------------------------
# apply_event — update por evento + clamps + intensity
# ---------------------------------------------------------------------------
def test_apply_event_positive_delta_raises_valence() -> None:
    s = PADState(valence=0.0, arousal=0.2, dominance=0.0, intensity=0.0)
    delta = PADState(valence=0.5, arousal=0.3, dominance=0.2, intensity=0.4)
    out = apply_event(s, delta)
    assert out.valence > s.valence
    assert out.arousal > s.arousal


def test_apply_event_clamps_at_ceiling() -> None:
    s = PADState(valence=0.9, arousal=0.9, dominance=0.9, intensity=0.9)
    delta = PADState(valence=0.9, arousal=0.9, dominance=0.9, intensity=0.9)
    out = apply_event(s, delta)
    assert out.valence == 1.0
    assert out.arousal == 1.0
    assert out.dominance == 1.0
    assert out.intensity == 1.0


def test_apply_event_zero_delta_is_identity_fail_open() -> None:
    # El camino fail-open (Ollama caído ⇒ delta=0) no debe mover el estado.
    s = PADState(valence=0.3, arousal=0.4, dominance=-0.1, intensity=0.5)
    zero = PADState(valence=0.0, arousal=0.0, dominance=0.0, intensity=0.0)
    out = apply_event(s, zero)
    assert out == s


def test_apply_event_intensity_tracks_delta_magnitude() -> None:
    s = PADState(valence=0.0, arousal=0.0, dominance=0.0, intensity=0.0)
    small = apply_event(s, PADState(valence=0.1, arousal=0.0, dominance=0.0, intensity=0.0))
    big = apply_event(s, PADState(valence=0.9, arousal=0.0, dominance=0.0, intensity=0.0))
    assert big.intensity > small.intensity


def test_apply_event_negative_delta_lowers_and_clamps_floor() -> None:
    s = PADState(valence=-0.9, arousal=0.1, dominance=-0.9, intensity=0.2)
    delta = PADState(valence=-0.9, arousal=0.0, dominance=-0.9, intensity=0.3)
    out = apply_event(s, delta)
    assert out.valence == -1.0
    assert out.dominance == -1.0


# ---------------------------------------------------------------------------
# update_mood — EWMA lento + piso/techo de temperamento
# ---------------------------------------------------------------------------
def test_update_mood_is_ewma() -> None:
    mood = PADState(valence=0.0, arousal=0.3, dominance=0.0, intensity=0.0)
    emotion = PADState(valence=1.0, arousal=0.9, dominance=0.5, intensity=0.8)
    out = update_mood(mood, emotion)
    a = MOOD_EWMA_ALPHA
    expected_v = a * mood.valence + (1 - a) * emotion.valence
    assert math.isclose(out.valence, expected_v, abs_tol=1e-9)


def test_update_mood_saturates_within_temperament_band() -> None:
    # Tras muchas iteraciones con emoción extrema, el mood se satura en el
    # piso/techo de temperamento, NUNCA alcanza el extremo de la emoción.
    mood = PADState(valence=0.0, arousal=0.3, dominance=0.0, intensity=0.0)
    extreme = PADState(valence=1.0, arousal=1.0, dominance=1.0, intensity=1.0)
    for _ in range(5000):
        mood = update_mood(mood, extreme)
    assert mood.valence <= MOOD_CEIL + 1e-9
    assert mood.valence < 1.0  # nunca el extremo de la emoción
    assert mood.dominance <= MOOD_CEIL + 1e-9

    mood = PADState(valence=0.0, arousal=0.3, dominance=0.0, intensity=0.0)
    extreme_neg = PADState(valence=-1.0, arousal=1.0, dominance=-1.0, intensity=1.0)
    for _ in range(5000):
        mood = update_mood(mood, extreme_neg)
    assert mood.valence >= MOOD_FLOOR - 1e-9
    assert mood.valence > -1.0


# ---------------------------------------------------------------------------
# decay_drives + satisfy_drive
# ---------------------------------------------------------------------------
def test_decay_drives_decays_toward_zero() -> None:
    d = Drives(curiosity=0.9, bonding=0.8, coherence=0.7, competence=0.6)
    out = decay_drives(d, elapsed_s=3600.0 * 100)
    assert out.curiosity < d.curiosity
    assert out.curiosity >= 0.0
    assert math.isclose(out.curiosity, 0.0, abs_tol=1e-2)


def test_decay_drives_zero_elapsed_is_identity() -> None:
    d = Drives(curiosity=0.5, bonding=0.5, coherence=0.5, competence=0.5)
    assert decay_drives(d, elapsed_s=0.0) == d


def test_satisfy_drive_raises_clamped() -> None:
    d = Drives(curiosity=0.4, bonding=0.5, coherence=0.5, competence=0.5)
    out = satisfy_drive(d, "curiosity", 0.3)
    assert math.isclose(out.curiosity, 0.7, abs_tol=1e-9)
    capped = satisfy_drive(d, "curiosity", 5.0)
    assert capped.curiosity == 1.0


def test_satisfy_drive_unknown_is_noop() -> None:
    d = Drives(curiosity=0.4, bonding=0.5, coherence=0.5, competence=0.5)
    assert satisfy_drive(d, "nonexistent", 0.5) == d


# ---------------------------------------------------------------------------
# derive_mood_label — etiqueta categórica SOLO-UI, bilingüe (ES/EN)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "valence,arousal,es,en",
    [
        (0.8, 0.8, "alegría", "joy"),  # valence alto + arousal alto
        (0.8, 0.2, "calma", "calm"),  # valence alto + arousal bajo
        (-0.8, 0.8, "tensión", "tension"),  # valence bajo + arousal alto
        (-0.8, 0.2, "abatimiento", "down"),  # valence bajo + arousal bajo
    ],
)
def test_derive_mood_label_quadrants(valence: float, arousal: float, es: str, en: str) -> None:
    mood = PADState(valence=valence, arousal=arousal, dominance=0.0, intensity=0.0)
    assert derive_mood_label(mood, language="es") == es
    assert derive_mood_label(mood, language="en") == en


def test_derive_mood_label_neutral_band() -> None:
    mood = PADState(valence=0.0, arousal=0.3, dominance=0.0, intensity=0.0)
    assert derive_mood_label(mood, language="es") == "neutral"
    assert derive_mood_label(mood, language="en") == "neutral"


def test_derive_mood_label_defaults_to_spanish() -> None:
    mood = PADState(valence=0.8, arousal=0.8, dominance=0.0, intensity=0.0)
    assert derive_mood_label(mood) == "alegría"


# ---------------------------------------------------------------------------
# AffectState.mood_label — etiqueta derivada del estado agregado
# ---------------------------------------------------------------------------
def test_affect_state_mood_label_is_derived_from_mood() -> None:
    st = AffectState(
        emotion=PADState(valence=0.0, arousal=0.3, dominance=0.0, intensity=0.0),
        mood=PADState(valence=0.8, arousal=0.8, dominance=0.2, intensity=0.0),
        drives=Drives(curiosity=0.5, bonding=0.5, coherence=0.5, competence=0.5),
    )
    assert st.mood_label(language="es") == "alegría"
    assert st.mood_label(language="en") == "joy"


# ---------------------------------------------------------------------------
# Suite de calibración — interacciones canónicas → rangos PAD esperados
# (ADR 0075 §7). Regresión que detecta cambios involuntarios en la dinámica.
# ---------------------------------------------------------------------------
def _apply_canonical(delta: PADState, drive: tuple[str, float] | None = None) -> AffectState:
    st = neutral_affect_state()
    emotion = apply_event(st.emotion, delta)
    mood = update_mood(st.mood, emotion)
    drives = st.drives
    if drive is not None:
        drives = satisfy_drive(drives, drive[0], drive[1])
    return AffectState(emotion=emotion, mood=mood, drives=drives)


def test_calibration_owner_praise_is_positive_high_dominance() -> None:
    # Elogio del owner: valence alto, dominance positivo (competencia validada).
    out = _apply_canonical(
        PADState(valence=0.7, arousal=0.4, dominance=0.5, intensity=0.6),
        drive=("competence", 0.3),
    )
    assert out.emotion.valence > 0.5
    assert out.emotion.dominance > 0.3
    assert out.drives.competence > 0.5


def test_calibration_criticism_is_negative() -> None:
    out = _apply_canonical(PADState(valence=-0.6, arousal=0.5, dominance=-0.4, intensity=0.6))
    assert out.emotion.valence < -0.3
    assert out.emotion.dominance < 0.0


def test_calibration_curious_question_satisfies_curiosity() -> None:
    out = _apply_canonical(
        PADState(valence=0.4, arousal=0.6, dominance=0.1, intensity=0.5),
        drive=("curiosity", 0.4),
    )
    assert out.emotion.arousal > 0.4
    assert out.drives.curiosity > 0.5


def test_calibration_cold_farewell_lowers_bonding() -> None:
    st = neutral_affect_state()
    # Una despedida fría baja bonding: lo modelamos como decay (no saciado).
    cooled = decay_drives(st.drives, elapsed_s=3600.0 * 6)
    assert cooled.bonding < st.drives.bonding


# ---------------------------------------------------------------------------
# Tabla canónica de calibración (ADR 0075 §7) — los ~8 escenarios que el plan F2
# pedía y que la auditoría del 2026-07-27 encontró a medias: sólo 3 ejercitaban
# de verdad `apply_event`+`update_mood` (el cuarto, la despedida fría de arriba,
# sólo llamaba a `decay_drives`, ya cubierto por el test de drives).
#
# Qué defecto atrapa esta tabla y no atrapan los tests unitarios de cada función:
# un cambio de CALIBRACIÓN. Las fórmulas pueden seguir siendo correctas (decay
# exponencial, EWMA, clamps) y aun así el córtex puede volverse eufórico o
# depresivo si alguien retoca `INTENSITY_GAIN`, la banda de temperamento o el
# baseline. Los tests por-función pasarían igual; esto no. Cada fila fija el
# RANGO PAD esperado del estado resultante, no un valor exacto: los rangos son la
# especificación ("un elogio deja valence entre 0.6 y 0.8"), y son lo bastante
# estrechos para que un cambio de ganancia los rompa.
#
# Todas las filas arrancan del estado neutro, aplican UN evento y avanzan el mood
# una vez — el turno tal como lo integra `workers/cortex_affect.py`.
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class _Canonical:
    """Un escenario canónico: el delta del appraisal y el estado que debe producir."""

    #: Delta PAD que el distilador emitiría para esa interacción.
    delta: PADState
    #: Rangos cerrados (mín, máx) esperados del estado EMOCIONAL resultante.
    valence: tuple[float, float]
    arousal: tuple[float, float]
    dominance: tuple[float, float]
    intensity: tuple[float, float]
    #: Rangos esperados de los drives que el escenario mueve.
    drives: dict[str, tuple[float, float]]
    #: Drive saciado por la interacción, si la sacia alguno.
    satisfied: tuple[str, float] | None = None
    #: Tiempo sin interactuar antes del evento (decae los drives, no la emoción).
    idle_s: float = 0.0


_CANONICAL_INTERACTIONS: dict[str, _Canonical] = {
    # El owner celebra un trabajo bien hecho: valence arriba y dominance positivo
    # (competencia validada), no sólo "contento".
    "elogio_del_owner": _Canonical(
        delta=PADState(valence=0.7, arousal=0.4, dominance=0.5, intensity=0.6),
        valence=(0.60, 0.80),
        arousal=(0.60, 0.80),
        dominance=(0.40, 0.60),
        intensity=(0.85, 1.00),
        drives={"competence": (0.75, 0.85)},
        satisfied=("competence", 0.3),
    ),
    # Una crítica dura: valence abajo, arousal ARRIBA (activa, no apática) y
    # dominance negativo — el patrón que distingue "tensión" de "abatimiento".
    "critica_dura": _Canonical(
        delta=PADState(valence=-0.6, arousal=0.5, dominance=-0.4, intensity=0.6),
        valence=(-0.70, -0.50),
        arousal=(0.70, 0.90),
        dominance=(-0.50, -0.30),
        intensity=(0.80, 0.95),
        drives={},
    ),
    # Una pregunta interesante: arousal alto y `curiosity` saciada — el drive que
    # en F4 deja de empujar el bucle de curiosidad justo porque está saciado.
    "pregunta_curiosa": _Canonical(
        delta=PADState(valence=0.4, arousal=0.6, dominance=0.1, intensity=0.5),
        valence=(0.30, 0.50),
        arousal=(0.80, 1.00),
        dominance=(0.00, 0.20),
        intensity=(0.65, 0.80),
        drives={"curiosity": (0.85, 0.95)},
        satisfied=("curiosity", 0.4),
    ),
    # Despedida fría tras horas de silencio: valence ligeramente negativa y
    # `bonding` MUY bajo por decay (nadie lo ha saciado).
    #
    # OJO con el arousal: el delta NO puede bajarlo. `arousal` vive en [0,1] y el
    # constructor de PADState lo clampa (ADR 0075 §1), así que un appraisal que
    # pidiera arousal=-0.15 emite 0.0 y la emoción se queda en el baseline. Que un
    # turno "calmante" NO calme por appraisal —sólo por el paso del tiempo— es
    # consecuencia del diseño, no un descuido: se fija aquí para que nadie lo
    # "arregle" ensanchando el rango del eje.
    "despedida_fria": _Canonical(
        delta=PADState(valence=-0.3, arousal=0.0, dominance=-0.1, intensity=0.2),
        valence=(-0.40, -0.20),
        arousal=(0.28, 0.32),
        dominance=(-0.20, 0.00),
        intensity=(0.25, 0.40),
        drives={"bonding": (0.10, 0.15)},
        idle_s=3600.0 * 6,
    ),
    # El owner vuelve y la conversación es cálida: `bonding` saciado.
    "reencuentro_calido": _Canonical(
        delta=PADState(valence=0.5, arousal=0.2, dominance=0.1, intensity=0.4),
        valence=(0.40, 0.60),
        arousal=(0.45, 0.55),
        dominance=(0.00, 0.20),
        intensity=(0.50, 0.60),
        drives={"bonding": (0.80, 0.90)},
        satisfied=("bonding", 0.35),
    ),
    # Se le señala una contradicción en su propia narrativa: valence abajo,
    # arousal arriba, dominance abajo y `coherence` sin saciar (decae).
    "contradiccion_en_su_identidad": _Canonical(
        delta=PADState(valence=-0.4, arousal=0.45, dominance=-0.35, intensity=0.5),
        valence=(-0.50, -0.30),
        arousal=(0.70, 0.80),
        dominance=(-0.45, -0.25),
        intensity=(0.65, 0.75),
        drives={"coherence": (0.20, 0.30)},
        idle_s=3600.0 * 3,
    ),
    # Cierra una tarea con éxito: el pico de dominance del catálogo (agencia).
    "tarea_resuelta_con_exito": _Canonical(
        delta=PADState(valence=0.6, arousal=0.3, dominance=0.7, intensity=0.5),
        valence=(0.50, 0.70),
        arousal=(0.55, 0.65),
        dominance=(0.60, 0.80),
        intensity=(0.92, 1.00),
        drives={"competence": (0.85, 0.95)},
        satisfied=("competence", 0.4),
    ),
    # Silencio del owner + el camino FAIL-OPEN del distilador (Ollama caído ⇒
    # delta=0): la emoción NO se mueve ni un ápice y los drives sólo decaen. Es el
    # escenario que garantiza que un appraisal indisponible no inventa afecto.
    "silencio_del_owner": _Canonical(
        delta=PADState(valence=0.0, arousal=0.0, dominance=0.0, intensity=0.0),
        valence=(-0.001, 0.001),
        arousal=(0.299, 0.301),
        dominance=(-0.001, 0.001),
        intensity=(0.0, 0.001),
        drives={
            "curiosity": (0.20, 0.30),
            "bonding": (0.20, 0.30),
            "coherence": (0.20, 0.30),
            "competence": (0.20, 0.30),
        },
        idle_s=3600.0 * 3,
    ),
}


def _integrate_turn(case: _Canonical) -> AffectState:
    """Integra UN turno canónico igual que el distilador: decay de drives por el
    tiempo ocioso, `apply_event` sobre la emoción, `update_mood` y saciado."""
    st = neutral_affect_state()
    emotion = apply_event(st.emotion, case.delta)
    mood = update_mood(st.mood, emotion)
    drives = st.drives
    if case.idle_s:
        drives = decay_drives(drives, elapsed_s=case.idle_s)
    if case.satisfied is not None:
        drives = satisfy_drive(drives, *case.satisfied)
    return AffectState(emotion=emotion, mood=mood, drives=drives)


@pytest.mark.parametrize("name", sorted(_CANONICAL_INTERACTIONS))
def test_canonical_interaction_lands_in_its_expected_pad_range(name: str) -> None:
    case = _CANONICAL_INTERACTIONS[name]
    out = _integrate_turn(case)

    for axis, (lo, hi) in (
        ("valence", case.valence),
        ("arousal", case.arousal),
        ("dominance", case.dominance),
        ("intensity", case.intensity),
    ):
        value = getattr(out.emotion, axis)
        assert lo <= value <= hi, f"{name}: {axis}={value:.4f} fuera de [{lo}, {hi}]"

    for drive, (lo, hi) in case.drives.items():
        value = getattr(out.drives, drive)
        assert lo <= value <= hi, f"{name}: drive {drive}={value:.4f} fuera de [{lo}, {hi}]"


@pytest.mark.parametrize("name", sorted(_CANONICAL_INTERACTIONS))
def test_canonical_interaction_never_swings_the_mood(name: str) -> None:
    """Ninguna interacción SUELTA puede mover el mood de forma perceptible.

    Es el invariante que hace del córtex algo estable en vez de un adolescente: el
    mood es casi-temperamento (EWMA α=0.98), así que un solo turno lo desplaza como
    mucho un (1-α) del camino y su etiqueta sigue siendo "neutral". Si alguien
    bajase α "para que se note más", este test cae en las 8 filas a la vez —
    mientras que los tests por-función seguirían en verde."""
    case = _CANONICAL_INTERACTIONS[name]
    out = _integrate_turn(case)
    step = 1.0 - MOOD_EWMA_ALPHA

    assert abs(out.mood.valence - BASELINE_PAD.valence) <= step + 1e-9, name
    assert abs(out.mood.dominance - BASELINE_PAD.dominance) <= step + 1e-9, name
    assert abs(out.mood.arousal - BASELINE_PAD.arousal) <= step + 1e-9, name
    # El mood se mueve HACIA la emoción, nunca en contra.
    assert out.mood.valence * out.emotion.valence >= 0.0, name
    assert out.mood_label(language="es") == "neutral", name
    assert out.mood_label(language="en") == "neutral", name


@pytest.mark.parametrize("name", sorted(_CANONICAL_INTERACTIONS))
def test_canonical_interaction_stays_inside_the_declared_ranges(name: str) -> None:
    """Ningún escenario canónico puede sacar un eje de su rango declarado (ADR 0075
    §1). Redundante con los clamps por construcción — a propósito: aquí se verifica
    sobre el estado COMPUESTO, que es lo que se serializa al snapshot y al frame de
    telemetría, y un serializador que reconstruyese el estado a mano se saltaría el
    constructor."""
    out = _integrate_turn(_CANONICAL_INTERACTIONS[name])
    for state in (out.emotion, out.mood):
        assert -1.0 <= state.valence <= 1.0, name
        assert 0.0 <= state.arousal <= 1.0, name
        assert -1.0 <= state.dominance <= 1.0, name
        assert 0.0 <= state.intensity <= 1.0, name
    for value in out.drives.as_dict().values():
        assert 0.0 <= value <= 1.0, name
