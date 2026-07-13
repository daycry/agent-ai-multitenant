"""Consolidación merge-into de la episódica del córtex (ADR 0077).

El olvido (``forgetting.py``) retira lo irrelevante; la consolidación fusiona
lo REPETIDO: grupos de recuerdos episódicos muy similares (coseno de sus
embeddings ya calculados ≥ umbral) colapsan en UNA memoria resumida que los
referencia, y los originales se soft-borran con
``metadata_.consolidated_into`` (reversible, mismo contrato que el olvido).

Este módulo es la lógica PURA (agrupar + fusionar texto), determinista y sin
BD/LLM — el worker ``cortex_maintenance`` la aplica gated por el kill-switch
``cortex.autonomy_enabled``. Deliberadamente sin destilador LLM: un resumen
determinista que CITA los originales no puede alucinar; si algún día se
quiere prosa sintetizada, que sea una mejora aparte y medida.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

# Similitud mínima (coseno) para considerar dos recuerdos "el mismo tema".
CONSOLIDATION_SIMILARITY = 0.90
# Un grupo consolida solo si junta al menos N recuerdos (2 no compensa).
CONSOLIDATION_MIN_GROUP = 3
# Presupuesto del contenido fusionado y de cada extracto citado.
_MERGED_CONTENT_CAP = 2000
_EXCERPT_CAP = 220


@dataclass(frozen=True)
class ConsolidationCandidate:
    """Vista mínima de una memoria episódica candidata (id + texto + vector)."""

    id: str
    content: str
    created_at: datetime
    embedding: list[float]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def select_consolidation_groups(
    candidates: list[ConsolidationCandidate],
    *,
    similarity: float = CONSOLIDATION_SIMILARITY,
    min_group: int = CONSOLIDATION_MIN_GROUP,
) -> list[list[ConsolidationCandidate]]:
    """Grupos de recuerdos consolidables (greedy, determinista).

    Cada candidato sin grupo abre uno; los restantes se unen al primer grupo
    cuyo SEMILLA supere el umbral de coseno (greedy por orden de entrada —
    estable y barato; no es clustering óptimo y no lo necesita). Los grupos
    con menos de ``min_group`` miembros se descartan. Candidatos sin
    embedding no participan (no hay señal de similitud honesta)."""
    usable = [c for c in candidates if c.embedding]
    groups: list[list[ConsolidationCandidate]] = []
    seeded: set[str] = set()
    for candidate in usable:
        if candidate.id in seeded:
            continue
        group = [candidate]
        seeded.add(candidate.id)
        for other in usable:
            if other.id in seeded:
                continue
            if _cosine(candidate.embedding, other.embedding) >= similarity:
                group.append(other)
                seeded.add(other.id)
        if len(group) >= min_group:
            groups.append(group)
    return groups


def merge_content(group: list[ConsolidationCandidate]) -> str:
    """El contenido de la memoria consolidada: un índice honesto que CITA los
    recuerdos originales (fechas + extracto), nunca prosa inventada."""
    ordered = sorted(group, key=lambda c: c.created_at)
    first = ordered[0].created_at.date().isoformat()
    last = ordered[-1].created_at.date().isoformat()
    lines = [
        f"[consolidado] {len(ordered)} recuerdos similares ({first} → {last}):",
    ]
    for candidate in ordered:
        excerpt = " ".join(candidate.content.split())[:_EXCERPT_CAP]
        lines.append(f"- ({candidate.created_at.date().isoformat()}) {excerpt}")
    merged = "\n".join(lines)
    return merged[:_MERGED_CONTENT_CAP]


__all__ = [
    "CONSOLIDATION_MIN_GROUP",
    "CONSOLIDATION_SIMILARITY",
    "ConsolidationCandidate",
    "merge_content",
    "select_consolidation_groups",
]
