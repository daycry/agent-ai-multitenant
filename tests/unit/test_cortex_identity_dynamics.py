"""Córtex F3 (bloque 2) — capa pura de mutación de identidad (sin LLM/red/DB).

Cubre los helpers deterministas que la reflexión usa para reescribir la identidad
de forma ACOTADA y versionada (ADR 0074: guardrail de auto-modificación):

  * ``clamp_traits`` / ``clamp_baseline`` — recortan a los rangos canónicos.
  * ``bounded_update`` — limita |Δ| por ciclo (no más de ``max_delta_per_cycle``).
  * ``editable_owner_state`` — el override del owner SOLO toca name/core_values/
    narrative/language/learning_goals; jamás traits/mood_baseline/relationship_model.
  * ``apply_reflection_delta`` — compone clamp+bounded sobre traits/baseline +
    reescribe narrative, devolviendo el nuevo ``identity_state`` (puro).
  * ``compute_diff`` — la auditoría del cambio: SOLO los campos que cambiaron.
"""

from __future__ import annotations

import pytest
from api_server.cortex.identity import (
    apply_reflection_delta,
    bounded_update,
    clamp_baseline,
    clamp_traits,
    compute_diff,
    default_identity_state,
    editable_owner_state,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# clamp_traits — cada Big-Five a [0,1]
# ---------------------------------------------------------------------------
def test_clamp_traits_recorta_a_rango() -> None:
    out = clamp_traits(
        {
            "openness": 1.7,
            "conscientiousness": -0.5,
            "extraversion": 0.5,
            "agreeableness": 0.5,
            "neuroticism": 0.5,
        }
    )
    assert out["openness"] == 1.0
    assert out["conscientiousness"] == 0.0
    assert out["extraversion"] == 0.5


def test_clamp_traits_rellena_claves_faltantes_con_neutro() -> None:
    out = clamp_traits({"openness": 0.9})
    # las cinco claves presentes; las faltantes en el punto medio neutro.
    assert set(out) == {
        "openness",
        "conscientiousness",
        "extraversion",
        "agreeableness",
        "neuroticism",
    }
    assert out["openness"] == 0.9
    assert out["neuroticism"] == 0.5


def test_clamp_traits_ignora_valores_no_numericos() -> None:
    out = clamp_traits({"openness": "alto", "extraversion": None})
    # un valor sucio cae al neutro 0.5 (nunca lanza).
    assert out["openness"] == 0.5
    assert out["extraversion"] == 0.5


# ---------------------------------------------------------------------------
# clamp_baseline — valence∈[-1,1], arousal∈[0,1], dominance∈[-1,1]
# ---------------------------------------------------------------------------
def test_clamp_baseline_recorta_cada_eje() -> None:
    out = clamp_baseline({"valence": 2.0, "arousal": -0.3, "dominance": -5.0})
    assert out["valence"] == 1.0
    assert out["arousal"] == 0.0
    assert out["dominance"] == -1.0


def test_clamp_baseline_rellena_faltantes_con_neutro() -> None:
    out = clamp_baseline({})
    assert out == {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}


# ---------------------------------------------------------------------------
# bounded_update — |Δ| por ciclo acotado
# ---------------------------------------------------------------------------
def test_bounded_update_acota_un_salto_grande() -> None:
    current = {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}
    proposed = {"valence": 0.9, "arousal": -0.9, "dominance": 0.4}
    out = bounded_update(current, proposed, max_delta_per_cycle=0.05)
    # Un salto grande se recorta a ±max_delta_per_cycle.
    assert out["valence"] == 0.05
    assert out["arousal"] == -0.05
    assert out["dominance"] == 0.05


def test_bounded_update_respeta_un_cambio_pequeno() -> None:
    current = {"openness": 0.50}
    proposed = {"openness": 0.53}
    out = bounded_update(current, proposed, max_delta_per_cycle=0.05)
    # 0.03 < 0.05 → pasa tal cual.
    assert abs(out["openness"] - 0.53) < 1e-9


def test_bounded_update_solo_campos_comunes_y_estables() -> None:
    current = {"openness": 0.5, "extraversion": 0.5}
    proposed = {"openness": 1.0}  # 'extraversion' ausente del propuesto
    out = bounded_update(current, proposed, max_delta_per_cycle=0.05)
    assert out["openness"] == 0.55
    # un campo no propuesto se conserva intacto.
    assert out["extraversion"] == 0.5


def test_bounded_update_converge_no_oscila() -> None:
    # Reflexión repetida hacia un objetivo: monótona, sin overshoot/oscilación.
    current = {"openness": 0.5}
    target = {"openness": 1.0}
    prev = current["openness"]
    for _ in range(5):
        current = bounded_update(current, target, max_delta_per_cycle=0.05)
        assert current["openness"] >= prev  # monótono hacia el objetivo
        assert current["openness"] <= 1.0  # nunca lo cruza
        prev = current["openness"]
    assert abs(current["openness"] - 0.75) < 1e-9  # 0.5 + 5*0.05


# ---------------------------------------------------------------------------
# editable_owner_state — override acotado del owner
# ---------------------------------------------------------------------------
def test_editable_owner_state_solo_toca_campos_permitidos() -> None:
    current = default_identity_state()
    current["traits"]["openness"] = 0.8  # un trait derivado por reflexión
    current["mood_baseline"] = {"valence": 0.3, "arousal": 0.2, "dominance": 0.1}
    current["relationship_model"] = {"owner_likes": "rigor"}

    out = editable_owner_state(
        current,
        name="Atlas",
        core_values=["honestidad", "  ", "curiosidad"],
        narrative="Soy un córtex.",
        language="en",
        learning_goals=["mejorar"],
    )
    # Campos editables aplicados (core_values normalizados — sin vacíos).
    assert out["name"] == "Atlas"
    assert out["core_values"] == ["honestidad", "curiosidad"]
    assert out["narrative"] == "Soy un córtex."
    assert out["language"] == "en"
    assert out["learning_goals"] == ["mejorar"]
    # Campos NO editables PRESERVADOS del estado actual (la reflexión los gobierna).
    assert out["traits"]["openness"] == 0.8
    assert out["mood_baseline"] == {"valence": 0.3, "arousal": 0.2, "dominance": 0.1}
    assert out["relationship_model"] == {"owner_likes": "rigor"}


def test_editable_owner_state_campos_none_no_pisan() -> None:
    current = default_identity_state()
    current["name"] = "Atlas"
    current["core_values"] = ["x"]
    # Un PUT parcial (solo language) no debe borrar name/core_values.
    out = editable_owner_state(current, language="en")
    assert out["name"] == "Atlas"
    assert out["core_values"] == ["x"]
    assert out["language"] == "en"


def test_editable_owner_state_no_muta_el_input() -> None:
    current = default_identity_state()
    snapshot = dict(current)
    editable_owner_state(current, name="Atlas")
    # el dict original no se toca (devuelve uno nuevo).
    assert current == snapshot


# ---------------------------------------------------------------------------
# apply_reflection_delta — clamp + bounded + narrative, todo en uno (puro)
# ---------------------------------------------------------------------------
def test_apply_reflection_delta_acota_traits_y_baseline() -> None:
    current = default_identity_state()  # traits 0.5, baseline 0
    out = apply_reflection_delta(
        current,
        narrative="He aprendido que el owner valora el rigor.",
        traits={"openness": 0.95, "conscientiousness": 0.95},
        mood_baseline={"valence": 0.9, "arousal": 0.9, "dominance": -0.9},
        max_delta_per_cycle=0.05,
    )
    # Narrative reescrita.
    assert out["narrative"] == "He aprendido que el owner valora el rigor."
    # traits movidos SOLO ±0.05 desde 0.5 pese al salto grande propuesto.
    assert abs(out["traits"]["openness"] - 0.55) < 1e-9
    assert abs(out["traits"]["conscientiousness"] - 0.55) < 1e-9
    # un trait no propuesto se conserva en su valor.
    assert out["traits"]["neuroticism"] == 0.5
    # baseline movido ±0.05 desde 0.
    assert abs(out["mood_baseline"]["valence"] - 0.05) < 1e-9
    assert abs(out["mood_baseline"]["arousal"] - 0.05) < 1e-9
    assert abs(out["mood_baseline"]["dominance"] - (-0.05)) < 1e-9


def test_apply_reflection_delta_sin_narrativa_conserva_la_actual() -> None:
    current = default_identity_state()
    current["narrative"] = "narrativa previa"
    out = apply_reflection_delta(current, narrative=None, traits=None, mood_baseline=None)
    assert out["narrative"] == "narrativa previa"
    # sin deltas, traits/baseline intactos.
    assert out["traits"] == current["traits"]
    assert out["mood_baseline"] == current["mood_baseline"]


def test_apply_reflection_delta_no_muta_el_input() -> None:
    current = default_identity_state()
    snapshot = {k: (dict(v) if isinstance(v, dict) else v) for k, v in current.items()}
    apply_reflection_delta(current, narrative="x", traits={"openness": 1.0})
    assert current["narrative"] == snapshot["narrative"]
    assert current["traits"] == snapshot["traits"]


def test_reflexion_repetida_converge_y_se_queda_quieta() -> None:
    """El criterio «reflexión repetida converge (no oscila)» del plan, pero sobre el
    paso COMPUESTO (clamp+bounded juntos), no solo sobre ``bounded_update``.

    El defecto que atrapa: que la composición clamp→bounded→clamp introduzca
    overshoot o un ciclo límite (el estado se pasa del objetivo y vuelve, ida y
    vuelta indefinidamente). Con un mismo objetivo propuesto ciclo tras ciclo la
    identidad debe acercarse de forma monótona, no cruzarlo nunca, y quedarse
    EXACTAMENTE ahí — si el punto fijo no fuese estable, la identidad del córtex
    "temblaría" para siempre y cada pasada emitiría una versión de histórico nueva
    con un diff espurio.
    """
    state = default_identity_state()  # openness 0.5, valence 0.0
    prev_openness = state["traits"]["openness"]
    prev_valence = state["mood_baseline"]["valence"]
    for _ in range(12):
        state = apply_reflection_delta(
            state,
            traits={"openness": 0.8},
            mood_baseline={"valence": 0.2},
            max_delta_per_cycle=0.05,
        )
        openness = state["traits"]["openness"]
        valence = state["mood_baseline"]["valence"]
        # Monótono hacia el objetivo y sin cruzarlo (nada de overshoot).
        assert openness >= prev_openness
        assert openness <= 0.8 + 1e-9
        assert valence >= prev_valence
        assert valence <= 0.2 + 1e-9
        prev_openness, prev_valence = openness, valence
    # Punto fijo alcanzado y ESTABLE (las últimas pasadas ya no mueven nada).
    assert abs(state["traits"]["openness"] - 0.8) < 1e-9
    assert abs(state["mood_baseline"]["valence"] - 0.2) < 1e-9
    again = apply_reflection_delta(
        state,
        traits={"openness": 0.8},
        mood_baseline={"valence": 0.2},
        max_delta_per_cycle=0.05,
    )
    assert compute_diff(state, again) == {}


# ---------------------------------------------------------------------------
# effective_mood_baseline — el set-point que el motor afectivo DEBE leer
# ---------------------------------------------------------------------------
def test_effective_baseline_lee_el_mood_baseline_calibrado() -> None:
    from api_server.cortex.identity import effective_mood_baseline

    state = default_identity_state()
    state["mood_baseline"] = {"valence": 0.2, "arousal": 0.45, "dominance": -0.1}
    pad = effective_mood_baseline(state)
    assert pad.valence == 0.2
    assert pad.arousal == 0.45
    assert pad.dominance == -0.1
    assert pad.intensity == 0.0


def test_effective_baseline_arousal_cero_cae_al_neutro_del_motor() -> None:
    # arousal <= 0.0 se trata como "sin calibrar": el motor usa 0.3 (calma
    # despierta, BASELINE_PAD), no 0.0 (catatónico) — desajuste documentado.
    from api_server.cortex.affective import BASELINE_PAD
    from api_server.cortex.identity import effective_mood_baseline

    pad = effective_mood_baseline(default_identity_state())
    assert pad.valence == 0.0
    assert pad.arousal == BASELINE_PAD.arousal
    assert pad.dominance == 0.0


def test_effective_baseline_sin_estado_es_neutro() -> None:
    from api_server.cortex.affective import BASELINE_PAD
    from api_server.cortex.identity import effective_mood_baseline

    pad = effective_mood_baseline(None)
    assert pad.valence == 0.0
    assert pad.arousal == BASELINE_PAD.arousal
    assert pad.dominance == 0.0


def test_effective_baseline_clampa_fuera_de_rango() -> None:
    from api_server.cortex.identity import effective_mood_baseline

    pad = effective_mood_baseline(
        {"mood_baseline": {"valence": 3.0, "arousal": 0.9, "dominance": -7.0}}
    )
    assert pad.valence == 1.0
    assert pad.arousal == 0.9
    assert pad.dominance == -1.0


# ---------------------------------------------------------------------------
# apply_owner_model_delta — merge acotado del "lo que sé de mi owner"
# ---------------------------------------------------------------------------
def test_owner_model_merge_anade_y_actualiza() -> None:
    from api_server.cortex.identity import apply_owner_model_delta

    current = default_identity_state()
    current["relationship_model"] = {"prefiere": "brevedad"}
    out = apply_owner_model_delta(current, {"prefiere": "evidencia", "stack": "python"})
    assert out["relationship_model"]["prefiere"] == "evidencia"
    assert out["relationship_model"]["stack"] == "python"
    # el resto del estado se preserva.
    assert out["name"] == current["name"]
    assert out["traits"] == current["traits"]


def test_owner_model_valor_vacio_borra_la_clave() -> None:
    from api_server.cortex.identity import apply_owner_model_delta

    current = default_identity_state()
    current["relationship_model"] = {"obsoleto": "ya no aplica", "prefiere": "TDD"}
    out = apply_owner_model_delta(current, {"obsoleto": ""})
    assert "obsoleto" not in out["relationship_model"]
    assert out["relationship_model"]["prefiere"] == "TDD"


def test_owner_model_trunca_valores_largos() -> None:
    from api_server.cortex.identity import apply_owner_model_delta

    out = apply_owner_model_delta(default_identity_state(), {"nota": "x" * 500})
    assert len(out["relationship_model"]["nota"]) <= 280


def test_owner_model_cap_de_claves_prioriza_existentes() -> None:
    from api_server.cortex.identity import apply_owner_model_delta

    current = default_identity_state()
    current["relationship_model"] = {"a": "1", "b": "2", "c": "3"}
    out = apply_owner_model_delta(current, {"b": "2bis", "d": "4", "e": "5"}, max_keys=3)
    rel = out["relationship_model"]
    # las existentes (actualizadas) sobreviven; las nuevas no caben en el cap.
    assert set(rel) == {"a", "b", "c"}
    assert rel["b"] == "2bis"


def test_owner_model_propuesta_invalida_es_noop() -> None:
    from api_server.cortex.identity import apply_owner_model_delta

    current = default_identity_state()
    current["relationship_model"] = {"prefiere": "TDD"}
    out = apply_owner_model_delta(current, None)
    assert out["relationship_model"] == {"prefiere": "TDD"}
    out2 = apply_owner_model_delta(current, "no soy un dict")  # type: ignore[arg-type]
    assert out2["relationship_model"] == {"prefiere": "TDD"}


def test_owner_model_no_muta_el_input() -> None:
    from api_server.cortex.identity import apply_owner_model_delta

    current = default_identity_state()
    current["relationship_model"] = {"prefiere": "TDD"}
    apply_owner_model_delta(current, {"prefiere": "otra cosa", "extra": "x"})
    assert current["relationship_model"] == {"prefiere": "TDD"}


# ---------------------------------------------------------------------------
# compute_diff — la auditoría del cambio (el criterio del plan que faltaba)
# ---------------------------------------------------------------------------
# El plan pedía aquí «compute_diff ignora campos sin cambio» y este fichero ni
# importaba la función (auditoría 2026-07-27): la única aserción del diff vivía en
# integración y era `'name' not in diff_v2`. El diff es lo que se persiste en
# ``cortex_identity_history.diff``, o sea la ÚNICA traza de qué tocó la reflexión;
# si emitiera campos que no cambiaron, cada versión parecería reescribir la
# identidad entera y la auditoría no serviría para nada.
def test_compute_diff_ignora_los_campos_sin_cambio() -> None:
    before = default_identity_state()
    after = default_identity_state()
    after["narrative"] = "He aprendido algo nuevo."
    diff = compute_diff(before, after)
    # SOLO el campo que cambió.
    assert set(diff) == {"narrative"}
    assert diff["narrative"] == {"before": "", "after": "He aprendido algo nuevo."}


def test_compute_diff_de_estados_identicos_es_vacio() -> None:
    state = default_identity_state()
    # Una pasada que no mueve nada NO debe ensuciar el histórico con un diff.
    assert compute_diff(state, dict(state)) == {}


def test_compute_diff_detecta_un_cambio_dentro_de_un_dict_anidado() -> None:
    """``traits``/``mood_baseline`` son dicts: el diff los compara por valor y emite
    el bloque completo. Si comparara por identidad de objeto (``is``) o solo mirara
    las claves de primer nivel por presencia, un cambio de rasgo pasaría invisible."""
    before = default_identity_state()
    after = default_identity_state()
    after["traits"] = {**after["traits"], "openness": 0.55}
    diff = compute_diff(before, after)
    assert set(diff) == {"traits"}
    assert diff["traits"]["before"]["openness"] == 0.5
    assert diff["traits"]["after"]["openness"] == 0.55


def test_compute_diff_marca_campo_nuevo_y_campo_retirado() -> None:
    diff = compute_diff({"name": "Córtex", "obsoleto": "x"}, {"name": "Córtex", "nuevo": "y"})
    # Un campo añadido entra con before=None; uno retirado, con after=None.
    assert diff["nuevo"] == {"before": None, "after": "y"}
    assert diff["obsoleto"] == {"before": "x", "after": None}
    # y el que no cambió no aparece.
    assert "name" not in diff


def test_compute_diff_no_muta_los_estados_que_compara() -> None:
    before = {"name": "Córtex", "traits": {"openness": 0.5}}
    after = {"name": "Atlas", "traits": {"openness": 0.5}}
    compute_diff(before, after)
    assert before == {"name": "Córtex", "traits": {"openness": 0.5}}
    assert after == {"name": "Atlas", "traits": {"openness": 0.5}}
