"""Córtex F2 (fase B) — regresión de CALIBRACIÓN del motor PAD (ADR 0075 §7).

Fichero dedicado que la tarea «Suite de calibración (interacciones canónicas →
rangos PAD esperados)» de `docs/roadmap/cortex-f2-afectivo.md` pedía por nombre.
La tabla de escenarios de un solo turno vive en `test_cortex_affective.py`
(`_CANONICAL_INTERACTIONS`, 8 filas); aquí van los escenarios que esa tabla **no
puede** cubrir por construcción, y que son justamente los que dejaban constantes
de calibración sin una sola aserción.

## Por qué hace falta este fichero (medido, no supuesto)

Se instrumentó el motor mutando cada constante de calibración y re-corriendo los
54 tests de `test_cortex_affective.py`. Resultado:

| mutación                       | ¿alguien la detecta? |
| ------------------------------ | -------------------- |
| `INTENSITY_GAIN` 1.0 → 1.5/0.5 | sí (5 y 7 rojos)     |
| `MOOD_EWMA_ALPHA` 0.98 → 0.90  | sí (8 rojos)         |
| `BASELINE_PAD.arousal` .3 → .5 | sí (16 rojos)        |
| `NEUTRAL_DRIVES` 0.5 → 0.9     | sí (8 rojos)         |
| `DRIVE_DECAY_HALF_LIFE_S` ±2×  | sí (3 rojos)         |
| `MOOD_EWMA_ALPHA` 0.98 → 0.999 | **1 rojo, 0 en la calibración** |
| `MOOD_FLOOR` −0.6 → −0.95      | **1 rojo, 0 en la calibración** |
| `MOOD_CEIL` 0.6 → 0.95 / 0.01  | **1 rojo, 0 en la calibración** |
| `DECAY_HALF_LIFE_S` 600 → 60   | **NADIE. 0 rojos en los 54.** |

Las dos causas raíz, ambas estructurales:

1. **La vida media de la emoción no está anclada a ningún reloj.**
   `test_decay_emotion_half_life_property` es auto-referencial: importa
   `DECAY_HALF_LIFE_S`, pasa `elapsed_s = 2 * DECAY_HALF_LIFE_S` a una función que
   divide por esa MISMA constante, y comprueba que el cociente es 1/4. Eso vale
   para CUALQUIER valor de la constante — verifica la FORMA exponencial (que está
   bien y merece su test) pero no la calibración. Es el patrón que
   `docs/03-guides/verificar-antes-de-implementar.md` §4 llama «una guarda que no
   puede fallar no es una guarda». Consecuencia real: se puede pasar la emoción de
   10 min de vida media a 1 min —o a 6 h— y la suite entera sigue verde, cuando ese
   número es exactamente lo que decide cuánto le dura un enfado al córtex en el
   dial que ve el owner (`CortexAffectStore.read` aplica el decay en LECTURA).

2. **La banda de temperamento no se alcanza en un solo turno.** El mood es un EWMA
   con α=0.98, así que UN evento lo mueve como mucho 0.02 — a tres órdenes de
   distancia de `MOOD_FLOOR`/`MOOD_CEIL` (±0.6). Los 8 escenarios canónicos
   arrancan del estado neutro y aplican un evento: el piso/techo nunca entra en
   juego, y por el mismo motivo α=0.98 y α=0.999 son indistinguibles para ellos
   (ambos dejan el mood «casi en cero», y la aserción es una cota superior).

De ahí la forma de este fichero: **anclas absolutas de reloj** para (1) y
**escenarios SOSTENIDOS de varios turnos** para (2). Ninguna aserción compara el
motor consigo mismo: todos los rangos son números absolutos justificados en el
ADR, de modo que mover una constante los rompe.
"""

from __future__ import annotations

import dataclasses

import pytest
from api_server.cortex.affective import (
    BASELINE_PAD,
    MOOD_CEIL,
    MOOD_FLOOR,
    AffectState,
    PADState,
    apply_event,
    decay_emotion,
    neutral_affect_state,
    update_mood,
)

pytestmark = pytest.mark.unit

