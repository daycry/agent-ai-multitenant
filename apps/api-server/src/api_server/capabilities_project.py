"""Capacidades de un PROYECTO: MCP con procedencia, tools y skills desplegadas (`task_ui_20`).

Amplía el contrato del Hub (`capabilities.CapabilitiesResponse`) de forma ADITIVA: los tres
campos son opcionales y sólo el endpoint de proyecto los rellena, así que agents y teams no
cambian. Todo sale del dominio, nada se inventa:

* **procedencia** del servidor: ``declared`` (entrada de `projects.mcp_servers` sin despliegue
  que la reclame) o ``marketplace`` (un despliegue ACTIVO cuyo `created_refs.mcp_servers` la
  incluye: listing, versión desplegada);
* **conexión**: la regla OAuth compartida (`mcp.oauth_probe`); `connected` es ``None`` si el
  servidor no usa OAuth;
* **N tools importadas**: `count_imported_tools` (estado derivado, ADR 0166 D4);
* **egress**: `egress_warnings_for_servers` (D11), con la allowlist de plataforma.

No existe en el dominio un «último probado» de la conexión (Probar no persiste resultado),
así que no se expone: el panel ofrece Probar y muestra el resultado de esa llamada.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from shared_mcp.catalog import uses_oauth
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Project
from api_server.db.marketplace import (
    DeploymentStatus,
    MarketplaceDeployment,
    MarketplaceInstallation,
    MarketplaceListing,
)
from api_server.db.platform_settings import get_platform_setting
from api_server.egress.mcp_allowlist import SETTING_KEY as MCP_ALLOWLIST_KEY
from api_server.egress.mcp_discovery import egress_warnings_for_servers
from api_server.mcp.import_tools import count_imported_tools
from api_server.mcp.oauth_probe import OAuthConnected, probe_many

__all__ = [
    "McpEgress",
    "McpOAuth",
    "McpOrigin",
    "McpServerCapability",
    "ProjectCapabilityExtras",
    "SkillCapability",
    "ToolCapability",
    "build_project_capability_extras",
]


class McpOrigin(BaseModel):
    """De dónde salió el servidor: declarado a mano o desplegado desde un listing."""

    model_config = ConfigDict(populate_by_name=True)

    kind: str  # "declared" | "marketplace"
    deployment_id: UUID | None = None
    listing_id: UUID | None = None
    listing_name: str | None = None
    version: str | None = None


class McpOAuth(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    required: bool
    #: ``None`` cuando el servidor no usa OAuth (no hay conexión que medir) o, con
    #: ``required``, cuando se desconoce (Vault sellado o caído, entrada ilegible): no es
    #: lo mismo que «no conectado» y el panel no invita a reconectar.
    connected: bool | None = None


class McpEgress(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    blocked: bool
    host: str | None = None
    code: str | None = None


class McpServerCapability(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    transport: str | None = None
    url: str | None = None
    origin: McpOrigin
    oauth: McpOAuth
    imported_tools: int
    egress: McpEgress


class ToolCapability(BaseModel):
    """Una tool desplegada en el proyecto desde el marketplace (ADR 0142)."""

    model_config = ConfigDict(populate_by_name=True)

    deployment_id: UUID
    listing_id: UUID
    name: str
    version: str
    #: Agentes del proyecto a los que este despliegue la asignó.
    agents: int


class SkillCapability(ToolCapability):
    """Igual que la tool: mismo origen, mismas asignaciones."""


class ProjectCapabilityExtras(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mcp: list[McpServerCapability] = Field(default_factory=list)
    tools: list[ToolCapability] = Field(default_factory=list)
    skills: list[SkillCapability] = Field(default_factory=list)


async def _active_deployments(
    session: AsyncSession, *, tenant_id: UUID, project_id: UUID
) -> list[tuple[MarketplaceDeployment, MarketplaceListing]]:
    rows = await session.execute(
        select(MarketplaceDeployment, MarketplaceListing)
        .join(
            MarketplaceInstallation,
            MarketplaceInstallation.id == MarketplaceDeployment.installation_id,
        )
        .join(MarketplaceListing, MarketplaceListing.id == MarketplaceInstallation.listing_id)
        .where(
            MarketplaceDeployment.tenant_id == tenant_id,
            MarketplaceDeployment.project_id == project_id,
            MarketplaceDeployment.status == DeploymentStatus.ACTIVE.value,
        )
        .order_by(MarketplaceListing.name, MarketplaceDeployment.id)
    )
    return [(d, listing) for d, listing in rows.all()]


def _ref_count(refs: dict[str, Any], key: str) -> int:
    value = refs.get(key)
    return len(value) if isinstance(value, list) else 0


def _url_of(server: dict[str, Any]) -> str | None:
    url = server.get("url")
    return url if isinstance(url, str) else None


async def build_project_capability_extras(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project: Project,
    oauth_connected: OAuthConnected,
) -> ProjectCapabilityExtras:
    """MCP + tools + skills del proyecto, con su procedencia."""
    deployments = await _active_deployments(session, tenant_id=tenant_id, project_id=project.id)

    origin_by_server: dict[str, McpOrigin] = {}
    tools: list[ToolCapability] = []
    skills: list[SkillCapability] = []
    for dep, listing in deployments:
        refs = dep.created_refs if isinstance(dep.created_refs, dict) else {}
        for server in refs.get("mcp_servers") or []:
            origin_by_server.setdefault(
                str(server),
                McpOrigin(
                    kind="marketplace",
                    deployment_id=dep.id,
                    listing_id=listing.id,
                    listing_name=listing.name,
                    version=dep.deployed_version,
                ),
            )
        if listing.kind == "tool":
            tools.append(
                ToolCapability(
                    deployment_id=dep.id,
                    listing_id=listing.id,
                    name=listing.name,
                    version=dep.deployed_version,
                    agents=_ref_count(refs, "agent_tools"),
                )
            )
        elif listing.kind == "skill":
            skills.append(
                SkillCapability(
                    deployment_id=dep.id,
                    listing_id=listing.id,
                    name=listing.name,
                    version=dep.deployed_version,
                    agents=_ref_count(refs, "agent_skills"),
                )
            )

    declared = [s for s in (project.mcp_servers or []) if isinstance(s, dict) and s.get("name")]
    names = [str(s["name"]) for s in declared]
    imported = await count_imported_tools(session, tenant_id=tenant_id, server_names=names)
    allowed = await get_platform_setting(session, MCP_ALLOWLIST_KEY, default=[])
    blocked = {
        w["server"]: w
        for w in egress_warnings_for_servers(
            declared, allowed_hosts=allowed if isinstance(allowed, list) else []
        )
    }

    oauth_states = await probe_many(
        oauth_connected,
        [str(s["name"]) for s in declared if uses_oauth(_url_of(s))],
    )
    servers: list[McpServerCapability] = []
    for s in declared:
        name = str(s["name"])
        url = s.get("url") if isinstance(s.get("url"), str) else None
        needs_oauth = bool(uses_oauth(url))
        warn = blocked.get(name)
        servers.append(
            McpServerCapability(
                name=name,
                transport=s.get("transport") if isinstance(s.get("transport"), str) else None,
                url=url,
                origin=origin_by_server.get(name, McpOrigin(kind="declared")),
                oauth=McpOAuth(
                    required=needs_oauth,
                    connected=oauth_states.get(name) if needs_oauth else None,
                ),
                imported_tools=imported.get(name, 0),
                egress=McpEgress(
                    blocked=warn is not None,
                    host=warn.get("host") if warn else None,
                    code=warn.get("code") if warn else None,
                ),
            )
        )
    return ProjectCapabilityExtras(mcp=servers, tools=tools, skills=skills)
