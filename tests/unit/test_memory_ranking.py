"""task_mem_10 — el ranking que aprende: utilidad, frescura y `disputed`.

Unit puro (sin base de datos): `memorizer.ranking` toma los rangos de la fusión RRF
y las señales de cada memoria y devuelve el orden final. Lo que fija:

  - con señales neutras (o con los pesos a cero) el orden es el de la fusión;
  - una memoria marcada `wrong`/`outdated` (disputada, con `harmful_count`) cae;
  - una memoria con votos `useful` sube, y con suavizado: un voto no es un veredicto;
  - el decay de frescura sólo afecta a `episodic`; `semantic` y `summary` no decaen;
  - los pesos viven en `platform_settings` (`memory.rank_*`, decimal) y se acotan.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from api_server.memorizer.ranking import (
    DEFAULT_RANK_WEIGHTS,
    MemorySignals,
    RankWeights,
    adjust_score,
    freshness_factor,
    rank_fused,
    utility_score,
)
from api_server.memorizer.recall import fuse_rankings, rrf_score

_ON = RankWeights(utility=1.0, freshness=1.0, half_life_days=30.0, disputed=2.0, smoothing=2.0)


def _sig(**kw: object) -> MemorySignals:
    base: dict[str, object] = {
        "type": "semantic",
        "kind": None,
        "useful_count": 0,
        "harmful_count": 0,
        "disputed": False,
        "age_days": 0.0,
    }
    base.update(kw)
    return MemorySignals(**base)  # type: ignore[arg-type]


def test_neutral_signals_leave_the_fused_score_untouched() -> None:
    base = rrf_score(1) + rrf_score(3)
    assert adjust_score(base, _sig(), _ON) == pytest.approx(base)
    assert adjust_score(base, _sig(type="episodic"), _ON) == pytest.approx(base)


def test_weights_at_zero_are_the_pre_learning_ranking() -> None:
    base = rrf_score(2)
    noisy = _sig(useful_count=9, harmful_count=4, disputed=True, type="episodic", age_days=900)
    assert adjust_score(base, noisy, RankWeights.off()) == pytest.approx(base)


def test_a_disputed_memory_scores_below_the_same_memory_undisputed() -> None:
    base = rrf_score(1)
    wrong = _sig(harmful_count=1, disputed=True)
    assert adjust_score(base, wrong, _ON) < adjust_score(base, _sig(), _ON) < base + 1e-9


def test_a_useful_memory_scores_above_a_neutral_one() -> None:
    base = rrf_score(5)
    assert adjust_score(base, _sig(useful_count=3), _ON) > adjust_score(base, _sig(), _ON)


def test_utility_is_smoothed_so_one_vote_is_not_a_verdict() -> None:
    one = utility_score(1, 0, smoothing=2.0)
    ten = utility_score(10, 0, smoothing=2.0)
    assert 0 < one < ten < 1
    assert utility_score(0, 0, smoothing=2.0) == 0
    assert utility_score(0, 3, smoothing=2.0) < 0
    # antisimétrica: los mismos votos a favor y en contra se anulan
    assert utility_score(4, 4, smoothing=2.0) == 0


def test_freshness_decays_only_episodic_memories() -> None:
    old = 400.0
    episodic = freshness_factor(_sig(type="episodic", age_days=old), half_life_days=30.0)
    semantic = freshness_factor(_sig(type="semantic", age_days=old), half_life_days=30.0)
    summary = freshness_factor(
        _sig(type="episodic", kind="summary", age_days=old), half_life_days=30.0
    )
    assert episodic < 0.01
    assert semantic == 1.0
    assert summary == 1.0  # un resumen de ejecución tampoco caduca


def test_freshness_halves_every_half_life_and_never_rewards() -> None:
    at_0 = freshness_factor(_sig(type="episodic", age_days=0), half_life_days=30.0)
    at_30 = freshness_factor(_sig(type="episodic", age_days=30), half_life_days=30.0)
    at_60 = freshness_factor(_sig(type="episodic", age_days=60), half_life_days=30.0)
    assert at_0 == 1.0
    assert at_30 == pytest.approx(0.5)
    assert at_60 == pytest.approx(0.25)
    # un dato de fecha en el futuro (reloj desfasado) no da bonus
    assert freshness_factor(_sig(type="episodic", age_days=-5), half_life_days=30.0) == 1.0


def test_the_decay_does_not_move_a_semantic_memory_in_the_ranking() -> None:
    base = rrf_score(1)
    fresh = adjust_score(base, _sig(type="semantic", age_days=0), _ON)
    ancient = adjust_score(base, _sig(type="semantic", age_days=3650), _ON)
    assert fresh == pytest.approx(ancient)
    stale_episodic = adjust_score(base, _sig(type="episodic", age_days=3650), _ON)
    assert stale_episodic < ancient


def test_rank_fused_drops_the_wrong_memory_and_lifts_the_useful_one() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    # a gana la fusión (rango 1 en las tres listas), luego b, luego c.
    fused = fuse_rankings([a, b, c], [a, b, c], [a, b, c])
    signals = {
        a: _sig(harmful_count=2, disputed=True),  # marcada como errónea
        b: _sig(),
        c: _sig(useful_count=6),  # ha servido muchas veces
    }
    assert [m for m, _ in rank_fused(fused, signals, RankWeights.off())] == [a, b, c]
    order = [m for m, _ in rank_fused(fused, signals, _ON)]
    assert order.index(a) > order.index(b)  # la errónea cae por debajo de la neutra
    assert order.index(c) < order.index(b)  # la útil sube por encima de la neutra


def test_rank_fused_is_stable_for_ties_and_tolerates_missing_signals() -> None:
    a, b = uuid4(), uuid4()
    fused = fuse_rankings([a, b], [], [])
    # sin señales de `b` (fila desaparecida entre consultas) no rompe: cuenta como neutra
    out = rank_fused(fused, {a: _sig()}, _ON)
    assert [m for m, _ in out] == [a, b]


def test_defaults_are_measured_positive_and_bounded() -> None:
    w = DEFAULT_RANK_WEIGHTS
    assert w.utility > 0 and w.freshness > 0 and w.disputed > 0
    assert w.half_life_days >= 7 and w.smoothing > 0
    # el peso de `disputed` debe poder bajar una memoria que casa por las tres vías
    all_three = fuse_rankings([uuid4()], [], [])  # cota inferior: una lista
    assert all_three
    assert w.disputed >= 1.0


# ---------------------------------------------------------------------------
# platform_settings: `memory.rank_*` (decimal) con cotas
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rank_weights_come_from_platform_settings_and_are_clamped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_server.db import platform_settings as ps

    store: dict[str, object] = {}

    async def fake_get(session: object, key: str, *, default: object = None) -> object:
        return store.get(key, default)

    monkeypatch.setattr(ps, "get_platform_setting", fake_get)

    # sin filas: los defaults medidos
    assert await ps.get_memory_rank_weights(object()) == DEFAULT_RANK_WEIGHTS  # type: ignore[arg-type]

    store.update(
        {
            "memory.rank_utility_weight": 0.5,
            "memory.rank_freshness_weight": "0.25",  # decimal como texto también vale
            "memory.rank_freshness_half_life_days": 3650,  # fuera de cota → se recorta
            "memory.rank_disputed_penalty": -3,  # negativo → 0 (nunca premia)
            "memory.rank_utility_smoothing": "basura",  # ilegible → default
        }
    )
    w = await ps.get_memory_rank_weights(object())  # type: ignore[arg-type]
    assert w.utility == 0.5
    assert w.freshness == 0.25
    assert w.half_life_days == ps.MEMORY_RANK_HALF_LIFE_DAYS_MAX
    assert w.disputed == 0.0
    assert w.smoothing == DEFAULT_RANK_WEIGHTS.smoothing

    store["memory.archive_after_days"] = 1  # por debajo del mínimo
    assert await ps.get_memory_archive_after_days(object()) == ps.MEMORY_ARCHIVE_AFTER_DAYS_MIN  # type: ignore[arg-type]
    store["memory.archive_after_days"] = "nope"
    assert await ps.get_memory_archive_after_days(object()) == 90  # type: ignore[arg-type]