#: Segundos entre turnos de una conversación sostenida. Un minuto es el ritmo de
#: un chat real; entra explícito para que el decay de la emoción se aplique entre
#: turnos igual que en producción (nunca se usa el reloj real).
_TURN_GAP_S: float = 60.0


# ===========================================================================
# 1. Anclas ABSOLUTAS del reloj de la emoción (la constante que nadie protegía)
# ===========================================================================
# El ADR 0075 §2 fija la escala en palabras: «emoción (Redis, MINUTOS, decae al
# baseline)», frente al mood (EMA lento, casi-temperamento) y los drives (horas).
# Estas anclas convierten ese «minutos» en números: a los 30 s la emoción apenas se
# ha movido, a los 10 min ha recorrido la mitad del camino, y a la hora ya no queda
# nada. Un cambio a segundos o a horas rompe aquí.
@dataclasses.dataclass(frozen=True)
class _DecayAnchor:
    """Un punto de la curva de decay, en tiempo de reloj."""

    elapsed_s: float
    #: Fracción esperada de la distancia inicial al baseline que AÚN queda.
    remaining: tuple[float, float]
    why: str


_DECAY_ANCHORS: tuple[_DecayAnchor, ...] = (
    _DecayAnchor(
        elapsed_s=30.0,
        remaining=(0.94, 0.99),
        why="medio minuto no calma a nadie: la emoción sigue casi entera",
    ),
    _DecayAnchor(
        elapsed_s=300.0,
        remaining=(0.68, 0.73),
        why="a los 5 min queda ~1/raíz(2): media vida media",
    ),
    _DecayAnchor(
        elapsed_s=600.0,
        remaining=(0.49, 0.51),
        why="LA vida media: 10 min ⇒ exactamente la mitad (ADR §2, 'minutos')",
    ),
    _DecayAnchor(
        elapsed_s=1800.0,
        remaining=(0.11, 0.14),
        why="media hora ⇒ 3 vidas medias, ~1/8: la emoción es ya residual",
    ),
    _DecayAnchor(
        elapsed_s=3600.0,
        remaining=(0.0, 0.03),
        why="una hora de silencio ⇒ el córtex ha vuelto al baseline",
    ),
)


@pytest.mark.parametrize("anchor", _DECAY_ANCHORS, ids=lambda a: f"{int(a.elapsed_s)}s")
def test_la_emocion_decae_en_minutos_de_reloj_no_en_segundos_ni_en_horas(
    anchor: _DecayAnchor,
) -> None:
    """Ancla la vida media de la emoción a tiempo REAL, sin usar la constante.

    Es el hueco que el mutation testing destapó: `DECAY_HALF_LIFE_S` podía pasar de
    600 s a 60 s sin que ninguno de los 54 tests del motor se pusiera rojo, porque
    el único test de la vida media divide por la misma constante que multiplica.
    Aquí los segundos son literales, así que la calibración queda fijada.
    """
    hot = PADState(valence=1.0, arousal=1.0, dominance=-1.0, intensity=1.0)
    out = decay_emotion(hot, BASELINE_PAD, elapsed_s=anchor.elapsed_s)

    lo, hi = anchor.remaining
    # Fracción de distancia al baseline que queda, por eje (el baseline no es 0 en
    # arousal: vale 0.3, así que se mide la DISTANCIA, no el valor absoluto).
    for axis in ("valence", "arousal", "dominance"):
        start = getattr(hot, axis) - getattr(BASELINE_PAD, axis)
        now = getattr(out, axis) - getattr(BASELINE_PAD, axis)
        fraction = now / start
        assert lo <= fraction <= hi, (
            f"{axis}: a los {anchor.elapsed_s:.0f}s queda {fraction:.4f} de la "
            f"distancia al baseline, esperado [{lo}, {hi}] — {anchor.why}"
        )
    # La intensidad decae hacia 0 con la misma vida media (no hacia el baseline).
    assert lo <= out.intensity <= hi, anchor.why


