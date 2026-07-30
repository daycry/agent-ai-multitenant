"""Asesor de configuración de agentes (ADR 0125).

Cierra el bucle del leaderboard (ADR 0121) con GATE HUMANO: el beat semanal
``workers.config_advisor`` agrega los runs por (agente, modelo) y, cuando un
agente rinde mal con su combinación de mayor volumen reciente Y sus propios
datos muestran otra claramente mejor (umbral de muestras + margen de
mejora), emite una PROPUESTA como notificación accionable
(``config_proposal``: agente, de-modelo, a-modelo, evidencia). Nunca aplica
nada: cambiar el modelo sigue siendo una decisión del operador en la ficha
del agente (herencia de modelo, ADR 0055/0082).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from workers.celery_app import app
from workers.config import get_settings

_log = structlog.get_logger(__name__)

#: Muestras mínimas por combinación para opinar (mismo espíritu que el
#: leaderboard: no rankear ruido).
MIN_RUNS = 5
#: El actual tiene que rendir MAL de verdad…
CURRENT_MAX_SUCCESS = 0.6
#: …y el candidato ser CLARAMENTE mejor (margen absoluto).
IMPROVEMENT_MARGIN = 0.25

PROPOSAL_EVENT = "config_proposal"


@dataclass(frozen=True)
class ComboStat:
    tenant_id: str
    agent_id: str
    agent_name: str
    model: str | None
    runs: int
    success_rate: float
    avg_cost_usd: float


class AdvisorNotifier(Protocol):
    def publish(self, event: dict[str, Any]) -> None: ...


def _proposals_for(stats: list[ComboStat]) -> list[dict[str, Any]]:
    """Las propuestas que se sostienen con los datos, por agente.

    Regla v1 (conservadora): la combinación ACTUAL de un agente es la de
    mayor volumen de runs; se propone cambio solo si esa combinación tiene
    n≥MIN_RUNS con éxito ≤CURRENT_MAX_SUCCESS y existe OTRA combinación del
    mismo agente con n≥MIN_RUNS y éxito ≥ actual+IMPROVEMENT_MARGIN. Entre
    candidatas gana la de más éxito y, a igualdad, la más barata.
    """
    by_agent: dict[str, list[ComboStat]] = defaultdict(list)
    for stat in stats:
        by_agent[stat.agent_id].append(stat)

    proposals: list[dict[str, Any]] = []
    for combos in by_agent.values():
        eligible = [c for c in combos if c.runs >= MIN_RUNS]
        if len(eligible) < 2:
            continue
        current = max(eligible, key=lambda c: c.runs)
        if current.success_rate > CURRENT_MAX_SUCCESS:
            continue
        candidates = [
            c
            for c in eligible
            if c.model != current.model
            and c.success_rate >= current.success_rate + IMPROVEMENT_MARGIN
        ]
        if not candidates:
            continue
        best = max(candidates, key=lambda c: (c.success_rate, -c.avg_cost_usd))
        proposals.append(
            {
                "tenant_id": current.tenant_id,
                "agent_id": current.agent_id,
                "agent_name": current.agent_name,
                "from_model": current.model,
                "to_model": best.model,
                "evidence": (
                    f"success_rate {current.success_rate:.0%}→{best.success_rate:.0%} "
                    f"(n={current.runs}/{best.runs}), "
                    f"coste medio ${current.avg_cost_usd:.4f}→${best.avg_cost_usd:.4f}"
                ),
            }
        )
    return proposals


async def _advise(*, stats: list[ComboStat], notifier: AdvisorNotifier) -> dict[str, int]:
    proposals = _proposals_for(stats)
    for p in proposals:
        notifier.publish(
            {
                "event_type": PROPOSAL_EVENT,
                "tenant_id": p["tenant_id"],
                "context": {
                    "agent_id": p["agent_id"],
                    "agent_name": p["agent_name"],
                    "from_model": p["from_model"] or "(sin modelo)",
                    "to_model": p["to_model"] or "(sin modelo)",
                    "evidence": p["evidence"],
                },
            }
        )
    return {"proposals": len(proposals)}


# ---------------------------------------------------------------------------
# Cableado real — la MISMA agregación del leaderboard, por SQL.
# ---------------------------------------------------------------------------
async def _load_combo_stats(sessionmaker: Any, *, window_days: int = 30) -> list[ComboStat]:
    from sqlalchemy import text as sa_text

    async with sessionmaker() as session:
        rows = await session.execute(
            sa_text(
                """
                SELECT e.tenant_id, e.agent_id, a.name, m.model,
                       count(*) AS runs,
                       (count(*) FILTER (WHERE e.status = 'done'))::float / count(*)
                           AS success_rate,
                       coalesce(avg(e.total_cost_usd), 0) AS avg_cost_usd
                FROM executions e
                JOIN agents a ON a.id = e.agent_id
                LEFT JOIN LATERAL (
                    SELECT s->>'model' AS model
                    FROM jsonb_array_elements(e.steps_log) s
                    WHERE s->>'kind' = 'model_call' AND s->>'model' IS NOT NULL
                    ORDER BY (s->>'index')::bigint DESC
                    LIMIT 1
                ) m ON true
                WHERE e.created_at >= now() - make_interval(days => :days)
                  AND e.agent_id IS NOT NULL
                GROUP BY e.tenant_id, e.agent_id, a.name, m.model
                """
            ),
            {"days": window_days},
        )
        return [
            ComboStat(
                tenant_id=str(r[0]),
                agent_id=str(r[1]),
                agent_name=str(r[2]),
                model=r[3],
                runs=int(r[4]),
                success_rate=float(r[5]),
                avg_cost_usd=float(r[6]),
            )
            for r in rows.fetchall()
        ]


@app.task(name="workers.config_advisor")  # type: ignore[untyped-decorator]
def config_advisor_task() -> dict[str, int]:
    """Beat semanal: propone (nunca aplica) mejoras de configuración."""
    settings = get_settings()

    async def _main() -> dict[str, int]:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from workers.db import worker_engine
        from workers.standup import CeleryStandupNotifier

        engine = worker_engine(settings)
        try:
            sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
            stats = await _load_combo_stats(sessionmaker)
            return await _advise(
                stats=stats, notifier=CeleryStandupNotifier(broker_url=settings.broker_url)
            )
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_main())
    except Exception:
        _log.exception("config_advisor.run_failed")
        return {"proposals": 0}
