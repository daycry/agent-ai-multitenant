"""Pestañas Equipo y Costes del proyecto (`task_ui_12`).

Routers finos: la lógica vive en `api_server.dashboard.project_team` y `project_costs`.

* ``GET /projects/{id}/team`` — cualquier miembro del tenant (el equipo no es un dato
  sensible; es lo que ya enseña la ficha del proyecto).
* ``GET /projects/{id}/costs`` — sólo ``tenant_admin`` (el gasto y las pausas por presupuesto
  lo son en sus endpoints de origen, CWE-863). La pausa se levanta con el ya existente
  ``POST /budgets/pause/override``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.dashboard.project import project_spend
from api_server.dashboard.project_costs import (
    DEFAULT_WINDOW_DAYS,
    MAX_WINDOW_DAYS,
    build_project_costs,
)
from api_server.dashboard.project_team import build_project_team
from api_server.routers._helpers import require_tenant_id
from api_server.routers.dashboard import _spend

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("/{project_id}/team")
async def project_team(
    project_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Equipo asignado, sus agentes (IA y humanos) y los huecos de rol del proyecto."""
    payload = await build_project_team(session, require_tenant_id(principal), project_id)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return payload


@router.get("/{project_id}/costs")
async def project_costs(
    project_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    window_days: int = Query(default=DEFAULT_WINDOW_DAYS, ge=1, le=MAX_WINDOW_DAYS),
) -> dict[str, Any]:
    """Gasto del proyecto por plan, agente y modelo frente a su presupuesto. tenant_admin."""
    tenant_id = require_tenant_id(principal)
    paused = (
        await session.execute(
            sa_text(
                "SELECT paused_by_budget FROM projects"
                " WHERE id = :pid AND tenant_id = :tid AND deleted_at IS NULL"
            ),
            {"pid": project_id, "tid": tenant_id},
        )
    ).first()
    if paused is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return await build_project_costs(
        session,
        tenant_id,
        project_id,
        window_days=window_days,
        project_spend=project_spend(await _spend(session, tenant_id), project_id),
        paused_by_budget=bool(paused.paused_by_budget),
    )