def test_la_emocion_y_los_drives_viven_en_escalas_de_tiempo_distintas() -> None:
    """Las tres capas del ADR §2 tienen relojes distintos, y ese ORDEN es el diseño.

    Si alguien igualase las dos vidas medias «para simplificar», los drives se
    vaciarían en minutos y el bucle de curiosidad de F4 dispararía sin parar (o la
    emoción duraría horas y el dial se quedaría clavado). Se fija la relación con
    números de reloj: a la media hora la emoción es residual y los drives siguen
    más de la mitad enteros.
    """
    hot = PADState(valence=1.0, arousal=0.9, dominance=0.5, intensity=1.0)
    half_hour = 1800.0

    emotion_left = decay_emotion(hot, BASELINE_PAD, elapsed_s=half_hour).valence
    from api_server.cortex.affective import decay_drives

    drives_left = decay_drives(neutral_affect_state().drives, elapsed_s=half_hour)

    assert emotion_left <= 0.15, "la emoción debería ser residual a la media hora"
    assert drives_left.curiosity >= 0.40, "los drives viven HORAS, no minutos"
    assert drives_left.curiosity > emotion_left


# ===========================================================================
# 2. Escenarios SOSTENIDOS — la banda de temperamento y la lentitud del mood
# ===========================================================================
def _sustain(delta: PADState, *, turns: int, gap_s: float = _TURN_GAP_S) -> AffectState:
    """Integra `turns` turnos seguidos con el MISMO delta, como el distilador.

    Cada turno: la emoción decae por el hueco entre mensajes, se aplica el delta del
    appraisal y el mood da su paso de EWMA — el orden exacto de
    `workers/cortex_affect.py`. Los drives no se tocan (los mueven otros tests).
    """
    st = neutral_affect_state()
    emotion, mood = st.emotion, st.mood
    for _ in range(turns):
        emotion = decay_emotion(emotion, BASELINE_PAD, elapsed_s=gap_s)
        emotion = apply_event(emotion, delta)
        mood = update_mood(mood, emotion)
    return AffectState(emotion=emotion, mood=mood, drives=st.drives)


#: Elogio repetido, turno tras turno. El delta es el del escenario canónico
#: «elogio_del_owner» de `test_cortex_affective.py`, aquí sostenido.
_PRAISE = PADState(valence=0.5, arousal=0.3, dominance=0.4, intensity=0.4)
#: Crítica repetida (arousal ALTO: tensión, no abatimiento).
_CRITICISM = PADState(valence=-0.5, arousal=0.4, dominance=-0.4, intensity=0.4)


def test_el_elogio_sostenido_satura_el_mood_a_media_altura_del_eje() -> None:
    """Un owner que sólo elogia NO produce un córtex eufórico (ADR §4: sin «manía»).

    La banda de temperamento es la defensa, y hasta ahora ningún test de calibración
    la tocaba: los 8 escenarios de un turno dejan el mood a 0.02 del baseline, a tres
    órdenes de distancia del techo. Aquí se llega al techo de verdad.

    El rango es ABSOLUTO a propósito (0.55–0.65, no `MOOD_CEIL ± ε`): comparar contra
    la constante sería auto-referencial y sobreviviría a que alguien la subiera a
    0.95, que es exactamente la mutación que hoy nadie detecta.
    """
    out = _sustain(_PRAISE, turns=400)

    # La emoción SÍ llega al extremo del eje: el clamp del mood es lo único que
    # separa una cosa de la otra.
    assert out.emotion.valence >= 0.95
    assert 0.55 <= out.mood.valence <= 0.65, (
        f"el mood saturó en {out.mood.valence:.4f}; la banda de temperamento debe "
        "dejarlo a media altura del eje, ni plano ni en euforia"
    )
    assert 0.55 <= out.mood.dominance <= 0.65
    # El invariante que da nombre a la banda: el mood NUNCA alcanza a la emoción.
    assert out.mood.valence <= out.emotion.valence - 0.3
    assert out.mood.valence <= MOOD_CEIL + 1e-9


def test_la_critica_sostenida_satura_el_mood_en_el_piso_sin_cruzarlo() -> None:
    """La mitad simétrica: sin «depresión» simulada (ADR §4).

    Importa además para la honestidad del producto: el piso es lo que impide que el
    panel llegue a mostrarle al owner un córtex «hundido», que sería una afirmación
    sobre sentimientos que el sistema no tiene.
    """
    out = _sustain(_CRITICISM, turns=400)

    assert out.emotion.valence <= -0.95
    assert -0.65 <= out.mood.valence <= -0.55, (
        f"el mood saturó en {out.mood.valence:.4f}; el piso debe cortar antes del extremo del eje"
    )
    assert out.mood.valence >= MOOD_FLOOR - 1e-9
    assert out.mood.valence >= out.emotion.valence + 0.3


