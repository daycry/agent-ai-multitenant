"""Córtex F4 — política de olvido pura (ADR 0077): scoring + protección.

La identidad y el "owner model" NUNCA se auto-olvidan; solo la episódica de BAJA
retención es candidata a soft-delete. El ``retention_score`` es determinista.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
