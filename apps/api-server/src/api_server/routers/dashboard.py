"""`GET /tenant/dashboard` — el dashboard del tenant en una respuesta (`task_ui_02`).

Router fino: la lógica vive en `api_server.dashboard.tenant`. Aquí sólo la
autorización (cualquier miembro del tenant) y una caché Redis corta por tenant, porque
el panel lo pide al entrar y las cifras no necesitan precisión al segundo. Todo lo que
devuelve es del tenant, no del usuario, así que la clave por tenant es suficiente.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.budgets.consumption import tenant_budget_summary
from api_server.dashboard import build_tenant_dashboard
from api_server.routers._helpers import require_tenant_id

router = APIRouter(prefix="/tenant", tags=["dashboard"])

#: El plan pide 30 s: lo bastante corto para que aprobar algo se vea casi al momento.
DASHBOARD_CACHE_TTL_SECONDS = 30
_CACHE_PREFIX = "dashboard:tenant:"
#: El gasto va en su propia clave y con TTL largo: `tenant_budget_summary` lanza tres
#: consultas por proyecto con presupuesto y los presupuestos se mueven despacio.
SPEND_CACHE_TTL_SECONDS = 300
_SPEND_CACHE_PREFIX = "dashboard:tenant-spend:"
#: Lo que ve quien no es admin del tenant en lugar del gasto.
_SPEND_FORBIDDEN: dict[str, Any] = {"available": False, "reason": "forbidden"}


async def _cached(key: str) -> dict[str, Any] | None:
    """El dashboard cacheado, o ``None``. Un fallo de Redis cae a la BD."""
    try:
        from api_server.auth.deps import get_redis

        raw = await get_redis().get(key)
    except Exception:
        return None
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


async def _store(key: str, payload: dict[str, Any], ttl: int = DASHBOARD_CACHE_TTL_SECONDS) -> None:
    """Best-effort: sin Redis, el dashboard sigue saliendo de la BD."""
    try:
        from api_server.auth.deps import get_redis

        await get_redis().setex(key, ttl, json.dumps(payload))
    except Exception:
        return


async def _spend(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    key = f"{_SPEND_CACHE_PREFIX}{tenant_id}"
    cached = await _cached(key)
    if cached is not None:
        return cached
    spend = await tenant_budget_summary(session, tenant_id=tenant_id)
    await _store(key, spend, SPEND_CACHE_TTL_SECONDS)
    return spend


async def _is_tenant_admin(principal: AuthPrincipal, session: AsyncSession) -> bool:
    """Reutiliza la propia puerta `require_tenant_admin`: la misma regla de rol que
    protege `/tenant-stats/*` y `/budgets/pause`, sin duplicarla aquí."""
    try:
        await require_tenant_admin(principal, session)
    except HTTPException as exc:
        if exc.status_code == 403:
            return False
        raise
    return True


def _trim_for_member(payload: dict[str, Any]) -> dict[str, Any]:
    """Gasto frente a presupuesto y pausas por presupuesto son de `tenant_admin` en sus
    endpoints de origen (CWE-863); a un miembro raso no se le cuentan aquí."""
    trimmed = dict(payload)
    trimmed["spend"] = dict(_SPEND_FORBIDDEN)
    trimmed["issues"] = [
        i
        for i in payload.get("issues", [])
        if not str(i.get("kind", "")).startswith("budget_paused")
    ]
    return trimmed


@router.get("/dashboard")
async def tenant_dashboard(
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Pendientes humanos, trabajo por proyecto, gasto, salud, actividad y MCP."""
    tenant_id = require_tenant_id(principal)
    key = f"{_CACHE_PREFIX}{tenant_id}"
    payload = await _cached(key)
    if payload is None:
        payload = await build_tenant_dashboard(
            session, tenant_id, spend=await _spend(session, tenant_id)
        )
        await _store(key, payload)
    # La caché guarda el payload completo por tenant; el recorte es por petición.
    if await _is_tenant_admin(principal, session):
        return payload
    return _trim_for_member(payload)