def test_el_mood_necesita_docenas_de_turnos_para_moverse_a_medio_camino() -> None:
    """Ancla α en TURNOS, que es la unidad en la que el owner lo percibiría.

    Los escenarios de un turno no distinguen α=0.98 de α=0.999 (ambos dejan el mood
    ~en cero y la aserción es una cota superior). Contando turnos hasta un umbral sí:
    con 0.98 hacen falta ~18 turnos para llegar a 0.30, con 0.999 harían falta ~350.
    Es el test que impide «bajar α para que se note más» y volver al córtex
    adolescente que el EWMA existe para evitar.
    """
    # Ancla exacta: 18 turnos de elogio máximo ⇒ mood a ~0.30 (medido: 0.2973).
    assert 0.28 <= _sustain(_PRAISE, turns=18).mood.valence <= 0.32

    # Y la lentitud como propiedad: pocos turnos ⇒ el mood sigue siendo "neutral"
    # para la UI, aunque la emoción ya esté saturada.
    short = _sustain(_PRAISE, turns=10)
    assert short.emotion.valence >= 0.95
    assert short.mood.valence <= 0.20
    assert short.mood_label(language="es") == "neutral"
    assert short.mood_label(language="en") == "neutral"

    # Monotonía en el número de turnos: más elogio, más mood (nunca al revés).
    trajectory = [_sustain(_PRAISE, turns=n).mood.valence for n in (1, 5, 10, 18, 35, 50)]
    assert trajectory == sorted(trajectory)
    assert trajectory[0] < trajectory[-1]


def test_tras_saturar_el_mood_el_silencio_calma_la_emocion_pero_no_el_animo() -> None:
    """La separación de escalas, que es la tesis del ADR §2, de punta a punta.

    Escenario: una sesión larga de críticas y luego una hora de silencio. La emoción
    vuelve al baseline (minutos) y el mood sigue en el piso (casi-temperamento). Este
    test cae si se rompe CUALQUIERA de las dos calibraciones —la vida media o α— y es
    el que fija que no son la misma cosa.
    """
    after = _sustain(_CRITICISM, turns=200)
    assert after.emotion.valence <= -0.95
    assert after.mood.valence <= -0.55

    cooled = decay_emotion(after.emotion, BASELINE_PAD, elapsed_s=3600.0)

    # La emoción se fue; el mood no se ha movido (nadie lo actualizó: es la capa lenta).
    assert abs(cooled.valence - BASELINE_PAD.valence) <= 0.05
    assert after.mood.valence <= -0.55
    # Y la etiqueta que vería el owner sigue siendo la del mood, no la de la emoción:
    # arousal alto + valence negativa ⇒ tensión, no abatimiento. Bilingüe (ES+EN).
    assert after.mood_label(language="es") == "tensión"
    assert after.mood_label(language="en") == "tension"


def test_el_fail_open_sostenido_no_inventa_animo() -> None:
    """400 turnos con el distilador caído (delta=0) NO mueven el mood.

    Es la garantía de que un Ollama indisponible durante horas no deriva el ánimo del
    córtex en ninguna dirección: sin appraisal no hay afecto, y el estado se queda
    donde el baseline dice. La tabla de un turno cubre el fail-open puntual; esta es
    la versión acumulada, que es donde una deriva de 1e-3 por turno se notaría.
    """
    out = _sustain(PADState(valence=0.0, arousal=0.0, dominance=0.0, intensity=0.0), turns=400)

    assert abs(out.emotion.valence - BASELINE_PAD.valence) <= 1e-6
    assert abs(out.emotion.arousal - BASELINE_PAD.arousal) <= 1e-6
    assert abs(out.emotion.dominance - BASELINE_PAD.dominance) <= 1e-6
    assert abs(out.mood.valence - BASELINE_PAD.valence) <= 1e-6
    assert abs(out.mood.dominance - BASELINE_PAD.dominance) <= 1e-6
    assert out.mood_label(language="es") == "neutral"
