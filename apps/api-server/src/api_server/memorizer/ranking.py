"""Ranking que aprende (task_mem_10): utilidad, frescura y `disputed` sobre la fusión RRF.

El recall fusiona BM25 + vectorial + entidades con RRF (``recall.fuse_rankings``).
Este módulo añade el cuarto término, que mira a la memoria y no a la consulta:

  - **utilidad**: ``(useful - harmful)`` con suavizado, en ``(-1, 1)``. Un voto no es
    un veredicto: con ``smoothing = 2`` un solo ``useful`` vale 1/3, diez valen 5/6.
  - **frescura**: decay exponencial (vida media ``half_life_days``) sobre la última
    recuperación o, si nunca se recuperó, la creación. **Sólo** las ``episodic``
    decaen; ``semantic`` y los resúmenes de ejecución (``kind = summary``) no. Es una
    penalización (``0`` para lo fresco, hasta ``-freshness`` para lo rancio), nunca un
    bonus: así una ``semantic`` no gana a una ``episodic`` de ayer sólo por existir.
  - **disputed**: una memoria marcada ``wrong``/``outdated`` baja ``disputed`` unidades.

Los pesos se expresan en **unidades de RRF** (``1 / (k + 1)``, lo que aporta un primer
puesto en UNA lista): ``utility = 1.0`` significa «la utilidad máxima pesa lo que un
primer puesto de BM25». Así los pesos no dependen de ``k`` ni del número de listas y
son legibles para un operador. ``RankWeights.off()`` (todo a cero) es el ranking
anterior a esta tarea, byte a byte.

Puro y sin base de datos: el I/O vive en ``recall.recall``; los pesos, en
``platform_settings`` (``memory.rank_*``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

# `recall.RRF_K_DEFAULT`: no se importa `recall` para no crear un ciclo (recall importa
# este módulo). Si cambia allá, `test_memory_ranking` no lo detecta: la unidad de RRF
# sólo depende de k, y k es el de la fusión que llama.
_RRF_K = 60

#: `kind` cuyas memorias no caducan aunque sean `episodic` (el resumen de una ejecución).
_NO_DECAY_KINDS = frozenset({"summary"})


@dataclass(frozen=True)
class RankWeights:
    """Pesos del cuarto término, en unidades de RRF. Todos ``>= 0``."""

    utility: float = 0.0
    freshness: float = 0.0
    half_life_days: float = 60.0
    disputed: float = 0.0
    smoothing: float = 2.0

    @classmethod
    def off(cls) -> RankWeights:
        """Sin aprendizaje: el orden es el de la fusión RRF pura."""
        return cls()


#: Defaults MEDIDOS en el eval sintético de `tests/unit/test_memory_rank_eval.py`: la rejilla
#: (`grid_search`) maximiza el MRR del mundo informativo sujeto a no empeorar más de 0,03 el del
#: mundo de ruido puro; entre las mejores (MRR 0,552) se eligió la que lleva `disputed > 0`
#: (MRR 0,551), porque el producto exige que una memoria marcada errónea CAIGA de verdad: el eval
#: no puede medir ese término por separado, ya que `disputed` coincide con `harmful_count > 0`.
#: Los pesos son pequeños a propósito: las puntuaciones RRF están muy comprimidas (del rango 1 al
#: 10 de una lista sólo hay 0,13 unidades), así que un peso de 1.0 ya reordena casi todo.
DEFAULT_RANK_WEIGHTS = RankWeights(
    utility=0.15,
    freshness=0.05,
    half_life_days=60.0,
    disputed=1.0,
    smoothing=2.0,
)


@dataclass(frozen=True)
class MemorySignals:
    """Lo que el ranking mira de una memoria (columnas de ``memory_entries``)."""

    type: str
    kind: str | None
    useful_count: int
    harmful_count: int
    disputed: bool
    #: Días desde la última recuperación (o desde la creación si nunca se recuperó).
    age_days: float


def utility_score(useful: int, harmful: int, *, smoothing: float) -> float:
    """``(useful - harmful) / (useful + harmful + smoothing)`` — antisimétrica, en (-1, 1)."""
    denom = max(0, useful) + max(0, harmful) + max(smoothing, 1e-9)
    return (max(0, useful) - max(0, harmful)) / denom


def freshness_factor(signals: MemorySignals, *, half_life_days: float) -> float:
    """1.0 = sin decay; ``0.5 ** (edad / vida media)`` para una ``episodic`` normal.

    ``semantic`` y ``kind = summary`` devuelven siempre 1.0. Una fecha futura (reloj
    desfasado) no da bonus: se trata como edad 0."""
    if signals.type != "episodic" or signals.kind in _NO_DECAY_KINDS:
        return 1.0
    age = max(0.0, signals.age_days)
    return float(0.5 ** (age / max(half_life_days, 1e-9)))


def adjust_score(
    base: float, signals: MemorySignals, weights: RankWeights, *, k: int = _RRF_K
) -> float:
    """El score final: fusión RRF + utilidad - decay - penalización por `disputed`."""
    unit = 1.0 / (k + 1)
    bonus = weights.utility * utility_score(
        signals.useful_count, signals.harmful_count, smoothing=weights.smoothing
    )
    decay = weights.freshness * (
        1.0 - freshness_factor(signals, half_life_days=weights.half_life_days)
    )
    dispute = weights.disputed if signals.disputed else 0.0
    return base + unit * (bonus - decay - dispute)


_NEUTRAL = MemorySignals(
    type="semantic", kind=None, useful_count=0, harmful_count=0, disputed=False, age_days=0.0
)


def rank_fused(
    fused: Mapping[UUID, tuple[float, int | None, int | None, int | None]],
    signals: Mapping[UUID, MemorySignals],
    weights: RankWeights,
    *,
    k: int = _RRF_K,
) -> list[tuple[UUID, float]]:
    """``[(memory_id, score)]`` de mayor a menor. Una memoria sin señales (fila que
    desapareció entre consultas) cuenta como neutra; el desempate es por id, estable."""
    scored = [
        (mid, adjust_score(entry[0], signals.get(mid, _NEUTRAL), weights, k=k))
        for mid, entry in fused.items()
    ]
    scored.sort(key=lambda item: (-item[1], str(item[0])))
    return scored


__all__ = [
    "DEFAULT_RANK_WEIGHTS",
    "MemorySignals",
    "RankWeights",
    "adjust_score",
    "freshness_factor",
    "rank_fused",
    "utility_score",
]
