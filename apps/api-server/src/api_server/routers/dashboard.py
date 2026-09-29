"""`GET /tenant/dashboard` — el dashboard del tenant en una respuesta (`task_ui_02`).

Router fino: la lógica vive en `api_server.dashboard.tenant`. Aquí sólo la
autorización (cualquier miembro del tenant) y una caché Redis corta por tenant, porque
el panel lo pide al entrar y las cifras no necesitan precisión al segundo. Todo lo que
devuelve es del tenant, no del usuario, así que la clave por tenant es suficiente.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_member
from api_server.dashboard import build_tenant_dashboard
from api_server.routers._helpers import require_tenant_id

router = APIRouter(prefix="/tenant", tags=["dashboard"])

#: El plan pide 30 s: lo bastante corto para que aprobar algo se vea casi al momento.
DASHBOARD_CACHE_TTL_SECONDS = 30
_CACHE_PREFIX = "dashboard:tenant:"


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


async def _store(key: str, payload: dict[str, Any]) -> None:
    """Best-effort: sin Redis, el dashboard sigue saliendo de la BD."""
    try:
        from api_server.auth.deps import get_redis

        await get_redis().setex(key, DASHBOARD_CACHE_TTL_SECONDS, json.dumps(payload))
    except Exception:
        return


@router.get("/dashboard")
async def tenant_dashboard(
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Pendientes humanos, trabajo por proyecto, gasto, salud, actividad y MCP."""
    tenant_id = require_tenant_id(principal)
    key = f"{_CACHE_PREFIX}{tenant_id}"
    cached = await _cached(key)
    if cached is not None:
        return cached
    payload = await build_tenant_dashboard(session, tenant_id)
    await _store(key, payload)
    return payload
