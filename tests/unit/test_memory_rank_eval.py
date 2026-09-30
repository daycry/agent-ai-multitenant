"""task_mem_10 — eval de recall ANTES / DESPUÉS del ranking que aprende.

**Esto NO es el dataset dorado del Plan 14.** Ese dataset (`api_server.evals`, `db/evals.py`)
califica versiones de prompt de agente con un juez LLM sobre ejecuciones; no contiene consultas de
recall de memoria con su respuesta esperada ni se puede correr sin el stack vivo (Postgres +
juez). Por eso el eval es un conjunto SINTÉTICO, sembrado y reproducible (`random.Random(seed)`),
sin base de datos:

  - cada consulta tiene un pool de 12 memorias candidatas y UNA respuesta esperada («gold»);
    400 consultas, semilla fija;
  - el recuperador base (BM25 + vectorial + entidades) es RUIDOSO: cada vía puntúa con ruido
    gaussiano y sólo devuelve las 10 mejores, que es lo que hace `fuse_rankings` con sus listas;
  - los metadatos que el ranking mira (votos, `disputed`, tipo, edad) se generan en DOS mundos:
      * **informativo**: lo que ha servido a otros runs tiende a ser la respuesta, lo erróneo y lo
        rancio tiende a ser distractor (la hipótesis del plan). El feedback es ESCASO y ruidoso:
        35 % de los golds tienen votos `useful`, 4 % un `wrong`; 6 % de los distractores tienen
        `useful` y 12 % `wrong`;
      * **ruido puro**: los metadatos no dicen nada de cuál es la respuesta (el peor caso: el
        feedback envenenado o inútil). El ranking no debe empeorar más de `NOISE_TOLERANCE`.

Métricas: MRR y recall@3 con los pesos a cero (el ranking de antes) y con los defaults
(`DEFAULT_RANK_WEIGHTS`). Los defaults salen de la rejilla de `test_grid_*` (ver el docstring de
`DEFAULT_RANK_WEIGHTS`); el test fija que la mejora es real y acotada, y que los números que van
al changelog salen de aquí (`pytest -s` los imprime).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from uuid import UUID, uuid4

from api_server.memorizer.ranking import (
    DEFAULT_RANK_WEIGHTS,
    MemorySignals,
    RankWeights,
    rank_fused,
)
from api_server.memorizer.recall import fuse_rankings

SEED = 20260930
N_QUERIES = 400
POOL = 12
PATH_K = 10  # cada vía devuelve sus 10 mejores
NOISE = 0.35
PATH_DROP = 0.15  # probabilidad de que una vía no devuelva un candidato


@dataclass(frozen=True)
class Case:
    gold: UUID
    bm25: list[UUID]
    vector: list[UUID]
    entity: list[UUID]
    signals: dict[UUID, MemorySignals]


def _signals(rng: random.Random, *, is_gold: bool, correlated: bool) -> MemorySignals:
    """Metadatos de una memoria. El feedback es ESCASO (la mayoría no tiene ningún voto: los
    agentes votan poco) y ruidoso, que es el caso realista y el que hace falta acotar."""
    episodic = rng.random() < 0.6
    useful = harmful = 0
    if correlated:
        age = rng.uniform(5, 120) if is_gold else rng.uniform(5, 400)
        if is_gold:
            useful = rng.randint(1, 4) if rng.random() < 0.35 else 0
            harmful = 1 if rng.random() < 0.04 else 0
        else:
            useful = rng.randint(1, 2) if rng.random() < 0.06 else 0
            harmful = rng.randint(1, 2) if rng.random() < 0.12 else 0
    else:  # los metadatos no informan de quién es la respuesta
        age = rng.uniform(5, 400)
        useful = rng.randint(1, 3) if rng.random() < 0.12 else 0
        harmful = rng.randint(1, 2) if rng.random() < 0.10 else 0
    return MemorySignals(
        type="episodic" if episodic else "semantic",
        kind=None,
        useful_count=useful,
        harmful_count=harmful,
        disputed=harmful > 0,
        age_days=age,
    )


def build_cases(*, correlated: bool, seed: int = SEED, n: int = N_QUERIES) -> list[Case]:
    rng = random.Random(seed + (0 if correlated else 1))
    cases: list[Case] = []
    for _ in range(n):
        ids = [uuid4() for _ in range(POOL)]
        gold = ids[0]
        latent = {m: (1.0 if m == gold else rng.uniform(0.0, 0.85)) for m in ids}
        paths: list[list[UUID]] = []
        for _path in range(3):
            noisy = {m: latent[m] + rng.gauss(0, NOISE) for m in ids if rng.random() >= PATH_DROP}
            ranked = sorted(noisy, key=lambda m: -noisy[m])[:PATH_K]
            paths.append(ranked)
        signals = {m: _signals(rng, is_gold=m == gold, correlated=correlated) for m in ids}
        cases.append(Case(gold, paths[0], paths[1], paths[2], signals))
    return cases


def evaluate(cases: list[Case], weights: RankWeights) -> tuple[float, float]:
    """``(MRR, recall@3)`` de la respuesta esperada con esos pesos."""
    rr = 0.0
    hit3 = 0
    for case in cases:
        fused = fuse_rankings(case.bm25, case.vector, case.entity)
        order = [m for m, _ in rank_fused(fused, case.signals, weights)]
        if case.gold in order:
            rank = order.index(case.gold) + 1
            rr += 1.0 / rank
            hit3 += rank <= 3
    return rr / len(cases), hit3 / len(cases)


def test_defaults_improve_recall_when_the_signals_are_informative() -> None:
    cases = build_cases(correlated=True)
    mrr0, r3_0 = evaluate(cases, RankWeights.off())
    mrr1, r3_1 = evaluate(cases, DEFAULT_RANK_WEIGHTS)
    print(
        f"\n[eval sintético · mundo correlacionado · {N_QUERIES} consultas]"
        f" ANTES (pesos 0): MRR={mrr0:.3f} recall@3={r3_0:.3f}"
        f" | DESPUÉS (defaults): MRR={mrr1:.3f} recall@3={r3_1:.3f}"
    )
    assert mrr1 >= mrr0 + 0.025, (mrr0, mrr1)
    assert r3_1 >= r3_0, (r3_0, r3_1)


def test_defaults_do_not_hurt_when_the_signals_are_pure_noise() -> None:
    cases = build_cases(correlated=False)
    mrr0, r3_0 = evaluate(cases, RankWeights.off())
    mrr1, r3_1 = evaluate(cases, DEFAULT_RANK_WEIGHTS)
    print(
        f"\n[eval sintético · mundo de ruido puro · {N_QUERIES} consultas]"
        f" ANTES: MRR={mrr0:.3f} recall@3={r3_0:.3f}"
        f" | DESPUÉS: MRR={mrr1:.3f} recall@3={r3_1:.3f}"
    )
    assert mrr1 >= mrr0 - NOISE_TOLERANCE, (mrr0, mrr1)
    assert r3_1 >= r3_0 - 0.05, (r3_0, r3_1)


def test_the_eval_is_reproducible() -> None:
    a = evaluate(build_cases(correlated=True), DEFAULT_RANK_WEIGHTS)
    b = evaluate(build_cases(correlated=True), DEFAULT_RANK_WEIGHTS)
    assert a == b


def test_each_signal_pulls_its_weight_ablation() -> None:
    """Cada término, solo, mejora el mundo informativo: ninguno es decorativo."""
    cases = build_cases(correlated=True)
    base, _ = evaluate(cases, RankWeights.off())
    d = DEFAULT_RANK_WEIGHTS
    only_utility, _ = evaluate(
        cases,
        RankWeights(utility=d.utility, smoothing=d.smoothing, half_life_days=d.half_life_days),
    )
    only_fresh, _ = evaluate(
        cases, RankWeights(freshness=d.freshness, half_life_days=d.half_life_days)
    )
    only_disputed, _ = evaluate(cases, RankWeights(disputed=d.disputed))
    assert only_utility > base
    assert only_fresh > base
    assert only_disputed > base


#: Cuánto puede empeorar el MRR del mundo de ruido puro respecto al ranking sin aprendizaje.
NOISE_TOLERANCE = 0.03


def grid_search(cases_good: list[Case], cases_noise: list[Case]) -> list[tuple[float, RankWeights]]:
    """La rejilla con la que se eligieron los defaults: MAXIMIZA el MRR del mundo informativo
    sujeto a no empeorar el de ruido puro más de ``NOISE_TOLERANCE``. Se conserva aquí para que
    la elección sea reproducible."""
    base_noise, _ = evaluate(cases_noise, RankWeights.off())
    out: list[tuple[float, RankWeights]] = []
    for u in (0.0, 0.05, 0.1, 0.15, 0.25, 0.5):
        for f in (0.0, 0.05, 0.1, 0.25):
            for dsp in (0.0, 0.5, 1.0, 2.0):
                for hl in (30.0, 60.0, 120.0):
                    w = RankWeights(utility=u, freshness=f, half_life_days=hl, disputed=dsp)
                    good, _ = evaluate(cases_good, w)
                    noise, _ = evaluate(cases_noise, w)
                    if noise >= base_noise - NOISE_TOLERANCE:
                        out.append((good, w))
    out.sort(key=lambda t: -t[0])
    return out


def test_the_defaults_sit_at_the_top_of_the_grid() -> None:
    """Los defaults no son un número a ojo: están entre los mejores de la rejilla."""
    good = build_cases(correlated=True)
    noise = build_cases(correlated=False)
    ranked = grid_search(good, noise)
    best = ranked[0][0]
    mine, _ = evaluate(good, DEFAULT_RANK_WEIGHTS)
    print(
        f"\n[rejilla] mejor MRR={best:.3f} ({ranked[0][1]}); defaults MRR={mine:.3f}"
        f" ({DEFAULT_RANK_WEIGHTS})"
    )
    assert mine >= best - 0.01, (mine, best)
