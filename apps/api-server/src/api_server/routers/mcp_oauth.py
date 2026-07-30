"""Interactive OAuth «Connect» endpoints for remote MCP servers (ADR 0127).

Three endpoints, mounted per project + server:

* ``POST /projects/{id}/mcp-servers/{name}/oauth/connect`` (tenant admin) —
  starts the flow (discovery + DCR + PKCE) and returns ``{authorization_url}``;
  the admin-panel redirects the browser there for consent.
* ``GET  /projects/{id}/mcp-servers/{name}/oauth/callback`` (**PUBLIC**) — the
  provider redirects the browser here after consent. Secured by the opaque,
  single-use ``state`` (not a JWT — the browser carries no session on a
  cross-site redirect). Exchanges the code, persists tokens to Vault, then
  302-redirects back to the MCP page with a result flag.
* ``GET  /projects/{id}/mcp-servers/{name}/oauth/status`` (tenant member) —
  ``{connected, expires_at, scopes}`` so the UI can show Conectado/No conectado.

The token STORE + refresh at runtime live in ``shared_mcp.oauth`` (the SDK's
``OAuthClientProvider`` reads what this flow persisted). Vault must be wired
(``API_SERVER_VAULT_TOKEN``); without it connect returns a typed AUTH_ERROR.
"""

from __future__ import annotations

from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from shared_mcp import VaultResolver, VaultTokenStorage, oauth_vault_path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_redis,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.db.domain import Project
from api_server.mcp_oauth_flow import (
    McpOAuthError,
    begin_flow,
    complete_flow,
    find_server_url,
)
from api_server.routers._helpers import require_tenant_id
from api_server.routers.mcp import get_vault_resolver
from api_server.routers.sso import _effective_redirect_base

router = APIRouter(prefix="/projects/{project_id}/mcp-servers/{server_name}/oauth", tags=["mcp"])


# Where the callback sends the browser back to (the SPA MCP page). Relative so
# it resolves against the reverse-proxy origin the browser is already on.
def _ui_return(project_id: str, result: str, server_name: str, reason: str | None = None) -> str:
    q = f"oauth_result={result}&server={server_name}"
    if reason:
        from urllib.parse import quote

        q += f"&reason={quote(reason)}"
    return f"/admin/projects/{project_id}/mcp-servers?{q}"


class OAuthConnectResponse(BaseModel):
    authorization_url: str


class OAuthStatusResponse(BaseModel):
    connected: bool
    expires_at: str | None = None
    scopes: list[str] = []


async def _server_url_or_404(session: AsyncSession, project_id: UUID, server_name: str) -> str:
    project = (
        await session.execute(
            select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    url = find_server_url(list(project.mcp_servers or []), server_name)
    if not url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server {server_name!r} not declared (or not an HTTP server)",
        )
    return url


@router.post("/connect", response_model=OAuthConnectResponse)
async def oauth_connect(
    project_id: UUID,
    server_name: str,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    resolver: VaultResolver | None = Depends(get_vault_resolver),
    redis: Redis = Depends(get_redis),
) -> OAuthConnectResponse:
    tenant_id = require_tenant_id(principal)
    if resolver is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Vault is not configured (API_SERVER_VAULT_TOKEN); cannot store OAuth tokens.",
        )
    server_url = await _server_url_or_404(session, project_id, server_name)
    redirect_base = await _effective_redirect_base()
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            authorization_url = await begin_flow(
                tenant_id=str(tenant_id),
                project_id=str(project_id),
                server_name=server_name,
                server_url=server_url,
                redirect_base=redirect_base,
                resolver=resolver,
                redis=redis,
                http_client=client,
            )
    except McpOAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return OAuthConnectResponse(authorization_url=authorization_url)


@router.get("/callback")
async def oauth_callback(
    project_id: UUID,
    server_name: str,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    resolver: VaultResolver | None = Depends(get_vault_resolver),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    """PUBLIC — no JWT (the browser arrives via a cross-site redirect). The
    single-use ``state`` in Redis is the security boundary."""
    if error:
        return RedirectResponse(
            _ui_return(str(project_id), "error", server_name, error_description or error),
            status_code=status.HTTP_302_FOUND,
        )
    if not code or not state:
        return RedirectResponse(
            _ui_return(str(project_id), "error", server_name, "missing code/state"),
            status_code=status.HTTP_302_FOUND,
        )
    if resolver is None:
        return RedirectResponse(
            _ui_return(str(project_id), "error", server_name, "vault not configured"),
            status_code=status.HTTP_302_FOUND,
        )
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            connected = await complete_flow(
                state=state,
                code=code,
                resolver=resolver,
                redis=redis,
                http_client=client,
            )
    except McpOAuthError as exc:
        return RedirectResponse(
            _ui_return(str(project_id), "error", server_name, str(exc)),
            status_code=status.HTTP_302_FOUND,
        )
    return RedirectResponse(
        _ui_return(str(project_id), "connected", connected),
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/status", response_model=OAuthStatusResponse)
async def oauth_status(
    project_id: UUID,
    server_name: str,
    principal: AuthPrincipal = Depends(require_tenant_member),
    resolver: VaultResolver | None = Depends(get_vault_resolver),
) -> OAuthStatusResponse:
    tenant_id = require_tenant_id(principal)
    if resolver is None:
        return OAuthStatusResponse(connected=False)
    storage = VaultTokenStorage(
        resolver,
        oauth_vault_path(
            tenant_id=str(tenant_id), project_id=str(project_id), server_name=server_name
        ),
    )
    token = await storage.get_tokens()
    if token is None or not token.access_token:
        return OAuthStatusResponse(connected=False)
    return OAuthStatusResponse(
        connected=True,
        scopes=token.scope.split() if token.scope else [],
    )


__all__ = ["router"]
