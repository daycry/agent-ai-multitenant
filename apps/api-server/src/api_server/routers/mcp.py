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
    MCPAuthError,
    MCPServerConfig,
    MCPTransportError,
    VaultResolver,
    discover_tools,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_admin
from api_server.db.domain import (
    Project,
    Tool,
    ToolCategory,
    ToolImplementationType,
    ToolSecurityLevel,
)
from api_server.mcp.config import MCPServerConfigModel
from api_server.routers._helpers import require_tenant_id
from api_server.schemas.catalog import ToolResponse, normalize_tool_name, to_tool_response

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

    value: VaultResolver | None | object = _UNSET


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

    from api_server.config import get_settings

    settings = get_settings()
    if settings.vault_token is None:
        _ResolverCache.value = None
        return None

    try:
        import hvac
    except ImportError:
        # hvac not installed — same as no token. Surface AUTH_ERROR
        # rather than crash; the operator can either pip-install or
        # unset the token to acknowledge they don't want Vault.
        _ResolverCache.value = None
        return None

    client = hvac.Client(
        url=settings.vault_url,
        token=settings.vault_token.get_secret_value(),
    )
    resolver: VaultResolver = HvacVaultResolver(client=client)
    _ResolverCache.value = resolver
    return resolver


def reset_vault_resolver_cache() -> None:
    """Test hook: forget the cached resolver so the next call rebuilds
    it from current settings + env. Used by tests that mutate
    ``API_SERVER_VAULT_TOKEN`` between cases."""
    _ResolverCache.value = _UNSET


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
McpErrorCode = Literal["AUTH_ERROR", "TRANSPORT_ERROR", "CONFIG_ERROR", "UNKNOWN_ERROR"]


class McpTestConnectionError(BaseModel):
    """Error payload for /test-connection failures.

    Returned as the body of a 4xx so the UI can branch on `error_code`
    without parsing the human-facing `message`. The Playwright spec
    pins this shape so the UI keeps working even if SDK error texts
    drift.
    """

    error_code: McpErrorCode
    message: str


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

    try:
        result_obj = await discover_tools(runtime_config, vault_resolver=resolver)
    except MCPAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=McpTestConnectionError(error_code="AUTH_ERROR", message=str(exc)).model_dump(),
        ) from exc
    except MCPTransportError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=McpTestConnectionError(
                error_code="TRANSPORT_ERROR", message=str(exc)
            ).model_dump(),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=McpTestConnectionError(
                error_code="UNKNOWN_ERROR", message=f"{type(exc).__name__}: {exc}"
            ).model_dump(),
        ) from exc

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
    """Operator's multiselección de tools a importar al catálogo.

    ``tool_names`` son los nombres *crudos* que el server expone (los que
    ``test-connection`` devolvió); el endpoint los namespacea
    ``<server>.<tool>`` antes de persistirlos. La lista es la decisión del
    operador (supply chain, ADR 0052 opción P-A) — NO se importa todo
    automáticamente. ``security_level`` arranca en ``sandboxed`` (mínimo
    privilegio para código de terceros) y el operador puede ajustarlo.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    tool_names: list[str] = Field(min_length=1, max_length=200)
    security_level: ToolSecurityLevel = ToolSecurityLevel.SANDBOXED


class ImportMcpToolsResponse(BaseModel):
    """Las filas ``Tool`` resultantes del upsert (creadas o actualizadas)."""

    tools: list[ToolResponse] = Field(default_factory=list)


@router.post("/servers/{server_name}/import-tools", response_model=ImportMcpToolsResponse)
async def import_mcp_tools(
    project_id: UUID,
    server_name: str,
    payload: ImportMcpToolsRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
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

    declared = {
        str(server.get("name"))
        for server in (project.mcp_servers or [])
        if isinstance(server, dict) and server.get("name")
    }
    if server_name not in declared:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server {server_name!r} not declared on this project",
        )

    # Namespaced name <server>.<tool>; the tool segment is normalised to a
    # slug so it matches the ``tools.name`` invariant (task_06_18_04). Dedupe
    # the requested names so a duplicated selection upserts once.
    namespaced: dict[str, str] = {}
    for raw in payload.tool_names:
        tool_slug = normalize_tool_name(raw)
        if not tool_slug:
            continue
        namespaced[f"{server_name}.{tool_slug}"] = raw
    if not namespaced:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="no importable tool names after normalisation",
        )

    # Existing live rows for these names (so re-import updates rather than
    # inserts) — keyed by name, scoped to the tenant session (RLS).
    existing_rows = (
        (
            await session.execute(
                select(Tool).where(
                    Tool.name.in_(list(namespaced)),
                    Tool.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    by_name = {row.name: row for row in existing_rows}

    result_tools: list[Tool] = []
    for name, raw_name in namespaced.items():
        row = by_name.get(name)
        if row is None:
            row = Tool(
                tenant_id=tenant_id,
                name=name,
                description=f"MCP tool {raw_name!r} from server {server_name!r}",
                category=ToolCategory.MCP.value,
                implementation_type=ToolImplementationType.MCP_TOOL.value,
                implementation_ref=name,
                security_level=payload.security_level.value,
                is_builtin=False,
            )
            session.add(row)
        else:
            # ON CONFLICT-style update: keep the row, refresh the operator's
            # security choice (the editable default, ADR 0052).
            row.security_level = payload.security_level.value
            row.implementation_ref = name
        result_tools.append(row)

    try:
        await session.flush()
    except IntegrityError as exc:  # pragma: no cover - racing concurrent import
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="concurrent import collided on a tool name; retry",
        ) from exc
    for row in result_tools:
        await session.refresh(row)

    return ImportMcpToolsResponse(tools=[to_tool_response(t) for t in result_tools])


# ---------------------------------------------------------------------------
# Pydantic → runtime dataclass conversion
# ---------------------------------------------------------------------------
def _to_runtime_config(payload: MCPServerConfigModel) -> MCPServerConfig:
    """Map :class:`MCPServerConfigModel` (HTTP shape, dict-friendly) to
    :class:`shared_mcp.MCPServerConfig` (frozen dataclass the SDK
    consumes). The shapes are intentionally 1:1 except for ``args``
    (list ↔ tuple) — the dataclass keeps tuple to stay hashable."""
    data = payload.model_dump()
    return MCPServerConfig(
        name=data["name"],
        transport=data["transport"],
        command=data.get("command"),
        args=tuple(data.get("args") or ()),
        env=dict(data.get("env") or {}),
        url=data.get("url"),
        headers=dict(data.get("headers") or {}),
        auth_ref=data.get("auth_ref"),
        timeout_s=float(data.get("timeout_s", 30.0)),
    )


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
