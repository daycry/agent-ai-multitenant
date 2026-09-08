"""MCP-related endpoints scoped to a project (Plan 05 task_05_07).

Right now we only ship ``POST /projects/{id}/mcp/test-connection``: a
one-shot probe the admin-panel's "Probar" button calls before the
operator clicks Save. It opens a session against the candidate config,
runs the MCP handshake + ``tools/list``, and returns what the server
advertised so the operator can verify they wired the right thing.

We deliberately keep this as a dedicated router (rather than wedging
it into ``routers/projects.py``) because Plan 05 already foresees
more MCP-scoped endpoints — diagnostic snapshots, tool inspection,
per-server enable/disable. They'll all land here.

Auth/Vault note: when the candidate config carries ``auth_ref``, the
resolver dependency below decides where the secret comes from. Today
the dependency returns ``None`` (Vault wiring for api-server is a
follow-up task); a config with ``auth_ref`` falls through to a typed
``AUTH_ERROR`` so the UI can show a useful message instead of the
SDK's raw exception.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from shared_mcp import (
    DiscoveryResult,
    MCPServerConfig,
    VaultResolver,
    discover_tools,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_admin
from api_server.config import get_settings
from api_server.db.domain import Project, ToolSecurityLevel
from api_server.egress.mcp_discovery import (
    EgressProxyNotConfiguredError,
    classify_discovery_failure,
    egress_client_factory_for,
)
from api_server.mcp.config import MCPServerConfigModel
from api_server.mcp.import_tools import (
    ConfigInvalidError,
    DiscoveryFailedError,
    ImportAbstainedError,
    ServerNotDeclaredError,
    import_server_tools,
    to_runtime_config,
)
from api_server.routers._helpers import require_tenant_id
from api_server.schemas.catalog import ToolResponse, to_tool_response

router = APIRouter(prefix="/projects/{project_id}/mcp", tags=["mcp"])


# ---------------------------------------------------------------------------
# Dependency seam — builds an HvacVaultResolver lazily when configured.
# ---------------------------------------------------------------------------
# Sentinel = "cache not built yet"; distinct from a built cache that's None.
_UNSET: object = object()


class _ResolverCache:
    """Module-level singleton holding the resolver. Class attribute (not
    a `global` keyword) so ruff PLW0603 stays happy and the test reset
    hook reads cleanly."""

    value: VaultResolver | object | None = _UNSET


def get_vault_resolver() -> VaultResolver | None:
    """Build (lazily, once) an `HvacVaultResolver` backed by the
    api-server's Vault config.

    Returns ``None`` when ``API_SERVER_VAULT_TOKEN`` is not set — the
    api-server starts without a working resolver and any MCP config
    with ``auth_ref`` falls through to a typed AUTH_ERROR. That keeps
    dev/test ergonomic (Vault doesn't need to be reachable to boot
    the api-server) while production deployments set the token and
    get real Vault resolution.

    Cached on a module-level singleton because hvac.Client is cheap to
    keep alive — one HTTP client + token. Tests reset via
    :func:`reset_vault_resolver_cache`.
    """
    if _ResolverCache.value is not _UNSET:
        cached = _ResolverCache.value
        assert cached is None or isinstance(cached, VaultResolver)
        return cached

    from shared_mcp import HvacVaultResolver

    # prod-10 task_prod10_07 (secrets-4): una sola fábrica para el cliente de
    # Vault, que además mantiene vivo el token (`renew_self` en segundo plano).
    # Antes se construía aquí un `hvac.Client` con el token estático y se cacheaba
    # para siempre; el día que caducase, toda resolución de `auth_ref` de MCP
    # empezaría a devolver AUTH_ERROR sin que nada hubiera cambiado.
    #
    # Además hereda el timeout de 5 s que a ESTE cliente le faltaba: sin él,
    # `requests` espera indefinidamente y un Vault sellado congelaba el bucle de
    # eventos (hallazgo perf-7, arreglado en llm_providers y no aquí).
    #
    # `None` sigue significando lo mismo que antes: sin token o sin hvac, el
    # api-server arranca y el `auth_ref` degrada a AUTH_ERROR tipado.
    from api_server.vault_client import build_vault_client

    client = build_vault_client()
    if client is None:
        _ResolverCache.value = None
        return None

    resolver: VaultResolver = HvacVaultResolver(client=client)
    _ResolverCache.value = resolver
    return resolver


def reset_vault_resolver_cache() -> None:
    """Test hook: forget the cached resolver so the next call rebuilds
    it from current settings + env. Used by tests that mutate
    ``API_SERVER_VAULT_TOKEN`` between cases.

    Vacía LAS DOS cachés, no sólo la de este módulo. Desde que prod-10
    (``task_prod10_07``) metió :func:`api_server.vault_client.build_vault_client`
    debajo, hay un segundo singleton —el ``hvac.Client``— que este hook no
    tocaba: un caso que corriese SIN token dejaba ese cliente cacheado en
    ``None``, y el siguiente, ya con token, reconstruía el resolver sobre el
    mismo ``None`` y se lo encontraba a ``None`` otra vez. El hook seguía
    prometiendo en su docstring que reconstruye «from current settings + env»
    y había dejado de hacerlo; el síntoma es un test que sólo pasa cuando corre
    solo, y lo sufren también los módulos que llaman a este hook para no
    arrastrar el Vault de otro test (``test_jit_provisioning``,
    ``test_sso_global_login``, ``test_post_login_membership_resolution``).
    """
    from api_server.vault_client import reset_vault_client_cache

    _ResolverCache.value = _UNSET
    reset_vault_client_cache()


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------
class DiscoveredTool(BaseModel):
    """One tool the server advertises during the probe."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str | None = None
    # JSON schema dict for the tool's args — the UI doesn't render it
    # today but exposing it now means we don't break the contract when
    # task_05_15 (diagnostic panel) wants to show it.
    input_schema: dict[str, object] = Field(default_factory=dict)


