"""«¿Está conectado el OAuth de este servidor MCP?» en un solo sitio.

La regla es la de `GET .../oauth/status`: sin Vault o sin token, no conectado. La
comparten el Resumen del proyecto (`task_ui_10`) y las Capacidades (`task_ui_20`);
dos copias serían dos definiciones de «conectado».
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from shared_mcp import VaultResolver, VaultTokenStorage, oauth_vault_path

OAuthConnected = Callable[[str], Awaitable[bool]]

__all__ = ["OAuthConnected", "oauth_connected_probe"]


def oauth_connected_probe(
    resolver: VaultResolver | None, tenant_id: UUID, project_id: UUID
) -> OAuthConnected:
    """Sonda por nombre de servidor: ``True`` sólo si hay un access token en Vault."""

    async def connected(server_name: str) -> bool:
        if resolver is None:
            return False
        storage = VaultTokenStorage(
            resolver,
            oauth_vault_path(
                tenant_id=str(tenant_id), project_id=str(project_id), server_name=server_name
            ),
        )
        token = await storage.get_tokens()
        return token is not None and bool(token.access_token)

    return connected
