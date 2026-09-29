"""`GET /projects/{id}/overview` — el Resumen del proyecto (`task_ui_10`).

Router fino: la lógica vive en `api_server.dashboard.project`. El gasto se reutiliza del
dashboard del tenant (`routers.dashboard._spend`, con su propia caché lenta) y el recorte
por rol es el mismo: gasto y pausas por presupuesto son de `tenant_admin` en sus endpoints
de origen (CWE-863).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from shared_mcp import VaultResolver
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_member
from api_server.dashboard.project import build_project_overview
from api_server.mcp.oauth_probe import oauth_connected_probe
from api_server.routers._helpers import require_tenant_id
from api_server.routers.dashboard import _is_tenant_admin, _spend
from api_server.routers.mcp import get_vault_resolver

router = APIRouter(prefix="/projects", tags=["projects"])

_SPEND_FORBIDDEN: dict[str, Any] = {"available": False, "reason": "forbidden"}


@router.get("/{project_id}/overview")
async def project_overview(
    project_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    resolver: VaultResolver | None = Depends(get_vault_resolver),
) -> dict[str, Any]:
    """Planes activos, runs, gasto, avisos y actividad de UN proyecto."""
    tenant_id = require_tenant_id(principal)
    is_admin = await _is_tenant_admin(principal, session)
    spend = await _spend(session, tenant_id) if is_admin else dict(_SPEND_FORBIDDEN)
    payload = await build_project_overview(
        session,
        tenant_id,
        project_id,
        spend=spend,
        oauth_connected=oauth_connected_probe(resolver, tenant_id, project_id),
    )
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    if not is_admin:
        payload["spend"] = dict(_SPEND_FORBIDDEN)
        payload["warnings"] = [
            w for w in payload["warnings"] if not str(w["kind"]).startswith("budget_paused")
        ]
    return payload
