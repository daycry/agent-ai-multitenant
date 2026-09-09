"""Egress de la plataforma — `/admin/egress/*` (`task_mk_02`, ADR 0165 D7.3).

Un solo endpoint por ahora: el sondeo empírico de la allowlist de hosts MCP
remotos. El panel de ajustes dice «guardado — pendiente de aplicar al proxy» y
NUNCA «permitido», porque no lo sabe; quien lo sabe es el proxy, y este endpoint
es la forma de preguntárselo desde el panel.

Va bajo `/admin`, así que `main.py` le engancha el endurecimiento de la superficie
de System Admin al montarlo (`_is_admin_surface`), además del
`require_system_admin` explícito: es el mismo actor que puede escribir el ajuste,
y el sondeo abre conexiones hacia fuera — no es para cualquier tenant.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_admin_session, require_system_admin
from api_server.config import get_settings
from api_server.db.platform_settings import get_platform_setting
from api_server.egress.mcp_allowlist import MAX_HOSTS, SETTING_KEY
from api_server.egress.probe import ProbeResult, Verdict, probe_hosts

admin_router = APIRouter(prefix="/admin/egress", tags=["admin", "egress"])


class ProbeRequest(BaseModel):
    """Qué sondear. Vacío = los hosts del ajuste tal como está guardado.

    Se aceptan hosts sueltos para el sentido de la deriva que el ajuste ya no
    puede enseñar: un host RETIRADO del ajuste sigue permitido en el proxy hasta
    el paso de aplicación (revocación asimétrica, riesgo alto del ADR). El panel
    manda los que acaba de quitar para comprobar que de verdad se cerraron.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    hosts: list[str] | None = Field(default=None, max_length=MAX_HOSTS)


class ProbeHostResult(BaseModel):
    host: str
    verdict: Verdict
    detail: str


class ProbeResponse(BaseModel):
    proxy_url_configured: bool
    results: list[ProbeHostResult]


def _to_response(configured: bool, results: list[ProbeResult]) -> ProbeResponse:
    return ProbeResponse(
        proxy_url_configured=configured,
        results=[ProbeHostResult(host=r.host, verdict=r.verdict, detail=r.detail) for r in results],
    )


@admin_router.post("/mcp-allowlist/probe", response_model=ProbeResponse)
async def probe_mcp_allowlist(
    payload: ProbeRequest,
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> ProbeResponse:
    """Abre un CONNECT a través del egress-proxy por cada host y reporta
    `permitido` / `bloqueado` / `error`.

    Sondear no enumera: se puede afirmar «todo lo que el ajuste pide está
    permitido», nunca «el proxy no permite nada más». Y no se dispara solo: sólo
    bajo esta petición explícita, nunca en un barrido periódico.
    """
    hosts = payload.hosts
    if hosts is None:
        guardados = await get_platform_setting(session, SETTING_KEY, default=[])
        hosts = [str(h) for h in guardados] if isinstance(guardados, list) else []
    proxy_url = get_settings().egress_proxy_url
    results = await probe_hosts(hosts, proxy_url=proxy_url)
    return _to_response(bool(proxy_url and proxy_url.strip()), results)


__all__ = ["ProbeRequest", "ProbeResponse", "admin_router"]