class TestConnectionResponse(BaseModel):
    """Successful probe response — mirrors `shared_mcp.DiscoveryResult`
    minus the runtime dataclasses (we project to plain JSON-friendly
    Pydantic so it serialises cleanly over HTTP)."""

    server_name: str
    server_version: str
    server_instructions: str | None = None
    tools: list[DiscoveredTool] = Field(default_factory=list)


# Typed error codes the UI can branch on. Free-form messages aren't
# stable across SDK versions; codes are.
#
# `task_mk_02` (ADR 0165 D9) añade los dos de egress. Antes se veían como un
# `TRANSPORT_ERROR` con un `403 Filtered` crudo dentro, que la UI pintaba tal
# cual y el operador leía como un fallo del servidor MCP.
McpErrorCode = Literal[
    "AUTH_ERROR",
    "TRANSPORT_ERROR",
    "CONFIG_ERROR",
    "UNKNOWN_ERROR",
    "EGRESS_BLOCKED",
    "EGRESS_PROXY_UNAVAILABLE",
    # ADR 0166: un servidor OAuth sin «Conectar» completado (D5) y la abstención
    # por encima del tope de 200 tools (L1).
    "OAUTH_NOT_CONNECTED",
    "TOO_MANY_TOOLS",
]


class McpTestConnectionError(BaseModel):
    """Error payload for /test-connection failures.

    Returned as the body of a 4xx so the UI can branch on `error_code`
    without parsing the human-facing `message`. The Playwright spec
    pins this shape so the UI keeps working even if SDK error texts
    drift.
    """

    error_code: McpErrorCode
    message: str


async def _discover_or_raise(
    runtime_config: MCPServerConfig, resolver: VaultResolver | None
) -> DiscoveryResult:
    """Descubrir las tools de un servidor POR EL MISMO CAMINO que el run (ADR 0165 D9).

    Es el único sitio desde el que este router llama a `discover_tools`, y tiene
    que seguir siéndolo: `task_mk_01` abre un tercer call site (descubrir al
    desplegar) y si uno quedase directo la asimetría probar≠ejecutar volvería
    por esa puerta. Un host externo sale por el egress-proxy
    (`API_SERVER_EGRESS_PROXY_URL`); un servicio del compose y un `stdio`, no —
    con la misma regla que el worker aplica por `NO_PROXY`.

    Los fallos se traducen a `McpTestConnectionError` mirando el TIPO de la causa
    raíz (addendum A2): un `403 Filtered` del proxy es `EGRESS_BLOCKED` (422 con
    el host y dónde pedirlo), no un `TRANSPORT_ERROR` con el texto crudo.
    """
    try:
        factory = egress_client_factory_for(runtime_config, get_settings().egress_proxy_url)
    except EgressProxyNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=McpTestConnectionError(
                error_code="EGRESS_PROXY_UNAVAILABLE", message=str(exc)
            ).model_dump(),
        ) from exc
    try:
        return await discover_tools(
            runtime_config, vault_resolver=resolver, httpx_client_factory=factory
        )
    except Exception as exc:
        failure = classify_discovery_failure(
            exc, config=runtime_config, proxied=factory is not None
        )
        raise HTTPException(
            status_code=failure.status_code,
            detail=McpTestConnectionError(
                error_code=failure.error_code, message=failure.message
            ).model_dump(),
        ) from exc


