"""«¿Está conectado el OAuth de este servidor MCP?» en un solo sitio.

La regla es la de `GET .../oauth/status`: sin Vault o sin token, no conectado. La
comparten el Resumen del proyecto (`task_ui_10`) y las Capacidades (`task_ui_20`);
dos copias serían dos definiciones de «conectado».

Tres estados, no dos: ``True`` (hay access token), ``False`` (no hay entrada o token) y
``None`` (**desconocido**: Vault sellado o caído, o la entrada no se puede leer). Confundir
el tercero con el segundo invitaba a «reconectar» cuando lo que falla es Vault.

Lo que se cuida, porque estos endpoints los refresca el panel cada 30 s:

* `hvac` es síncrono: la lectura sale del event loop con ``asyncio.to_thread`` y las de
  varios servidores corren en paralelo acotado (``probe_many``), no en serie.
* Caché en proceso de 30 s por (tenant, proyecto, servidor), el mismo TTL y la misma
  razón que la credencial de proveedor (`llm_providers.factory`). El desconocido NO se
  cachea: un parpadeo de Vault no debe durar 30 s.
* Ninguna excepción de la sonda sale de aquí: una entrada corrupta o un Vault caído son
  ``None``, nunca un 500.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterable
from uuid import UUID

from shared_mcp import MCPAuthError, VaultResolver, oauth_vault_path

from api_server.llm_providers.factory import PROVIDER_SECRET_CACHE_TTL_SECONDS

#: ``True`` conectado · ``False`` no conectado · ``None`` desconocido (Vault no responde).
OAuthConnected = Callable[[str], Awaitable[bool | None]]

#: Cuántas lecturas a Vault a la vez: suficiente para el puñado de servidores de un
#: proyecto sin abrir un abanico contra Vault.
PROBE_CONCURRENCY = 4

# Clave de la entrada de Vault donde `shared_mcp.oauth.VaultTokenStorage` guarda el token.
_TOKEN_KEY = "oauth_token"
# Causas de MCPAuthError que significan «esta entrada no existe» (no conectado), frente
# a cualquier otra (Vault sellado, red, permiso) que es «no lo sé».
_MISSING_CAUSES = frozenset({"KeyError", "InvalidPath"})

_CACHE: dict[tuple[UUID, UUID, str], tuple[float, bool]] = {}

__all__ = [
    "PROBE_CONCURRENCY",
    "OAuthConnected",
    "clear_oauth_probe_cache",
    "oauth_connected_probe",
    "probe_many",
]


def _monotonic() -> float:
    return time.monotonic()


def clear_oauth_probe_cache() -> None:
    """Vacía la caché (tests, cambio de resolver)."""
    _CACHE.clear()


def _read(resolver: VaultResolver, path: str) -> bool | None:
    """Lectura síncrona (hilo aparte). Nunca lanza."""
    try:
        entry = resolver.resolve(path)
    except MCPAuthError as exc:
        cause = exc.__cause__
        return False if cause is not None and type(cause).__name__ in _MISSING_CAUSES else None
    except Exception:
        return None
    raw = entry.get(_TOKEN_KEY)
    if not raw:
        return False
    try:
        token = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(token, dict) or "access_token" not in token:
        return None
    return bool(token["access_token"])


def oauth_connected_probe(
    resolver: VaultResolver | None, tenant_id: UUID, project_id: UUID
) -> OAuthConnected:
    """Sonda por nombre de servidor: ``True`` sólo si hay un access token en Vault."""

    async def connected(server_name: str) -> bool | None:
        if resolver is None:
            return False
        key = (tenant_id, project_id, server_name)
        hit = _CACHE.get(key)
        if hit is not None and hit[0] > _monotonic():
            return hit[1]
        path = oauth_vault_path(
            tenant_id=str(tenant_id), project_id=str(project_id), server_name=server_name
        )
        state = await asyncio.to_thread(_read, resolver, path)
        if state is not None:
            _CACHE[key] = (_monotonic() + PROVIDER_SECRET_CACHE_TTL_SECONDS, state)
        return state

    return connected


async def probe_many(probe: OAuthConnected, names: Iterable[str]) -> dict[str, bool | None]:
    """El estado de varios servidores a la vez, con concurrencia acotada."""
    gate = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def one(name: str) -> tuple[str, bool | None]:
        async with gate:
            return name, await probe(name)

    unique = list(dict.fromkeys(names))
    return dict(await asyncio.gather(*(one(n) for n in unique)))
