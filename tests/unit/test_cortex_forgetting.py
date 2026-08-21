"""Córtex F4 — política de olvido pura (ADR 0077): scoring + protección.

La identidad y el "owner model" NUNCA se auto-olvidan; solo la episódica de BAJA
retención es candidata a soft-delete. El ``retention_score`` es determinista.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from api_server.cortex.forgetting import (
    DEFAULT_RETENTION_FORGET_THRESHOLD,
    decide_forget,
    is_protected,
    recency_factor,
    retention_score,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)


def test_recency_factor_fresh_is_high_old_is_low() -> None:
    fresh = recency_factor(_NOW, _NOW)
    assert fresh == pytest.approx(1.0)
    # 30 días == una vida media → ~0.5.
    half = recency_factor(_NOW - timedelta(days=30), _NOW)
    assert half == pytest.approx(0.5, abs=0.01)
    # 120 días → muy bajo.
    old = recency_factor(_NOW - timedelta(days=120), _NOW)
    assert old < 0.1


def test_retention_score_is_product_of_factors() -> None:
    # importance 0.5 (default) × recency ~0.5 (30d) × freq 1.0 ≈ 0.25.
    score = retention_score(
        created_at=_NOW - timedelta(days=30), now=_NOW, metadata={}, recall_frequency=1.0
    )
    assert score == pytest.approx(0.25, abs=0.02)


def test_identity_and_owner_model_are_protected() -> None:
    assert is_protected({"kind": "identity"}) is True
    assert is_protected({"kind": "owner_model"}) is True
    assert is_protected({"kind": "reflection"}) is True
    assert is_protected({"kind": "learning"}) is True
    assert is_protected({"kind": "episodic_event"}) is False
    assert is_protected({}) is False


def test_protected_memory_never_forgotten_even_if_ancient() -> None:
    # Una identidad de hace 10 años NO se olvida.
    d = decide_forget(
        created_at=_NOW - timedelta(days=3650),
        now=_NOW,
        metadata={"kind": "identity"},
        memory_type="semantic",
    )
    assert d.forget is False
    assert d.reason == "protected_kind"


def test_semantic_episodic_only_episodic_is_candidate() -> None:
    # Una semántica vieja NO es candidata (solo destila reglas duraderas).
    d_sem = decide_forget(
        created_at=_NOW - timedelta(days=365), now=_NOW, metadata={}, memory_type="semantic"
    )
    assert d_sem.forget is False
    assert d_sem.reason == "not_episodic"


def test_low_retention_episodic_is_forgotten() -> None:
    # Episódica vieja + sin protección + score bajo → olvidar.
    d = decide_forget(
        created_at=_NOW - timedelta(days=365),
        now=_NOW,
        metadata={"importance": 0.4},
        memory_type="episodic",
        threshold=DEFAULT_RETENTION_FORGET_THRESHOLD,
    )
    assert d.forget is True
    assert d.reason == "low_retention"
    assert d.score < DEFAULT_RETENTION_FORGET_THRESHOLD


def test_recent_episodic_is_retained() -> None:
    d = decide_forget(
        created_at=_NOW - timedelta(days=1),
        now=_NOW,
        metadata={"importance": 0.8},
        memory_type="episodic",
    )
    assert d.forget is False
    assert d.reason == "retained"


# ---------------------------------------------------------------------------
# recall_frequency_factor — uso real de la memoria en la retención (ADR 0077)
# ---------------------------------------------------------------------------
def test_recall_frequency_factor_curva_con_suelo() -> None:
    from api_server.cortex.forgetting import recall_frequency_factor

    # Suelo 0.5: una memoria jamás recallada no queda automáticamente condenada.
    assert recall_frequency_factor(0) == 0.5
    # Satura en 1.0 a partir de RECALL_COUNT_SATURATION.
    assert recall_frequency_factor(5) == 1.0
    assert recall_frequency_factor(50) == 1.0
    # Monótona entre medias.
    assert 0.5 < recall_frequency_factor(2) < 1.0
    # Tolerante: negativos/sucios caen al suelo (nunca lanza).
    assert recall_frequency_factor(-3) == 0.5


def test_fresca_sin_recalls_se_retiene_pese_al_suelo() -> None:
    from datetime import UTC, datetime

    from api_server.cortex.forgetting import decide_forget, recall_frequency_factor

    now = datetime.now(UTC)
    decision = decide_forget(
        created_at=now,
        now=now,
        metadata={"cortex": True},
        memory_type="episodic",
        recall_frequency=recall_frequency_factor(0),
    )
    # importance 0.5 * recency 1.0 * freq 0.5 = 0.25 > umbral 0.1 ⇒ retenida.
    assert decision.forget is False
    assert decision.score > 0.1


def test_vieja_no_recallada_cae_y_recallada_se_salva() -> None:
    from datetime import UTC, datetime, timedelta

    from api_server.cortex.forgetting import decide_forget, recall_frequency_factor

    now = datetime.now(UTC)
    created = now - timedelta(days=45)

    nunca_recallada = decide_forget(
        created_at=created,
        now=now,
        metadata={"cortex": True},
        memory_type="episodic",
        recall_frequency=recall_frequency_factor(0),
    )
    assert nunca_recallada.forget is True

    recallada = decide_forget(
        created_at=created,
        now=now,
        metadata={"cortex": True, "recall_count": 5},
        memory_type="episodic",
        recall_frequency=recall_frequency_factor(5),
    )
    assert recallada.forget is False


# ---------------------------------------------------------------------------
# Monotonía del `retention_score` — criterio de aceptación D1 del plan
# cortex-f5: «score monótono respecto a recencia / frecuencia / intensidad».
#
# Ningún test de arriba lo demuestra: los que comparan una memoria vieja con una
# fresca mueven DOS variables a la vez (365 días + importancia 0.4 frente a 1 día
# + importancia 0.8), así que fijan casos concretos del veredicto, no la
# monotonía de ningún factor. Los de abajo barren UNA dimensión dejando las otras
# FIJAS, que es lo que atrapa el defecto que importa: un signo invertido, una
# ganancia a cero o un factor que se cuela sin efecto en el producto.
# ---------------------------------------------------------------------------
def test_score_estrictamente_decreciente_en_la_edad() -> None:
    """A igual importancia y frecuencia, más vieja ⇒ menos retención.

    Si la recencia se ignorase (o entrase con el signo cambiado), el barrido de
    edad daría una serie plana o creciente y el olvido enterraría lo reciente en
    vez de lo rancio.
    """
    scores = [
        retention_score(
            created_at=_NOW - timedelta(days=days),
            now=_NOW,
            metadata={"importance": 0.6},
            recall_frequency=1.0,
        )
        for days in (0, 1, 7, 30, 90, 365)
    ]
    assert all(b < a for a, b in pairwise(scores)), scores


def test_score_estrictamente_creciente_en_la_frecuencia_de_recall() -> None:
    """A igual edad e importancia, más recalls ⇒ más retención (hasta saturar).

    `test_recall_frequency_factor_curva_con_suelo` verifica la curva del FACTOR;
    esto verifica que el factor llega al SCORE de verdad — si `retention_score`
    dejara de multiplicarlo (o lo clampease a 1 por error), la curva seguiría
    verde y el score sería ciego al uso real de la memoria.
    """
    from api_server.cortex.forgetting import recall_frequency_factor

    def _score(count: int) -> float:
        return retention_score(
            created_at=_NOW - timedelta(days=45),
            now=_NOW,
            metadata={"importance": 0.6},
            recall_frequency=recall_frequency_factor(count),
        )

    scores = [_score(c) for c in (0, 1, 2, 3, 4, 5)]
    assert all(b > a for a, b in pairwise(scores)), scores
    # Saturación: pasados RECALL_COUNT_SATURATION recalls el score ya no sube.
    assert _score(50) == pytest.approx(_score(5))


def test_score_estrictamente_creciente_en_la_importancia() -> None:
    """A igual edad y frecuencia, más importante ⇒ más retención.

    `importance` y la intensidad emocional son dimensiones DISTINTAS con
    productores distintos (`metadata_.importance` lo fija quien crea la memoria;
    `metadata_.emotion.intensity` lo escribe el distilador afectivo de F2, ver
    `workers/cortex_affect.py::_persist_emotional_episode`). El criterio D1 pide
    monotonía en la segunda; este test protege la primera de romperse al añadirla.
    """
    scores = [
        retention_score(
            created_at=_NOW - timedelta(days=10),
            now=_NOW,
            metadata={"importance": imp},
            recall_frequency=1.0,
        )
        for imp in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    assert all(b > a for a, b in pairwise(scores)), scores


# ---------------------------------------------------------------------------
# D1 (a) — intensidad EMOCIONAL: `metadata_.emotion.intensity`
#
# El criterio D1 del plan enumera tres dimensiones (recencia / frecuencia /
# **intensidad emocional**) y la tercera no existía: el módulo puntuaba
# `metadata_.importance`, que es otro dato con otro productor. El productor real
# de la intensidad es el distilador afectivo de F2, que escribe
# `metadata_.emotion = {valence, arousal, dominance, intensity, mood_label, …}`
# en cada episódica del córtex — justo las filas que barre el sweep de olvido.
# ---------------------------------------------------------------------------
def test_emotion_intensity_of_lee_el_bloque_del_distilador_y_tolera_basura() -> None:
    """El lector de la intensidad: presente, ausente, y sucia.

    Sin el default 0.0 para el bloque ausente, TODA memoria sin emoción (las que
    escriben `cortex_remember`, la reflexión y el aprendizaje) cambiaría de score
    al introducir la dimensión.
    """
    from api_server.cortex.forgetting import emotion_intensity_of

    assert emotion_intensity_of({"emotion": {"intensity": 0.8}}) == pytest.approx(0.8)
    # Sin bloque emotion (o sin la clave) ⇒ neutro, no penalización.
    assert emotion_intensity_of({}) == 0.0
    assert emotion_intensity_of(None) == 0.0
    assert emotion_intensity_of({"emotion": {}}) == 0.0
    # Fuera de rango se recorta; basura y booleanos caen a 0 (nunca lanza).
    assert emotion_intensity_of({"emotion": {"intensity": 7.0}}) == 1.0
    assert emotion_intensity_of({"emotion": {"intensity": -3.0}}) == 0.0
    assert emotion_intensity_of({"emotion": {"intensity": "mucha"}}) == 0.0
    assert emotion_intensity_of({"emotion": {"intensity": True}}) == 0.0
    # Un `emotion` que no es dict (dato corrupto) no debe romper el barrido.
    assert emotion_intensity_of({"emotion": "alegría"}) == 0.0


def test_score_estrictamente_creciente_en_la_intensidad_emocional() -> None:
    """Barrido de UNA sola variable: la intensidad. Todo lo demás FIJO.

    Es el test que la auditoría echaba en falta: los que comparaban una memoria
    vieja con una fresca movían dos variables a la vez. Aquí la edad, la
    importancia y la frecuencia son constantes, así que si la intensidad no
    entrase en el score (o entrase con el signo cambiado) la serie saldría plana
    o decreciente.

    Sentido de la monotonía (modelo psicológico de ADR 0077): lo que se vivió con
    más carga emocional se olvida MENOS.
    """
    scores = [
        retention_score(
            created_at=_NOW - timedelta(days=45),
            now=_NOW,
            metadata={"importance": 0.6, "emotion": {"intensity": i}},
            recall_frequency=1.0,
        )
        for i in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
    ]
    assert all(b > a for a, b in pairwise(scores)), scores


def test_intensidad_ausente_no_cambia_el_score_historico() -> None:
    """Retrocompatibilidad: sin bloque `emotion`, el score es el de siempre.

    La dimensión se añade como REFUERZO (factor ≥ 1), no como multiplicador que
    pueda condenar a las memorias sin emoción registrada. Si se hubiese cableado
    como `score *= intensity`, una intensidad 0 (el valor que deja el camino
    fail-open del distilador) enterraría la memoria entera al primer barrido.
    """
    sin_emocion = retention_score(
        created_at=_NOW - timedelta(days=30), now=_NOW, metadata={}, recall_frequency=1.0
    )
    intensidad_cero = retention_score(
        created_at=_NOW - timedelta(days=30),
        now=_NOW,
        metadata={"emotion": {"intensity": 0.0}},
        recall_frequency=1.0,
    )
    assert sin_emocion == pytest.approx(0.25, abs=0.02)
    assert intensidad_cero == pytest.approx(sin_emocion)


def test_una_episodica_intensa_no_se_olvida_donde_una_apagada_si() -> None:
    """El efecto observable de la dimensión, en el veredicto y no sólo en el score.

    Dos episódicas idénticas de 60 días, nunca recalladas: la que el córtex vivió
    con intensidad alta sobrevive al barrido y la apagada cae. Sin esto, la
    monotonía podría cumplirse con una ganancia tan pequeña que no cambiase
    ninguna decisión — es decir, código muerto con test verde.
    """
    from api_server.cortex.forgetting import recall_frequency_factor

    def _decide(intensity: float):  # type: ignore[no-untyped-def]
        return decide_forget(
            created_at=_NOW - timedelta(days=60),
            now=_NOW,
            metadata={"cortex": True, "emotion": {"intensity": intensity}},
            memory_type="episodic",
            recall_frequency=recall_frequency_factor(0),
        )

    apagada = _decide(0.0)
    intensa = _decide(1.0)
    assert apagada.forget is True, apagada
    assert intensa.forget is False, intensa


# ---------------------------------------------------------------------------
# D1 (b) — la recencia se ancla al ÚLTIMO RECALL, no sólo a la creación
#
# `cortex/memory.py::_bump_recall_counters` ya escribe
# `metadata_.last_recalled_at` (además de `recall_count`) cada vez que el recall
# usa una memoria, y NADIE lo leía: una memoria de hace dos años recordada ayer
# puntuaba como si nadie la hubiese tocado. El criterio TDD del plan
# («recordada hace poco → score alto») no estaba cubierto: el test que lo
# aproximaba usaba `recall_count`, que es FRECUENCIA, no recencia de acceso.
# ---------------------------------------------------------------------------
def test_retention_anchor_prefiere_el_ultimo_recall_y_tolera_basura() -> None:
    """El ancla de la recencia: último recall si es posterior a la creación."""
    from api_server.cortex.forgetting import retention_anchor

    created = _NOW - timedelta(days=400)
    ayer = _NOW - timedelta(days=1)

    # Con marca de recall válida y posterior, manda el recall.
    assert retention_anchor(created, {"last_recalled_at": ayer.isoformat()}) == ayer
    # Sin marca, la creación.
    assert retention_anchor(created, {}) == created
    assert retention_anchor(created, None) == created
    # Marca ilegible / de otro tipo ⇒ la creación (nunca lanza).
    assert retention_anchor(created, {"last_recalled_at": "ayer por la tarde"}) == created
    assert retention_anchor(created, {"last_recalled_at": 12345}) == created
    # Marca ANTERIOR a la creación (dato corrupto): no puede envejecer la memoria.
    viejo = created - timedelta(days=10)
    assert retention_anchor(created, {"last_recalled_at": viejo.isoformat()}) == created


def test_vieja_pero_recordada_ayer_puntua_alto_y_se_salva() -> None:
    """El caso TDD que el plan exigía y no estaba: «recordada hace poco → alto».

    Una episódica de dos años que el owner recordó AYER debe retenerse: el
    `created_at` la condenaba (recencia ≈ 0 tras 730 días) aunque el dato del
    último acceso estuviese escrito en el JSONB desde F1.
    """
    created = _NOW - timedelta(days=730)
    meta = {
        "cortex": True,
        "last_recalled_at": (_NOW - timedelta(days=1)).isoformat(),
    }
    score = retention_score(created_at=created, now=_NOW, metadata=meta, recall_frequency=1.0)
    assert score > DEFAULT_RETENTION_FORGET_THRESHOLD, score

    decision = decide_forget(created_at=created, now=_NOW, metadata=meta, memory_type="episodic")
    assert decision.forget is False, decision
    assert decision.reason == "retained"

    # Y la misma memoria SIN la marca de recall sigue cayendo (el efecto es de la
    # marca, no de haber aflojado el umbral para todos).
    sin_marca = decide_forget(
        created_at=created, now=_NOW, metadata={"cortex": True}, memory_type="episodic"
    )
    assert sin_marca.forget is True, sin_marca


def test_score_estrictamente_decreciente_en_la_antiguedad_del_recall() -> None:
    """Monotonía de la recencia medida sobre el ancla real: más tiempo desde el
    último recall ⇒ menos retención, con la fecha de creación FIJA.

    Complementa a `test_score_estrictamente_decreciente_en_la_edad`, que barre la
    creación: sin este barrido, un ancla que ignorase la marca daría una serie
    plana y nadie se enteraría.
    """
    created = _NOW - timedelta(days=730)
    scores = [
        retention_score(
            created_at=created,
            now=_NOW,
            metadata={"last_recalled_at": (_NOW - timedelta(days=d)).isoformat()},
            recall_frequency=1.0,
        )
        for d in (0, 1, 7, 30, 90)
    ]
    assert all(b < a for a, b in pairwise(scores)), scores


def test_marca_de_recall_naive_no_revienta_el_barrido() -> None:
    """Una marca sin zona horaria se compara igual (no lanza y sigue mandando).

    `_bump_recall_counters` escribe UTC aware, pero una fila migrada o un JSONB
    tocado a mano puede traer `"2026-06-23T12:00:00"` sin offset — y comparar
    naïve con aware lanza `TypeError`. Como el sweep corre dentro de un
    `try/except` que traga, el fallo NO se vería: el barrido devolvería 0 memorias
    olvidadas para siempre, en silencio. De ahí que esto se pruebe explícitamente.
    """
    from api_server.cortex.forgetting import retention_anchor

    created = _NOW - timedelta(days=400)
    naive_recent = (_NOW - timedelta(days=1)).replace(tzinfo=None)
    assert retention_anchor(created, {"last_recalled_at": naive_recent.isoformat()}) is not created

    score = retention_score(
        created_at=created,
        now=_NOW,
        metadata={"last_recalled_at": naive_recent.isoformat()},
        recall_frequency=1.0,
    )
    assert score > DEFAULT_RETENTION_FORGET_THRESHOLD, score

    # Y el simétrico: created_at naïve contra un now aware.
    assert recency_factor((_NOW - timedelta(days=30)).replace(tzinfo=None), _NOW) == pytest.approx(
        0.5, abs=0.01
    )


def test_los_lectores_de_metadata_toleran_tipos_imposibles() -> None:
    """Las guardas de tolerancia de `importance_of` / `recall_frequency_factor`
    se ejecutan de verdad.

    Ninguna de las dos ramas `except` tenía test: el barrido corre dentro de un
    `try/except` que traga, así que un `TypeError` aquí se manifestaría como "no
    se olvida nada, nunca" sin un solo error en los logs de la tarea.
    """
    from api_server.cortex.forgetting import (
        DEFAULT_IMPORTANCE,
        RECALL_FREQUENCY_FLOOR,
        importance_of,
        recall_frequency_factor,
    )

    assert importance_of({"importance": ["no", "soy", "un", "float"]}) == DEFAULT_IMPORTANCE
    assert importance_of({"importance": "mucho"}) == DEFAULT_IMPORTANCE
    assert recall_frequency_factor(["tampoco"]) == RECALL_FREQUENCY_FLOOR
    assert recall_frequency_factor("varias") == RECALL_FREQUENCY_FLOOR


def test_la_proteccion_dura_sigue_por_encima_del_ancla_y_de_la_intensidad() -> None:
    """Ni la intensidad ni la marca de recall pueden ABRIR la puerta del olvido a
    un kind protegido — ni su ausencia CERRARLA.

    Las dos dimensiones nuevas cambian el comportamiento del olvido, así que el
    invariante NO negociable de ADR 0077 se re-afirma aquí explícitamente contra
    los datos nuevos: una identidad de hace 10 años, sin emoción y sin recall,
    sigue intocable.
    """
    for meta in (
        {"kind": "identity"},
        {"kind": "owner_model", "emotion": {"intensity": 0.0}},
        {"kind": "reflection", "last_recalled_at": "basura"},
        {"kind": "learning", "emotion": {"intensity": 1.0}},
    ):
        d = decide_forget(
            created_at=_NOW - timedelta(days=3650),
            now=_NOW,
            metadata=meta,
            memory_type="episodic",
        )
        assert d.forget is False, meta
        assert d.reason == "protected_kind", meta
