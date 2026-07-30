"""ADR 0077 — consolidación merge-into de la episódica del córtex.

Lógica PURA (sin BD/LLM, determinista): agrupa memorias episódicas no
protegidas y suficientemente antiguas por similitud coseno de sus embeddings
(umbral alto, greedy) y produce el contenido fusionado que las referencia.
El worker (cortex_maintenance) crea la memoria consolidada y soft-borra las
originales con ``metadata_.consolidated_into`` — mismo patrón reversible que
el olvido.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from api_server.cortex.consolidation import (
    CONSOLIDATION_MIN_GROUP,
    ConsolidationCandidate,
    merge_content,
    select_consolidation_groups,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, tzinfo=UTC)
_OLD = _NOW - timedelta(days=30)


def _cand(mid: str, embedding: list[float], content: str = "x") -> ConsolidationCandidate:
    return ConsolidationCandidate(id=mid, content=content, created_at=_OLD, embedding=embedding)


def test_similar_memories_form_a_group() -> None:
    a = _cand("a", [1.0, 0.0, 0.0], "el owner prefiere REST")
    b = _cand("b", [0.99, 0.05, 0.0], "el owner pidió REST otra vez")
    c = _cand("c", [0.98, 0.08, 0.01], "REST de nuevo en la charla")
    d = _cand("d", [0.0, 1.0, 0.0], "hablamos del clima")
    groups = select_consolidation_groups([a, b, c, d])
    assert len(groups) == 1
    assert {m.id for m in groups[0]} == {"a", "b", "c"}


def test_groups_below_min_size_are_ignored() -> None:
    a = _cand("a", [1.0, 0.0], "uno")
    b = _cand("b", [0.99, 0.01], "dos")  # solo 2 similares < mínimo
    assert CONSOLIDATION_MIN_GROUP >= 3
    assert select_consolidation_groups([a, b]) == []


def test_dissimilar_memories_never_group() -> None:
    a = _cand("a", [1.0, 0.0, 0.0])
    b = _cand("b", [0.0, 1.0, 0.0])
    c = _cand("c", [0.0, 0.0, 1.0])
    assert select_consolidation_groups([a, b, c]) == []


def test_missing_embeddings_are_skipped() -> None:
    a = _cand("a", [], "sin embedding")
    b = _cand("b", [1.0, 0.0], "con embedding")
    assert select_consolidation_groups([a, b]) == []


def test_merge_content_references_sources_and_caps() -> None:
    group = [
        _cand("a", [1.0], "el owner prefiere REST para las APIs públicas"),
        _cand("b", [1.0], "insistió en REST versionado /v1"),
        _cand("c", [1.0], "x" * 500),
    ]
    merged = merge_content(group)
    assert "3" in merged  # cuántos recuerdos consolida
    assert "REST para las APIs" in merged
    assert "/v1" in merged
    # Cap del contenido total (presupuesto de memoria, no un volcado).
    assert len(merged) <= 2200
