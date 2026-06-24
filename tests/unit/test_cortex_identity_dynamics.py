"""Córtex F3 (bloque 2) — capa pura de mutación de identidad (sin LLM/red/DB).

Cubre los helpers deterministas que la reflexión usa para reescribir la identidad
de forma ACOTADA y versionada (ADR 0074: guardrail de auto-modificación):

  * ``clamp_traits`` / ``clamp_baseline`` — recortan a los rangos canónicos.
  * ``bounded_update`` — limita |Δ| por ciclo (no más de ``max_delta_per_cycle``).
  * ``editable_owner_state`` — el override del owner SOLO toca name/core_values/
    narrative/language/learning_goals; jamás traits/mood_baseline/relationship_model.
  * ``apply_reflection_delta`` — compone clamp+bounded sobre traits/baseline +
    reescribe narrative, devolviendo el nuevo ``identity_state`` (puro).
"""

from __future__ import annotations

from api_server.cortex.identity import (
    apply_reflection_delta,
    bounded_update,
    clamp_baseline,
    clamp_traits,
    default_identity_state,
    editable_owner_state,
)


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
