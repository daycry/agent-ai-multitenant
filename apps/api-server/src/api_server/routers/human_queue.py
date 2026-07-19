"""`/human-queue` — todo lo que espera decisión humana, en una lista (ADR 0123).

El cuello de botella nº1 del flujo agéntico es el HUMANO: lo que le espera
vive repartido en cuatro pantallas (planes en validación, approvals de
acciones sensibles, runs escalados por review y runs aparcados en una
aprobación). Este endpoint las agrega con un shape uniforme, ordenado por
antigüedad (lo más viejo primero), para la bandeja del panel. Solo LECTURA:
las acciones se ejecutan en sus endpoints ya existentes (approve del plan,
resolución del approval, la ficha del run) — la bandeja es una lente, no un
bypass de sus gates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_member
from api_server.routers._helpers import require_tenant_id

router = APIRouter(tags=["human-queue"])


class HumanQueueItem(BaseModel):
    kind: str = Field(description="plan_validation | approval_request | run_review | run_approval")
    id: UUID
    title: str
    project_name: str | None
    age_seconds: float = Field(description="Segundos esperando una decisión humana.")
    url_path: str = Field(description="Ruta del panel donde se resuelve.")


def _age(now: datetime, since: datetime | None) -> float:
    if since is None:
        return 0.0
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    return max((now - since).total_seconds(), 0.0)


@router.get("/human-queue", response_model=list[HumanQueueItem])
async def human_queue(
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[HumanQueueItem]:
    """La cola del humano del tenant, lo más viejo primero. Cualquier miembro.

    Cuatro consultas RLS-scoped (+ predicado tenant_id de defensa en
    profundidad) proyectadas al mismo shape. El campo `age_seconds` usa el
    último `updated_at`/`requested_at` de cada fila: cuánto lleva ESPERANDO.
    """
    tenant_id = str(require_tenant_id(principal))
    now = datetime.now(tz=UTC)
    items: list[HumanQueueItem] = []

    plans = await session.execute(
        sa_text(
            "SELECT p.id, p.title, pr.name, p.updated_at FROM plans p"
            " LEFT JOIN projects pr ON pr.id = p.project_id"
            " WHERE p.tenant_id = :tid AND p.status = 'pending_human_validation'"
        ),
        {"tid": tenant_id},
    )
    for row in plans.fetchall():
        items.append(
            HumanQueueItem(
                kind="plan_validation",
                id=row[0],
                title=str(row[1]),
                project_name=row[2],
                age_seconds=_age(now, row[3]),
                url_path=f"/admin/plans/{row[0]}",
            )
        )

    approvals = await session.execute(
        sa_text(
            "SELECT a.id, a.category, pr.name, a.requested_at FROM approval_requests a"
            " LEFT JOIN projects pr ON pr.id = a.project_id"
            " WHERE a.tenant_id = :tid AND a.status = 'pending'"
        ),
        {"tid": tenant_id},
    )
    for row in approvals.fetchall():
        items.append(
            HumanQueueItem(
                kind="approval_request",
                id=row[0],
                title=f"Acción sensible: {row[1]}",
                project_name=row[2],
                age_seconds=_age(now, row[3]),
                url_path="/admin/approvals",
            )
        )

    runs = await session.execute(
        sa_text(
            "SELECT e.id, e.status, t.title, pr.name, e.updated_at FROM executions e"
            " JOIN tasks t ON t.id = e.task_id"
            " LEFT JOIN projects pr ON pr.id = t.project_id"
            " WHERE e.tenant_id = :tid"
            "   AND e.status IN ('needs_human_review', 'awaiting_human_approval')"
        ),
        {"tid": tenant_id},
    )
    for row in runs.fetchall():
        status = str(row[1])
        items.append(
            HumanQueueItem(
                kind="run_review" if status == "needs_human_review" else "run_approval",
                id=row[0],
                title=str(row[2] or "Run"),
                project_name=row[3],
                age_seconds=_age(now, row[4]),
                url_path=f"/admin/executions/{row[0]}",
            )
        )

    items.sort(key=lambda i: i.age_seconds, reverse=True)
    return items
