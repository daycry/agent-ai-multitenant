"""`/runs` — member-facing read access to this tenant's agent runs (executions).

The Work-menu Runs view (and the Kanban card's run-history panel) list a tenant's
executions, newest first. Unlike `GET /tenant-stats/runs` (``tenant_admin`` — the
analytics/export explorer), this surface is open to ANY tenant member: it reuses
the SAME query (:func:`query_execution_runs`) so the row shape and the
tenant-isolation guarantees are identical — only the required role differs.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_member
from api_server.routers._helpers import require_tenant_id
from api_server.routers.tenant_stats import query_execution_runs
from api_server.schemas.tenant_stats import ExecutionRunRow

router = APIRouter(tags=["runs"])

# Mirror the explorer's bounds (tenant_stats) so the two surfaces behave the same.
_DEFAULT_WINDOW_DAYS = 90
_MAX_WINDOW_DAYS = 730


@router.get("/runs", response_model=list[ExecutionRunRow])
async def list_runs(
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    window_days: int = Query(default=_DEFAULT_WINDOW_DAYS, ge=1, le=_MAX_WINDOW_DAYS),
    agent_id: UUID | None = Query(default=None, description="Narrow to one agent."),
    role: str | None = Query(default=None, max_length=32, description="Narrow to one agent role."),
    plan_id: UUID | None = Query(default=None, description="Narrow to one plan."),
    task_id: UUID | None = Query(
        default=None, description="Narrow to one task — the Kanban run-history panel."
    ),
    verdict: str | None = Query(
        default=None, max_length=32, description="Narrow to one execution verdict/status."
    ),
    model: str | None = Query(default=None, max_length=120, description="Narrow to one model."),
    min_cost: Decimal | None = Query(
        default=None, ge=0, description="Minimum total cost USD threshold."
    ),
    display_currency: str | None = Query(
        default=None, max_length=3, description="Override the tenant's display currency (ISO-4217)."
    ),
) -> list[ExecutionRunRow]:
    """This tenant's runs, newest first, paginated + filterable. ANY tenant member.

    Same row shape and tenant isolation as ``GET /tenant-stats/runs`` (RLS-bound
    session + defence-in-depth ``tenant_id`` predicate); only the required role
    differs (member vs ``tenant_admin``). Use ``?task_id=`` for the Kanban card's
    run-history panel. Never leaks prompts / completions / credentials / steps_log.
    """
    tenant_id = require_tenant_id(principal)
    return await query_execution_runs(
        session,
        tenant_id=tenant_id,
        limit=limit,
        offset=offset,
        window_days=window_days,
        agent_id=agent_id,
        role=role,
        plan_id=plan_id,
        task_id=task_id,
        verdict=verdict,
        model=model,
        min_cost=min_cost,
        display_currency=display_currency,
    )


# ---------------------------------------------------------------------------
# ADR 0121 — leaderboard de configuraciones (modelo x agente)
# ---------------------------------------------------------------------------
class LeaderboardRow(BaseModel):
    """Una combinación modeloxagente agregada sobre la ventana pedida.

    Atribución honesta (ADR 0121): el agente/modelo son los del RUN tal como
    se persistieron; si la config del agente cambió después, las filas
    antiguas siguen contando con la config con la que corrieron.
    """

    model: str | None
    agent_id: UUID | None
    agent_name: str | None
    agent_role: str | None
    runs: int = Field(description="Ejecuciones totales de la combinación en la ventana.")
    done: int = Field(description="Runs acabados en done (sin contar escalados).")
    escalated: int = Field(description="Runs needs_human_review / awaiting_human_approval.")
    aborted: int = Field(description="Runs aborted (cualquier código).")
    success_rate: float = Field(description="done / runs (0..1).")
    avg_iterations: float
    avg_cost_usd: float = Field(description="Coste medio por run (USD).")
    avg_tokens: float


@router.get("/runs/leaderboard", response_model=list[LeaderboardRow])
async def runs_leaderboard(
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    window_days: int = Query(default=_DEFAULT_WINDOW_DAYS, ge=1, le=_MAX_WINDOW_DAYS),
    min_runs: int = Query(
        default=5, ge=1, le=100, description="Umbral mínimo de muestras para rankear (n≥5)."
    ),
) -> list[LeaderboardRow]:
    """¿Qué combinación modeloxagente converge más y más barato AQUÍ? (ADR 0121).

    Agregación pura de lectura sobre las executions del tenant (RLS + predicado
    tenant_id de defensa en profundidad), ordenada por tasa de éxito y coste.
    El umbral ``min_runs`` evita rankear ruido estadístico. Los runs de review
    quedan fuera (juzgan, no implementan).
    """
    tenant_id = require_tenant_id(principal)
    rows = await session.execute(
        sa_text(
            """
            SELECT
                m.model,
                e.agent_id,
                a.name AS agent_name,
                a.role AS agent_role,
                count(*) AS runs,
                count(*) FILTER (WHERE e.status = 'done') AS done,
                count(*) FILTER (
                    WHERE e.status IN ('needs_human_review', 'awaiting_human_approval')
                ) AS escalated,
                count(*) FILTER (WHERE e.status = 'aborted') AS aborted,
                coalesce(avg(e.iterations), 0) AS avg_iterations,
                coalesce(avg(e.total_cost_usd), 0) AS avg_cost_usd,
                coalesce(avg(e.total_tokens), 0) AS avg_tokens
            FROM executions e
            LEFT JOIN agents a ON a.id = e.agent_id
            -- El modelo del run vive en su steps_log (último model_call) —
            -- misma semántica que _last_model_expr del explorer.
            LEFT JOIN LATERAL (
                SELECT s->>'model' AS model
                FROM jsonb_array_elements(e.steps_log) s
                WHERE s->>'kind' = 'model_call' AND s->>'model' IS NOT NULL
                ORDER BY (s->>'index')::bigint DESC
                LIMIT 1
            ) m ON true
            WHERE e.tenant_id = :tenant_id
              AND e.created_at >= now() - make_interval(days => :window_days)
            GROUP BY m.model, e.agent_id, a.name, a.role
            HAVING count(*) >= :min_runs
            ORDER BY (count(*) FILTER (WHERE e.status = 'done'))::float / count(*) DESC,
                     coalesce(avg(e.total_cost_usd), 0) ASC
            """
        ),
        {"tenant_id": str(tenant_id), "window_days": window_days, "min_runs": min_runs},
    )
    out: list[LeaderboardRow] = []
    for row in rows.mappings():
        runs_n = int(row["runs"])
        out.append(
            LeaderboardRow(
                model=row["model"],
                agent_id=row["agent_id"],
                agent_name=row["agent_name"],
                agent_role=row["agent_role"],
                runs=runs_n,
                done=int(row["done"]),
                escalated=int(row["escalated"]),
                aborted=int(row["aborted"]),
                success_rate=(int(row["done"]) / runs_n) if runs_n else 0.0,
                avg_iterations=float(row["avg_iterations"]),
                avg_cost_usd=float(row["avg_cost_usd"]),
                avg_tokens=float(row["avg_tokens"]),
            )
        )
    return out
