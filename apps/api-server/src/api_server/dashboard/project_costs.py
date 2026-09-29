"""La pestaña Costes de un proyecto (`task_ui_12`, plan ui-reestructuracion).

Gasto de los runs del proyecto desglosado por plan, agente y modelo, más su fila de
presupuesto y la pausa por presupuesto. Reutiliza lo que ya existe:

* el coste de un run es ``executions.total_cost_usd`` (USD canónico, el mismo que suma
  ``/tenant-stats/consumption``) y el modelo ``executions.last_model`` (el mismo que filtra
  ``/tenant-stats/runs?model=``);
* el presupuesto es la fila del proyecto de ``tenant_budget_summary`` (el router la pasa
  ya calculada, con la caché lenta del dashboard).

Es coste de **IA**: el coste humano (``compute_human_cost_usd``) no se desglosa por
proyecto en el dominio y no se inventa aquí. Todas las consultas llevan ``tenant_id``
además de la RLS de la sesión (principio 1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_WINDOW_DAYS = 90
MAX_WINDOW_DAYS = 730
#: Filas por desglose (los más caros primero).
BREAKDOWN_LIMIT = 20

_COST = Decimal("0.000001")

_BASE = (
    " FROM executions e JOIN tasks t ON t.id = e.task_id"
    " {joins}"
    " WHERE e.tenant_id = :tid AND t.tenant_id = :tid AND t.project_id = :pid"
    " AND e.created_at >= :since"
)


def _cost(value: Any) -> str:
    return str(Decimal(value or 0).quantize(_COST))


async def _breakdown(
    session: AsyncSession,
    *,
    select_cols: str,
    joins: str,
    group_by: str,
    params: dict[str, Any],
) -> list[Any]:
    sql = (
        f"SELECT {select_cols}, count(e.id) AS runs,"
        " coalesce(sum(e.total_cost_usd), 0) AS cost,"
        " coalesce(sum(e.total_tokens), 0) AS tokens"
        + _BASE.format(joins=joins)
        + f" GROUP BY {group_by} ORDER BY cost DESC, runs DESC LIMIT :limit"
    )
    return list((await session.execute(sa_text(sql), {**params, "limit": BREAKDOWN_LIMIT})).all())


async def build_project_costs(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    *,
    window_days: int,
    project_spend: dict[str, Any],
    paused_by_budget: bool,
) -> dict[str, Any]:
    """El desglose de gasto del proyecto en la ventana (el router comprueba que existe)."""
    params: dict[str, Any] = {
        "tid": tenant_id,
        "pid": project_id,
        "since": datetime.now(tz=UTC) - timedelta(days=window_days),
    }
    totals = (
        await session.execute(
            sa_text(
                "SELECT count(e.id) AS runs, coalesce(sum(e.total_cost_usd), 0) AS cost,"
                " coalesce(sum(e.total_tokens), 0) AS tokens" + _BASE.format(joins="")
            ),
            params,
        )
    ).one()
    by_plan = await _breakdown(
        session,
        select_cols="p.id AS key, p.title AS label",
        joins="LEFT JOIN plans p ON p.id = t.plan_id AND p.tenant_id = :tid",
        group_by="p.id, p.title",
        params=params,
    )
    by_agent = await _breakdown(
        session,
        select_cols="a.id AS key, a.name AS label",
        joins="LEFT JOIN agents a ON a.id = e.agent_id AND a.tenant_id = :tid",
        group_by="a.id, a.name",
        params=params,
    )
    by_model = await _breakdown(
        session,
        select_cols="e.last_model AS key, e.last_model AS label",
        joins="",
        group_by="e.last_model",
        params=params,
    )
    return {
        "window_days": window_days,
        "run_count": int(totals.runs),
        "total_cost_usd": _cost(totals.cost),
        "total_tokens": int(totals.tokens),
        "budget": project_spend,
        "paused_by_budget": paused_by_budget,
        "by_plan": [
            {
                "plan_id": str(r.key) if r.key is not None else None,
                "title": r.label,
                "runs": int(r.runs),
                "cost_usd": _cost(r.cost),
                "tokens": int(r.tokens),
            }
            for r in by_plan
        ],
        "by_agent": [
            {
                "agent_id": str(r.key) if r.key is not None else None,
                "name": r.label,
                "runs": int(r.runs),
                "cost_usd": _cost(r.cost),
                "tokens": int(r.tokens),
            }
            for r in by_agent
        ],
        "by_model": [
            {
                "model": r.label,
                "runs": int(r.runs),
                "cost_usd": _cost(r.cost),
                "tokens": int(r.tokens),
            }
            for r in by_model
        ],
    }


__all__ = ["BREAKDOWN_LIMIT", "DEFAULT_WINDOW_DAYS", "MAX_WINDOW_DAYS", "build_project_costs"]