# ---------------------------------------------------------------------------
# POST /projects/{id}/mcp/test-connection
# ---------------------------------------------------------------------------
@router.post(
    "/test-connection",
    response_model=TestConnectionResponse,
    responses={
        # FastAPI's openapi generator picks these up so the contract is
        # visible without grepping the source.
        400: {"model": McpTestConnectionError, "description": "Config rejected"},
        401: {"model": McpTestConnectionError, "description": "Auth resolution failed"},
        502: {"model": McpTestConnectionError, "description": "Transport / handshake failed"},
    },
)
async def test_mcp_connection(
    project_id: UUID,
    payload: MCPServerConfigModel,
    _principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    resolver: VaultResolver | None = Depends(get_vault_resolver),
) -> TestConnectionResponse:
    """Open a one-shot MCP session against the candidate config.

    No state is persisted — the candidate config does NOT have to be
    saved yet. The operator typically calls this from inside the
    "edit / create MCP server" dialog before clicking Save.

    Errors fold into :class:`McpTestConnectionError` payloads:

      * ``CONFIG_ERROR``    — schema rejected (rare; the Pydantic
                              validator on the body already covers
                              most of this with a 422).
      * ``AUTH_ERROR``      — Vault resolver missing / Vault refused.
      * ``TRANSPORT_ERROR`` — couldn't open transport / handshake
                              failed / server crashed mid-call.
      * ``UNKNOWN_ERROR``   — fallback so the UI always has something
                              to show.
    """
    # Make sure the project is visible to the caller (tenant scoping).
    # We don't actually need the row to probe — but returning a 404 here
    # avoids leaking endpoint behavior for projects in other tenants.
    result = await session.execute(
        select(Project.id).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    # The body is a Pydantic model that already enforces transport
    # invariants + auth_ref shape. Translate it to the runtime dataclass
    # the shared_mcp client consumes.
    try:
        runtime_config = _to_runtime_config(payload)
    except ValueError as exc:
        # Defensive: model_validator already rejects bad combinations,
        # but if we ever broaden the schema we want a typed error here
        # rather than a 500.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=McpTestConnectionError(error_code="CONFIG_ERROR", message=str(exc)).model_dump(),
        ) from exc

    result_obj = await _discover_or_raise(runtime_config, resolver)

    return TestConnectionResponse(
        server_name=result_obj.server_name,
        server_version=result_obj.server_version,
        server_instructions=result_obj.server_instructions,
        tools=[
            DiscoveredTool(
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
            )
            for tool in result_obj.tools
        ],
    )


