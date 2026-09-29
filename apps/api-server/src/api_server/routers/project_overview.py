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
from api_server.mcp.oauth_probe import invalidate_oauth_probe, oauth_connected_probe
from api_server.routers._helpers import require_tenant_id
from api_server.routers.dashboard import (
    DASHBOARD_CACHE_TTL_SECONDS,
    _cached,
    _is_tenant_admin,
    _spend,
    _store,
)
from api_server.routers.mcp import get_vault_resolver

router = APIRouter(prefix="/projects", tags=["projects"])

_SPEND_FORBIDDEN: dict[str, Any] = {"available": False, "reason": "forbidden"}
#: Como el dashboard del tenant: el panel refresca el Resumen cada 30 s y las cifras no
#: necesitan precisión al segundo. Clave por (tenant, proyecto); el recorte por rol va
#: DESPUÉS de la caché, así que se guarda el payload completo.
OVERVIEW_CACHE_TTL_SECONDS = DASHBOARD_CACHE_TTL_SECONDS
OVERVIEW_CACHE_PREFIX = "dashboard:project:"
#: Aviso de un estado TRANSITORIO (Vault sellado o caído): un payload que lo lleva no se
#: guarda en Redis, o el aviso duraría el TTL entero después de que Vault vuelva.
_TRANSIENT_WARNING_KINDS = frozenset({"vault_unavailable"})


async def invalidate_project_oauth_state(
    tenant_id: UUID, project_id: UUID, server_name: str
) -> None:
    """«Conectar» o desconectar un servidor OAuth cambió su estado: olvida la sonda de
    ese (tenant, proyecto, servidor) y el Resumen cacheado del proyecto, para que
    Capacidades y Resumen lo reflejen ya y no 30 s después. Best-effort: sin Redis no
    hay caché que invalidar."""
    invalidate_oauth_probe(tenant_id, project_id, server_name)
    try:
        from api_server.auth.deps import get_redis

        await get_redis().delete(f"{OVERVIEW_CACHE_PREFIX}{tenant_id}:{project_id}")
    except Exception:
        return


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
    key = f"{OVERVIEW_CACHE_PREFIX}{tenant_id}:{project_id}"
    payload = await _cached(key)
    if payload is None:
        payload = await build_project_overview(
            session,
            tenant_id,
            project_id,
            spend=await _spend(session, tenant_id),
            oauth_connected=oauth_connected_probe(resolver, tenant_id, project_id),
        )
        if payload is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
        if not any(w.get("kind") in _TRANSIENT_WARNING_KINDS for w in payload["warnings"]):
            await _store(key, payload, OVERVIEW_CACHE_TTL_SECONDS)
    if not is_admin:
        payload = dict(payload)
        payload["spend"] = dict(_SPEND_FORBIDDEN)
        payload["warnings"] = [
            w for w in payload["warnings"] if not str(w["kind"]).startswith("budget_paused")
        ]
    return payload
