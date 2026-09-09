"""Importar las tools de un servidor MCP al catálogo, en un solo sitio (`task_mk_01`, ADR 0166).

Hasta el ADR 0166 esta lógica vivía en línea en `routers/mcp.py::import_mcp_tools`
y tenía UN llamante: el botón del diálogo de edición. Ahora tiene tres —el
endpoint manual, la task de la lane `marketplace` que dispara un despliegue, y el
final del «Conectar» de OAuth— y un tercer copiado es la vía por la que el
fail-closed del ADR 0101 se pierde en uno de los caminos. Por eso está aquí y el
router sólo traduce sus excepciones a HTTP.

Las reglas que este módulo hace cumplir, con su letra en el ADR:

- **D2**: `tool_names` opcional. Ausente = todas las que el servidor anuncie
  AHORA; con lista = la multiselección del ADR 0052, intacta.
- **D5**: un servidor OAuth se descubre con el proveedor montado sobre los
  tokens de Vault; sin token guardado el fallo es tipado y accionable («pulsa
  Conectar»), no un `AUTH_ERROR` crudo. La ruta de Vault se DERIVA de
  (tenant, proyecto, nombre): nunca se acepta montada.
- **D7**: R1 alta por upsert `(tenant, name)`; R2 refresco de `input_schema` y
  `description` sin tocar `security_level` salvo elección explícita; R3 retirada
  (soft) de lo que el servidor ya no anuncia, SÓLO sin `tool_names` y SÓLO tras
  un discovery completo; R4 retirada de las filas de un servidor que sale del
  proyecto, salvo que otro proyecto vivo del tenant lo declare.
- **D8**: fail-closed — discovery caído ⇒ cero filas y error tipado.
- **D9**: L1 tope de 200 con ABSTENCIÓN (no truncado); L2 32 KiB por schema y
  L3 nombre ≤ 120, ambos por omisión con aviso. Nunca se trunca ni se sufija:
  un nombre que no es `<server>.<tool>` es invisible al runtime.
- **D9/ADR 0165**: el descubrimiento sale por el egress-proxy cuando el host
  es externo, con la misma regla que el run.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from shared_mcp import DiscoveryResult, MCPServerConfig, MCPTool, VaultResolver, discover_tools
from shared_mcp.catalog import uses_oauth
from shared_mcp.oauth import VaultTokenStorage, build_oauth_provider, oauth_vault_path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import (
    Project,
    Tool,
    ToolCategory,
    ToolImplementationType,
    ToolSecurityLevel,
)
from api_server.db.models import AuditLog
from api_server.egress.mcp_discovery import (
    DiscoveryFailure,
    EgressProxyNotConfiguredError,
    classify_discovery_failure,
    egress_client_factory_for,
)
from api_server.mcp.config import MCPServerConfigModel
from api_server.schemas.catalog import normalize_tool_name

__all__ = [
    "MAX_INPUT_SCHEMA_BYTES",
    "MAX_TOOLS_PER_SERVER",
    "MAX_TOOL_NAME_LEN",
    "OAUTH_NOT_CONNECTED",
    "RETIRED_AUDIT_ACTION",
    "TOO_MANY_TOOLS",
    "ConfigInvalidError",
    "DiscoveryFailedError",
    "ImportAbstainedError",
    "ImportOutcome",
    "ImportPlan",
    "Provenance",
    "ServerNotDeclaredError",
    "count_imported_tools",
    "declared_server",
    "import_server_tools",
    "plan_import",
    "retire_server_tools",
    "to_runtime_config",
]

#: L1 — el `max_length=200` que `tool_names` ya tenía, aplicado al camino automático.
MAX_TOOLS_PER_SERVER = 200
#: L2 — el schema viaja al anuncio del modelo y se paga en tokens cada turno.
MAX_INPUT_SCHEMA_BYTES = 32 * 1024
#: L3 — `Tool.name` es `String(120)`; un nombre que no cabe revienta con `DataError`.
MAX_TOOL_NAME_LEN = 120

#: Códigos tipados que este módulo añade a los de `McpTestConnectionError`.
OAUTH_NOT_CONNECTED: Final = "OAUTH_NOT_CONNECTED"
TOO_MANY_TOOLS: Final = "TOO_MANY_TOOLS"

#: `action` de la fila de `audit_log` que deja toda retirada (R3/R4).
RETIRED_AUDIT_ACTION = "mcp.tools_retired"

DiscoverFn = Callable[..., Awaitable[DiscoveryResult]]


# ---------------------------------------------------------------------------
# Excepciones (el router las traduce; la task las registra)
# ---------------------------------------------------------------------------
class ServerNotDeclaredError(LookupError):
    """El proyecto no declara un servidor con ese nombre."""


class ConfigInvalidError(ValueError):
    """La entrada guardada no pasa `MCPServerConfigModel` (una fila anterior a la
    regla de forma del ADR 0165 D11, por ejemplo)."""


class DiscoveryFailedError(RuntimeError):
    """El descubrimiento no terminó: cero filas (fail-closed, ADR 0101/0166 D8)."""

    def __init__(self, failure: DiscoveryFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


class ImportAbstainedError(RuntimeError):
    """L1: el servidor anuncia más tools que el tope; el automatismo se apaga y
    pide selección manual. No es un error del servidor ni del transporte."""


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Provenance:
    """Lo que estampa un import disparado por un despliegue (ADR 0166 D6), para
    que `dematerialize_installation` retire exactamente lo que creó."""

    listing_id: UUID
    installation_id: UUID
    version: str | None


@dataclass
class ImportPlan:
    """Qué se va a upsertear y qué se omite, decidido SIN tocar la base de datos."""

    #: `namespaced -> spec anunciado` (o None cuando se pidió un nombre que el
    #: servidor no anuncia: degradación del ADR 0052 para selección explícita).
    to_upsert: dict[str, MCPTool | None] = field(default_factory=dict)
    omitted: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: `True` sólo cuando `tool_names` era None: es la condición 1 de R3.
    reconcile: bool = False


@dataclass
class ImportOutcome:
    tools: list[Tool] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Puras
# ---------------------------------------------------------------------------
def declared_server(project: Project, server_name: str) -> dict[str, Any]:
    for raw in project.mcp_servers or []:
        if isinstance(raw, dict) and str(raw.get("name")) == server_name:
            return raw
    raise ServerNotDeclaredError(f"MCP server {server_name!r} not declared on this project")


def to_runtime_config(payload: MCPServerConfigModel) -> MCPServerConfig:
    """`MCPServerConfigModel` (forma HTTP) → `shared_mcp.MCPServerConfig` (dataclass
    que consume el SDK). 1:1 salvo `args` (list ↔ tuple, para que sea hashable)."""
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
        max_output_bytes=int(data.get("max_output_bytes", 65536)),
    )


def _schema_bytes(schema: Any) -> int:
    try:
        return len(json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode())
    except (TypeError, ValueError):
        return MAX_INPUT_SCHEMA_BYTES + 1


def plan_import(
    *,
    server_name: str,
    announced: Sequence[MCPTool],
    tool_names: Sequence[str] | None,
) -> ImportPlan:
    """Decide qué entra, qué se omite y con qué avisos (L1, L2, L3, D2).

    Levanta :class:`ImportAbstainedError` cuando `tool_names` es None y el servidor
    anuncia más de :data:`MAX_TOOLS_PER_SERVER`: el automatismo se apaga justo
    donde la objeción de «ruido y superficie» del ADR 0052 empieza a ser cierta.
    """
    by_name = {t.name: t for t in announced}
    plan = ImportPlan(reconcile=tool_names is None)

    if tool_names is None:
        if len(by_name) > MAX_TOOLS_PER_SERVER:
            raise ImportAbstainedError(
                f"el servidor {server_name!r} anuncia {len(by_name)} tools, por encima del "
                f"límite de {MAX_TOOLS_PER_SERVER}: elige cuáles importar (selección manual)"
            )
        requested: list[str] = list(by_name)
    else:
        requested = []
        for raw in tool_names:
            if raw not in requested:
                requested.append(raw)

    for raw in requested:
        slug = normalize_tool_name(raw)
        if not slug:
            continue
        namespaced = f"{server_name}.{slug}"
        if len(namespaced) > MAX_TOOL_NAME_LEN:
            plan.omitted.append(raw)
            plan.warnings.append(
                f"la tool {raw!r} se omite: `{namespaced}` pasa de {MAX_TOOL_NAME_LEN} "
                "caracteres (`Tool.name`) y no se trunca — un nombre truncado colisiona en el "
                "índice único y uno sufijado deja de ser `<server>.<tool>`"
            )
            continue
        spec = by_name.get(raw)
        if spec is not None and _schema_bytes(spec.input_schema) > MAX_INPUT_SCHEMA_BYTES:
            plan.omitted.append(raw)
            plan.warnings.append(
                f"la tool {raw!r} se omite: su `input_schema` pasa de "
                f"{MAX_INPUT_SCHEMA_BYTES // 1024} KiB y se pagaría en tokens en cada turno; "
                "importarla con `{}` recrearía el defecto del ADR 0101"
            )
            continue
        plan.to_upsert[namespaced] = spec
    return plan


def _server_origin(url: str | None) -> str:
    parts = urlsplit(url or "")
    return f"{parts.scheme}://{parts.netloc}"


# ---------------------------------------------------------------------------
# OAuth (D5)
# ---------------------------------------------------------------------------
async def _oauth_auth_for(
    *,
    tenant_id: UUID,
    project_id: UUID,
    server_name: str,
    url: str | None,
    resolver: VaultResolver | None,
) -> httpx.Auth | None:
    """El proveedor OAuth del servidor, o None si el servidor no usa OAuth.

    Fail-closed tipado: sin Vault o sin token guardado no se intenta descubrir.
    """
    if not uses_oauth(url):
        return None
    if resolver is None:
        raise DiscoveryFailedError(
            DiscoveryFailure(
                401,
                OAUTH_NOT_CONNECTED,
                f"el servidor {server_name!r} usa OAuth y el api-server no tiene Vault configurado "
                "(API_SERVER_VAULT_TOKEN): no hay dónde leer el token",
            )
        )
    storage = VaultTokenStorage(
        resolver,
        oauth_vault_path(
            tenant_id=str(tenant_id), project_id=str(project_id), server_name=server_name
        ),
    )
    if await storage.get_tokens() is None:
        raise DiscoveryFailedError(
            DiscoveryFailure(
                401,
                OAUTH_NOT_CONNECTED,
                f"el servidor {server_name!r} usa OAuth y no está conectado: pulsa «Conectar» en "
                "la pestaña MCP del proyecto y el import se disparará al completarlo",
            )
        )
    return build_oauth_provider(
        server_url=_server_origin(url),
        storage=storage,
        # Sólo hace falta para un flujo interactivo, que aquí no puede ocurrir: los
        # dos handlers del proveedor levantan si se les llama.
        redirect_uri="http://127.0.0.1/oauth/non-interactive",
    )


# ---------------------------------------------------------------------------
# La operación
# ---------------------------------------------------------------------------
async def import_server_tools(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project: Project,
    server_name: str,
    tool_names: Sequence[str] | None = None,
    security_level: ToolSecurityLevel | None = None,
    resolver: VaultResolver | None,
    proxy_url: str | None,
    provenance: Provenance | None = None,
    discover: DiscoverFn = discover_tools,
    actor_user_id: UUID | None = None,
) -> ImportOutcome:
    """Descubre y upsertea las tools de `server_name` en el catálogo del tenant.

    El caller es dueño de la transacción. Un `IntegrityError` en el `flush`
    (carrera por `(tenant, name)`) se propaga: el router lo convierte en 409 y la
    task de la lane lo absorbe con reintento (R5).

    ``security_level`` = None significa «no tocar»: en el camino automático no
    hay elección humana que aplicar, y las filas nuevas nacen `sandboxed`.
    """
    raw = declared_server(project, server_name)
    try:
        model = MCPServerConfigModel.model_validate(raw)
    except ValueError as exc:
        raise ConfigInvalidError(str(exc)) from exc
    runtime_config = to_runtime_config(model)

    auth = await _oauth_auth_for(
        tenant_id=tenant_id,
        project_id=project.id,
        server_name=server_name,
        url=model.url,
        resolver=resolver,
    )
    try:
        factory = egress_client_factory_for(runtime_config, proxy_url)
    except EgressProxyNotConfiguredError as exc:
        raise DiscoveryFailedError(
            DiscoveryFailure(502, "EGRESS_PROXY_UNAVAILABLE", str(exc))
        ) from exc
    try:
        discovery = await discover(
            runtime_config,
            vault_resolver=resolver,
            httpx_client_factory=factory,
            auth=auth,
        )
    except Exception as exc:
        raise DiscoveryFailedError(
            classify_discovery_failure(exc, config=runtime_config, proxied=factory is not None)
        ) from exc

    plan = plan_import(server_name=server_name, announced=discovery.tools, tool_names=tool_names)
    outcome = ImportOutcome(omitted=list(plan.omitted), warnings=list(plan.warnings))

    live_rows = (
        (
            await session.execute(
                select(Tool).where(
                    Tool.tenant_id == tenant_id,
                    Tool.implementation_type == ToolImplementationType.MCP_TOOL.value,
                    Tool.deleted_at.is_(None),
                    Tool.name.like(f"{server_name}.%"),
                )
            )
        )
        .scalars()
        .all()
    )
    by_name = {row.name: row for row in live_rows}

    for name, spec in plan.to_upsert.items():
        raw_name = name.split(".", 1)[1]
        input_schema = dict(spec.input_schema) if spec is not None else {}
        description = (
            spec.description
            if spec is not None and spec.description
            else f"MCP tool {raw_name!r} from server {server_name!r}"
        )
        row = by_name.get(name)
        if row is None:
            row = Tool(
                tenant_id=tenant_id,
                name=name,
                description=description,
                category=ToolCategory.MCP.value,
                implementation_type=ToolImplementationType.MCP_TOOL.value,
                implementation_ref=name,
                input_schema=input_schema,
                security_level=(security_level or ToolSecurityLevel.SANDBOXED).value,
                is_builtin=False,
            )
            session.add(row)
            outcome.created.append(name)
        else:
            # R2: se refresca lo que el servidor dice de sí mismo; el nivel de
            # seguridad sólo si alguien lo eligió explícitamente.
            row.implementation_ref = name
            row.input_schema = input_schema
            row.description = description
            if security_level is not None:
                row.security_level = security_level.value
            outcome.refreshed.append(name)
        if provenance is not None:
            row.source_listing_id = provenance.listing_id
            row.source_installation_id = provenance.installation_id
            row.source_version = provenance.version
        outcome.tools.append(row)

    # R3: sólo sin selección explícita y sólo tras un discovery completo (si no lo
    # fue, ya hemos levantado antes de llegar aquí).
    if plan.reconcile:
        now = datetime.now(UTC)
        for name, row in by_name.items():
            if name not in plan.to_upsert:
                row.deleted_at = now
                outcome.retired.append(name)
        if outcome.retired:
            outcome.retired.sort()
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    user_id=actor_user_id,
                    action=RETIRED_AUDIT_ACTION,
                    resource_type="mcp_server",
                    changes={
                        "server": server_name,
                        "reason": "no longer announced",
                        "tools": list(outcome.retired),
                    },
                )
            )

    await session.flush()
    return outcome


async def count_imported_tools(
    session: AsyncSession, *, tenant_id: UUID, server_names: Iterable[str]
) -> dict[str, int]:
    """«N tools importadas» por servidor: un COUNT de filas vivas con prefijo
    `<server>.` — el mismo criterio que `_project_mcp_tool_rows`. Es el estado
    DERIVADO del ADR 0166 D4: no hay columna que se pueda quedar atascada."""
    wanted = {str(n) for n in server_names if n}
    if not wanted:
        return {}
    names = (
        (
            await session.execute(
                select(Tool.name).where(
                    Tool.tenant_id == tenant_id,
                    Tool.implementation_type == ToolImplementationType.MCP_TOOL.value,
                    Tool.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    counts = dict.fromkeys(wanted, 0)
    for name in names:
        if "." in name:
            prefix = name.split(".", 1)[0]
            if prefix in counts:
                counts[prefix] += 1
    return counts


async def retire_server_tools(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project: Project,
    server_names: Iterable[str],
    actor_user_id: UUID | None = None,
) -> dict[str, list[str]]:
    """R4: al retirar un servidor del proyecto, sus filas `<server>.*` se soft-borran
    y sus claves salen de `mcp_tool_roles` — SALVO que otro proyecto vivo del
    tenant declare un servidor con ese nombre (las filas son de tenant).

    Devuelve `{server: [tools retiradas]}`; los servidores que otro proyecto
    sigue declarando no aparecen.
    """
    candidates = {str(n) for n in server_names if n}
    if not candidates:
        return {}

    others = (
        (
            await session.execute(
                select(Project.mcp_servers).where(
                    Project.tenant_id == tenant_id,
                    Project.id != project.id,
                    Project.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    still_declared: set[str] = set()
    for servers in others:
        for raw in servers or []:
            if isinstance(raw, dict) and raw.get("name"):
                still_declared.add(str(raw["name"]))
    to_retire = sorted(candidates - still_declared)
    if not to_retire:
        return {}

    rows = (
        (
            await session.execute(
                select(Tool).where(
                    Tool.tenant_id == tenant_id,
                    Tool.implementation_type == ToolImplementationType.MCP_TOOL.value,
                    Tool.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    retired: dict[str, list[str]] = {}
    for row in rows:
        if "." not in row.name:
            continue
        prefix = row.name.split(".", 1)[0]
        if prefix in to_retire:
            row.deleted_at = now
            retired.setdefault(prefix, []).append(row.name)

    policy = {
        str(k): list(v) for k, v in (project.mcp_tool_roles or {}).items() if isinstance(v, list)
    }
    cleaned = {k: v for k, v in policy.items() if k.split(".", 1)[0] not in to_retire}
    if cleaned != policy:
        project.mcp_tool_roles = cleaned

    for server, names in retired.items():
        names.sort()
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                user_id=actor_user_id,
                action=RETIRED_AUDIT_ACTION,
                resource_type="mcp_server",
                resource_id=project.id,
                changes={"server": server, "reason": "server removed from project", "tools": names},
            )
        )
    if retired or cleaned != policy:
        await session.flush()
    return retired