# ---------------------------------------------------------------------------
# POST /projects/{id}/mcp/servers/{server_name}/import-tools  (ADR 0052)
# ---------------------------------------------------------------------------
class ImportMcpToolsRequest(BaseModel):
    """Qué importar del servidor al catálogo.

    ``tool_names`` son los nombres *crudos* que el server expone (los que
    ``test-connection`` devolvió); se namespacean ``<server>.<tool>`` antes de
    persistirlos. Desde el ADR 0166 (D2) la lista es OPCIONAL: ausente = todas
    las que el servidor anuncie ahora, con reconciliación de lo que ya no anuncia
    (R3) y el tope de 200 con abstención (L1); con lista = la multiselección del
    ADR 0052, intacta, sin reconciliación.

    ``security_level`` también es opcional: ausente = no tocar el de las filas
    que ya existen (R2) y `sandboxed` para las nuevas. Antes se sobreescribía
    siempre con el default, así que un re-import pisaba en silencio la elección
    de un operador que lo hubiera subido o bajado.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    tool_names: list[str] | None = Field(default=None, min_length=1, max_length=200)
    security_level: ToolSecurityLevel | None = None


class ImportMcpToolsResponse(BaseModel):
    """Las filas ``Tool`` resultantes del upsert, y lo que NO entró y por qué."""

    tools: list[ToolResponse] = Field(default_factory=list)
    #: R3: nombres namespaceados retirados porque el servidor ya no los anuncia.
    retired: list[str] = Field(default_factory=list)
    #: L2/L3: nombres crudos omitidos; el motivo está en `warnings`.
    omitted: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@router.post("/servers/{server_name}/import-tools", response_model=ImportMcpToolsResponse)
async def import_mcp_tools(
    project_id: UUID,
    server_name: str,
    payload: ImportMcpToolsRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    resolver: VaultResolver | None = Depends(get_vault_resolver),
) -> ImportMcpToolsResponse:
    """Importar tools descubiertas de un MCP server al catálogo (ADR 0052).

    Tras un ``test-connection`` exitoso, el operador importa la selección que
    elija (``tool_names``). Cada tool se persiste como una fila ``Tool``
    ``mcp_tool`` namespaced ``<server>.<tool>`` para que ``<server>.read_file``
    no parezca un duplicado de un built-in ``read_file`` (faceta Origen=MCP,
    ADR 0049). El upsert es **idempotente**: re-importar una tool ya existente
    actualiza su ``security_level`` en vez de crear un duplicado, respetando el
    ``UNIQUE(tenant_id, name)`` de task_06_18_04 (un ``IntegrityError`` por
    carrera se traduce en un 409 limpio).

    Tenant-safe: el proyecto debe ser visible a la sesión (RLS); uno de otro
    tenant produce ``404`` sin filtrar su existencia. El server debe estar
    declarado en ``project.mcp_servers`` (si no, ``404``) — solo se importan
    tools de servers que el operador ya configuró.
    """
    tenant_id = require_tenant_id(principal)

    project = (
        await session.execute(
            select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    # ADR 0101 (fail-closed: discovery caído ⇒ cero filas), ADR 0165 D9 (sale por
    # el egress-proxy) y ADR 0166 (D2 lista opcional, D5 OAuth, D7 reconciliación,
    # D9 límites): todo vive en `import_server_tools`, que es el ÚNICO sitio donde
    # se importa — este endpoint, la task de la lane `marketplace` y el final de
    # «Conectar» lo comparten. Aquí sólo se traducen sus excepciones a HTTP.
    try:
        outcome = await import_server_tools(
            session,
            tenant_id=tenant_id,
            project=project,
            server_name=server_name,
            tool_names=payload.tool_names,
            security_level=payload.security_level,
            resolver=resolver,
            proxy_url=get_settings().egress_proxy_url,
            discover=discover_tools,
            actor_user_id=principal.user_id,
        )
    except ServerNotDeclaredError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConfigInvalidError as exc:
        # Una fila anterior a la regla de forma (ADR 0165 D11) que hoy no
        # validaría: se dice cuál es el problema en vez de descubrir contra ella.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=McpTestConnectionError(error_code="CONFIG_ERROR", message=str(exc)).model_dump(),
        ) from exc
    except DiscoveryFailedError as exc:
        raise HTTPException(
            status_code=exc.failure.status_code,
            detail=McpTestConnectionError(
                error_code=exc.failure.error_code, message=exc.failure.message
            ).model_dump(),
        ) from exc
    except ImportAbstainedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=McpTestConnectionError(
                error_code="TOO_MANY_TOOLS", message=str(exc)
            ).model_dump(),
        ) from exc
    except IntegrityError as exc:  # pragma: no cover - racing concurrent import
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="concurrent import collided on a tool name; retry",
        ) from exc

    if not outcome.tools and not outcome.retired and not outcome.omitted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="no importable tool names after normalisation",
        )
    for row in outcome.tools:
        await session.refresh(row)

    return ImportMcpToolsResponse(
        tools=[to_tool_response(t) for t in outcome.tools],
        retired=outcome.retired,
        omitted=outcome.omitted,
        warnings=outcome.warnings,
    )


# ---------------------------------------------------------------------------
# Pydantic → runtime dataclass conversion
# ---------------------------------------------------------------------------
# ADR 0166 D2: la conversión vive en `api_server.mcp.import_tools` porque la
# comparten los tres llamantes del import; aquí queda el nombre histórico para
# los tests que lo importan por ruta.
_to_runtime_config = to_runtime_config


__all__ = [
    "DiscoveredTool",
    "ImportMcpToolsRequest",
    "ImportMcpToolsResponse",
    "McpErrorCode",
    "McpTestConnectionError",
    "TestConnectionResponse",
    "get_vault_resolver",
    "router",
]
