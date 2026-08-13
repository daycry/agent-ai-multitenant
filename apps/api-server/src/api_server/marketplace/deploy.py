"""Despliegue de una instalación en un proyecto — el corazón del ADR 0142.

Instalar añade la capacidad al fondo del tenant; **desplegar** escribe las filas
concretas que hacen que un agente pueda usarla. Ese último tramo es el que no
existía (el marketplace materializaba una fila `Tool` que nadie tenía asignada y
no configuraba ningún servidor MCP en ningún proyecto), y es todo lo que hace
este módulo.

## Qué se materializa, por tipo

* `mcp_server` → entrada en `projects.mcp_servers` **+** la política rol→tool
  en `projects.mcp_tool_roles`.
* `tool` → filas `agent_tools` para los agentes del proyecto cuyo rol esté en el
  `role_map`.
* `skill` → filas `agent_skills`, ídem.

**Sin política paralela** (ADR 0142 §4, aviso explícito del plan): para un
`mcp_server` el `role_map` se materializa escribiendo la política que YA existe
—`projects.mcp_tool_roles`, ADR 0128 fase 2— y **no** filas `agent_tools`. La
fase 3 del ADR 0128 retiró justamente el grant por-agente de las tools MCP; que
el despliegue lo resucitara sería el mecanismo competidor que el plan prohíbe.
El diseño lo dice sin ambigüedad («`mcp_server` → entrada en
`Project.mcp_servers`; `tool`/`skill` → asignación a los agentes»).

## La retirada es EXACTA

Cada fila escrita se anota en `deployment.created_refs`, y `retire_deployment`
deshace **exactamente eso**. Corolario que se comprueba con un test: si el
operador ya había asignado a mano la misma tool al mismo agente, el despliegue
**no la anota** (no la creó él), así que retirar no se la lleva. Lo mismo con un
servidor MCP ya declarado en el proyecto: se avisa y no se toca.

## Idempotencia

Un segundo despliegue activo del mismo par (instalación, proyecto) es un **no-op
con aviso** (`already_deployed=True`), y no porque este código lo compruebe
—que lo comprueba— sino porque el índice UNIQUE PARCIAL
`uq_marketplace_deployments_active` lo impide en la base de datos. La
comprobación en Python da el mensaje bonito; el índice da la garantía bajo
concurrencia.

## OAuth

Si el servidor MCP desplegado usa OAuth (se resuelve por URL contra el catálogo,
`mcp_oauth_flow.uses_oauth`), el resultado viaja con `oauth_pending=True`: la
entrada nace declarada pero **sin estado OAuth** hasta que el flujo «Conectar»
del ADR 0127 lo complete. No se inventa un campo `pending_connection` en la
entrada porque `MCPServerConfigModel` es `extra="forbid"` y su forma es contrato
con el runtime; el estado «pendiente» es una lectura derivada, no un dato nuevo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Agent, AgentSkill, AgentTool, Project, Skill, TeamMember, Tool
from api_server.db.marketplace import (
    DeploymentStatus,
    InstallationStatus,
    MarketplaceAuditAction,
    MarketplaceAuditEntry,
    MarketplaceDeployment,
    MarketplaceInstallation,
    MarketplaceListing,
    MarketplaceListingKind,
    MarketplaceListingVersion,
)
from api_server.marketplace.config_schema import (
    apply_defaults,
    validate_deployment_config,
)
from api_server.mcp.config import validate_mcp_servers_payload

logger = structlog.get_logger("api_server.marketplace.deploy")

#: Clave comodín del `role_map`: «estos roles reciben TODO lo de este listing».
#: Existe porque un `mcp_server` reparte N tools cuyos nombres no se conocen
#: hasta que el servidor se importa, así que enumerarlas al desplegar es
#: imposible en el caso general.
ROLE_MAP_WILDCARD = "*"

#: Campos del `config` del despliegue que se superponen sobre la entrada de
#: `projects.mcp_servers`. Cerrado a propósito: cualquier otra clave del
#: `config_schema` (una `base_url` de Playwright, por ejemplo) se queda en
#: `deployment.config` y NO se cuela en la config del servidor MCP, cuya forma es
#: contrato con el runtime (`MCPServerConfigModel`, `extra="forbid"`).
MCP_ENTRY_OVERLAY_KEYS: frozenset[str] = frozenset(
    {
        "url",
        "command",
        "args",
        "env",
        "headers",
        "auth_ref",
        "timeout_s",
        "max_output_bytes",
        "transport",
    }
)


class DeployError(ValueError):
    """El despliegue no se puede hacer. Lleva el código HTTP que le corresponde.

    Subclasea :class:`ValueError` para no romper los `except ValueError` que ya
    mapean a 422 en los routers, pero el router de despliegues usa
    :attr:`status_code` para distinguir un 404 de un 409 de un 422.
    """

    status_code: int = 422

    def __init__(self, message: str, *, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class DeployNotFoundError(DeployError):
    """La instalación o el proyecto no existen **para esta sesión**.

    Cross-tenant incluido: la RLS esconde lo del otro tenant, así que intentar
    desplegar la instalación del tenant A en un proyecto del B llega aquí como
    «no existe» — que es la respuesta correcta (un 403 confirmaría que existe).
    """

    status_code = 404


class DeployConflictError(DeployError):
    """El estado no admite el despliegue (instalación no habilitada, etc.)."""

    status_code = 409


@dataclass(frozen=True)
class DeploymentResult:
    """Qué produjo el despliegue. Viaja a la respuesta HTTP y al audit row."""

    deployment_id: UUID
    kind: str
    deployed_version: str
    #: ``True`` cuando ya había un despliegue ACTIVO del mismo par: no se
    #: escribió nada y ``deployment_id`` es el del que ya existía.
    already_deployed: bool = False
    created_refs: dict[str, Any] = field(default_factory=dict)
    #: Avisos legibles: lo que el despliegue NO pudo hacer sin que sea un error
    #: (un servidor ya declarado, una capacidad diferida por falta de sandbox,
    #: un `role_map` vacío…). Se enseñan; no se tragan.
    warnings: tuple[str, ...] = ()
    #: El servidor MCP usa OAuth y la conexión está pendiente (ADR 0127).
    oauth_pending: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "deployment_id": str(self.deployment_id),
            "kind": self.kind,
            "deployed_version": self.deployed_version,
            "already_deployed": self.already_deployed,
            "created_refs": self.created_refs,
            "warnings": list(self.warnings),
            "oauth_pending": self.oauth_pending,
        }


# ---------------------------------------------------------------------------
# Versiones: get-or-create + pin
# ---------------------------------------------------------------------------
async def ensure_listing_version(
    session: AsyncSession,
    *,
    listing: MarketplaceListing,
    version: str,
) -> MarketplaceListingVersion | None:
    """La fila de versión de `(listing, version)`; la crea SOLO si le toca.

    El backfill de la migración 0128 dejó una fila por cada listing e
    instalación que existía. Un listing publicado DESPUÉS —y antes de que la
    fase 3 cablee la creación en el flujo de publicación— no la tiene, así que
    aquí se crea a partir del manifest vigente… **pero solo si el listing es
    PRIVADO del tenant de la sesión**.

    Por qué la excepción, y no es un rodeo: una versión de un listing GLOBAL
    (`tenant_id IS NULL`) es el registro de lo que la PLATAFORMA publicó, y
    escribirla está reservado al publicador BYPASSRLS — la policy
    `marketplace_listing_versions_tenant_isolation` rechaza el `WITH CHECK` de
    una sesión de tenant, igual que hace con `marketplace_listings`. Intentarlo
    reventaba el despliegue entero de cualquier listing del catálogo oficial
    (medido: `InsufficientPrivilegeError` en el primer deploy de Playwright).
    Que un tenant pudiera fabricar el histórico del catálogo global sería un
    agujero, no una comodidad.

    Devuelve ``None`` cuando no hay fila y no le corresponde crearla: el
    despliegue sigue adelante leyendo el manifest VIVO del listing y deja el pin
    como estaba. Es una degradación honesta, no un fallo.

    Idempotente por el UNIQUE `(listing_id, version)`.
    """
    existing = (
        await session.execute(
            select(MarketplaceListingVersion).where(
                MarketplaceListingVersion.listing_id == listing.id,
                MarketplaceListingVersion.version == version,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    if listing.tenant_id is None:
        logger.info(
            "marketplace_global_listing_version_absent",
            listing_id=str(listing.id),
            version=version,
            reason="authoring a global-catalog version row is the publisher's job (fase 3)",
        )
        return None

    manifest: dict[str, Any] = dict(listing.manifest or {})
    raw_schema = manifest.get("config_schema")
    row = MarketplaceListingVersion(
        listing_id=listing.id,
        tenant_id=listing.tenant_id,
        version=version,
        manifest=manifest,
        requested_permissions=list(listing.requested_permissions or []),
        config_schema=dict(raw_schema) if isinstance(raw_schema, dict) else None,
        changelog=None,
    )
    session.add(row)
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# role_map
# ---------------------------------------------------------------------------
def normalize_role_map(
    raw: Any,
    *,
    targets: list[str] | tuple[str, ...] = (),
) -> dict[str, list[str]]:
    """Normaliza el `role_map` a `{clave: [rol, …]}`.

    Acepta tres formas, porque las tres puertas de UI mandan lo natural en cada
    sitio:

      * ``None`` / ``{}`` → se cae a ``targets`` del manifest bajo el comodín
        (decisión D5: el manifest sugiere, quien despliega confirma). Sin
        `targets`, mapa vacío = no se asigna a nadie (con aviso);
      * una LISTA de roles → ``{"*": roles}``;
      * un MAPPING ``{clave: [roles]}`` tal cual.

    No valida los roles contra `AgentRole`: los `targets` ya pasaron por el
    parser del manifest, y un rol inventado aquí simplemente no casa con ningún
    agente — se refleja en el aviso «0 agentes» en vez de en un 422 que impida
    desplegar por un rol que el tenant sí usa.
    """
    if raw is None or raw in ({}, []):
        return {ROLE_MAP_WILDCARD: list(targets)} if targets else {}
    if isinstance(raw, list):
        roles = [str(r).strip() for r in raw if isinstance(r, str) and r.strip()]
        return {ROLE_MAP_WILDCARD: roles} if roles else {}
    if not isinstance(raw, dict):
        raise DeployError("role_map debe ser un objeto {clave: [roles]} o una lista de roles")
    out: dict[str, list[str]] = {}
    for key, declared in raw.items():
        value = [declared] if isinstance(declared, str) else declared
        if not isinstance(value, list):
            raise DeployError(f"role_map[{key!r}] debe ser una lista de roles")
        out[str(key)] = [str(r).strip() for r in value if isinstance(r, str) and r.strip()]
    return out


def roles_for(role_map: dict[str, list[str]], capability: str) -> list[str]:
    """Los roles que reciben `capability`: entrada propia, o el comodín."""
    if capability in role_map:
        return role_map[capability]
    return role_map.get(ROLE_MAP_WILDCARD, [])


# ---------------------------------------------------------------------------
# Los agentes del proyecto
# ---------------------------------------------------------------------------
async def _project_agents(session: AsyncSession, project: Project) -> list[Agent]:
    """Los agentes «del proyecto»: los del equipo asignado + los `project_local`.

    Los dos conjuntos, unidos y sin repetir, porque las dos formas de tener
    agentes en un proyecto conviven en este repo: un equipo compartido
    (`team_members` del `project.team_id`) y los forks locales del proyecto
    (`agents.project_id`, ADR 0066). Quedarse solo con el primero dejaría sin
    capacidad a los proyectos que forkearon su equipo.
    """
    by_id: dict[UUID, Agent] = {}

    if project.team_id is not None:
        rows = (
            (
                await session.execute(
                    select(Agent)
                    .join(TeamMember, TeamMember.agent_id == Agent.id)
                    .where(
                        TeamMember.team_id == project.team_id,
                        Agent.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for agent in rows:
            by_id[agent.id] = agent

    local = (
        (
            await session.execute(
                select(Agent).where(
                    Agent.project_id == project.id,
                    Agent.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for agent in local:
        by_id[agent.id] = agent

    return sorted(by_id.values(), key=lambda a: str(a.id))


# ---------------------------------------------------------------------------
# Materialización por tipo
# ---------------------------------------------------------------------------
def _mcp_entry_name(listing: MarketplaceListing, manifest: dict[str, Any]) -> str:
    """El nombre de la entrada en `projects.mcp_servers`.

    Se respeta lo que el manifest declare (`mcp_server.name`); si no declara
    nada, el nombre del listing saneado al patrón que `MCPServerConfigModel`
    exige (`^[a-zA-Z][a-zA-Z0-9_\\-.]{0,63}$`).
    """
    block = manifest.get("mcp_server")
    if isinstance(block, dict):
        declared = block.get("name")
        if isinstance(declared, str) and declared.strip():
            return declared.strip()[:64]
    sane = "".join(ch if (ch.isalnum() or ch in "_-.") else "-" for ch in listing.name)
    sane = sane.lstrip("-._0123456789") or "mcp-server"
    return sane[:64]


def _build_mcp_entry(
    listing: MarketplaceListing,
    manifest: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    """La entrada de `projects.mcp_servers`: base del manifest + overlay del config.

    El overlay está acotado a :data:`MCP_ENTRY_OVERLAY_KEYS`. Lo demás del
    `config` (los valores propios de la capacidad) se queda en el despliegue: la
    entrada del servidor MCP tiene `extra="forbid"` y una clave de más la
    rechazaría entera.
    """
    base = manifest.get("mcp_server")
    entry: dict[str, Any] = dict(base) if isinstance(base, dict) else {}
    entry["name"] = _mcp_entry_name(listing, manifest)
    entry.setdefault("transport", "streamable_http")
    for key in MCP_ENTRY_OVERLAY_KEYS:
        if key in values and values[key] is not None:
            entry[key] = values[key]
    return entry


async def _materialize_mcp_server(
    session: AsyncSession,
    *,
    project: Project,
    listing: MarketplaceListing,
    manifest: dict[str, Any],
    values: dict[str, Any],
    role_map: dict[str, list[str]],
) -> tuple[dict[str, Any], list[str], bool]:
    """`projects.mcp_servers` + `projects.mcp_tool_roles`. Nada más.

    Devuelve ``(created_refs, warnings, oauth_pending)``.
    """
    refs: dict[str, Any] = {}
    warnings: list[str] = []

    entry = _build_mcp_entry(listing, manifest, values)
    name = str(entry["name"])
    current = [dict(s) for s in (project.mcp_servers or []) if isinstance(s, dict)]
    declared = {str(s.get("name")) for s in current}

    if name in declared:
        # Exactitud de la retirada: no se anota lo que no se creó.
        warnings.append(
            f"el proyecto ya declaraba un servidor MCP llamado {name!r}: se deja"
            " intacto y la retirada de este despliegue NO lo quitará"
        )
    else:
        try:
            project.mcp_servers = validate_mcp_servers_payload([*current, entry])
        except ValueError as exc:
            raise DeployError(
                f"la entrada MCP que produce este despliegue no es válida: {exc}"
            ) from exc
        refs["mcp_servers"] = [name]

    # --- la política rol→tool, en el sitio de siempre (ADR 0128) -----------
    roles = roles_for(role_map, name)
    if roles:
        tool_names = await _namespaced_tool_names(session, project, server_name=name)
        if not tool_names:
            warnings.append(
                f"el servidor {name!r} aún no tiene tools importadas en el catálogo"
                " (`<servidor>.<tool>`), así que no hay nada que restringir por rol:"
                " tras importarlas, vuelve a desplegar para aplicar el role_map"
            )
        else:
            policy = {
                str(k): list(v)
                for k, v in (project.mcp_tool_roles or {}).items()
                if isinstance(v, list)
            }
            written: list[str] = []
            for tool_name in tool_names:
                if tool_name in policy:
                    warnings.append(
                        f"la tool {tool_name!r} ya tenía política de roles: se respeta"
                        " y la retirada no la tocará"
                    )
                    continue
                policy[tool_name] = list(roles)
                written.append(tool_name)
            if written:
                project.mcp_tool_roles = policy
                refs["mcp_tool_roles"] = written
    else:
        warnings.append(
            "sin roles en el role_map: la política de `mcp_tool_roles` no se escribe,"
            " así que las tools quedan abiertas a todos los roles del proyecto (el"
            " default del ADR 0128)"
        )

    oauth_pending = _uses_oauth(entry.get("url"))
    if oauth_pending:
        warnings.append(
            "el servidor declara OAuth: la entrada nace SIN estado de conexión;"
            " compléta el flujo «Conectar» (ADR 0127) en la pestaña MCP del proyecto"
        )

    await session.flush()
    return refs, warnings, oauth_pending


def _uses_oauth(url: Any) -> bool:
    """¿Es una URL de servidor MCP con OAuth según el catálogo (ADR 0127)?

    Import perezoso y del ORIGEN (`shared_mcp.catalog`), no del re-export de
    `mcp_oauth_flow`: ese módulo arrastra el cliente MCP entero y este se
    importa desde el arranque del router.
    """
    if not isinstance(url, str) or not url:
        return False
    from shared_mcp.catalog import uses_oauth

    try:
        return bool(uses_oauth(url))
    except Exception:  # pragma: no cover - el catálogo nunca debe tumbar un deploy
        logger.warning("mcp_oauth_lookup_failed", url=url)
        return False


async def _namespaced_tool_names(
    session: AsyncSession, project: Project, *, server_name: str
) -> list[str]:
    """Las tools `<server>.<tool>` del catálogo del tenant para ese servidor.

    Es el MISMO criterio que `agent_tools_enforcement._project_mcp_tool_rows`
    usa para decidir qué tools aporta el proyecto — la política se escribe sobre
    las claves que el runtime va a consultar, no sobre unas inventadas.
    """
    rows = (
        (
            await session.execute(
                select(Tool.name).where(
                    Tool.tenant_id == project.tenant_id,
                    Tool.implementation_type == "mcp_tool",
                    Tool.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    prefix = f"{server_name}."
    return sorted(name for name in rows if name.startswith(prefix))


async def _materialized_catalog_row(
    session: AsyncSession, *, installation_id: UUID, is_skill: bool
) -> tuple[UUID, str] | None:
    """`(id, nombre)` de la fila `Skill`/`Tool` que el ADR 0100 materializó.

    Ramas explícitas en vez de una variable `type[Skill] | type[Tool]`: mypy
    strict resuelve esa unión a `Base` y pierde todas las columnas.
    """
    if is_skill:
        skill_row = (
            await session.execute(
                select(Skill).where(
                    Skill.source_installation_id == installation_id,
                    Skill.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        return None if skill_row is None else (skill_row.id, skill_row.name)
    tool_row = (
        await session.execute(
            select(Tool).where(
                Tool.source_installation_id == installation_id,
                Tool.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    return None if tool_row is None else (tool_row.id, tool_row.name)


async def _already_granted(
    session: AsyncSession, *, agent_id: UUID, row_id: UUID, is_skill: bool
) -> bool:
    """¿Ya existe la asignación? Determina si el despliegue puede reclamarla."""
    if is_skill:
        found = (
            await session.execute(
                select(AgentSkill.agent_id).where(
                    AgentSkill.agent_id == agent_id,
                    AgentSkill.skill_id == row_id,
                )
            )
        ).first()
    else:
        found = (
            await session.execute(
                select(AgentTool.agent_id).where(
                    AgentTool.agent_id == agent_id,
                    AgentTool.tool_id == row_id,
                )
            )
        ).first()
    return found is not None


async def _materialize_agent_grants(
    session: AsyncSession,
    *,
    project: Project,
    installation: MarketplaceInstallation,
    listing: MarketplaceListing,
    role_map: dict[str, list[str]],
) -> tuple[dict[str, Any], list[str]]:
    """Filas `agent_tools` / `agent_skills` para los agentes del rol destino.

    Reutiliza la fila `Tool`/`Skill` que la materialización del ADR 0100 creó al
    instalar (`source_installation_id`); no crea catálogo nuevo. Si esa fila no
    existe (tipos diferidos por falta de sandbox, ADR 0081), se avisa y no se
    escribe nada — un despliegue que dice haber entregado algo que no existe es
    peor que uno que dice que no pudo.
    """
    refs: dict[str, Any] = {}
    warnings: list[str] = []

    is_skill = listing.kind == MarketplaceListingKind.SKILL.value
    materialized = await _materialized_catalog_row(
        session, installation_id=installation.id, is_skill=is_skill
    )
    if materialized is None:
        warnings.append(
            "la instalación no tiene fila de catálogo materializada (tipo diferido"
            " por el sandbox del ADR 0081, o instalación anterior al ADR 0100):"
            " no hay nada que asignar a ningún agente"
        )
        return refs, warnings

    row_id, row_name = materialized
    roles = roles_for(role_map, row_name)
    if not roles:
        warnings.append(
            "sin roles en el role_map: no se asigna a ningún agente (el despliegue"
            " queda registrado y se puede re-desplegar con roles)"
        )
        return refs, warnings

    agents = [a for a in await _project_agents(session, project) if a.role in roles]
    if not agents:
        warnings.append(
            f"ningún agente del proyecto tiene los roles {sorted(roles)}:"
            " nada asignado (revisa el equipo del proyecto o el role_map)"
        )
        return refs, warnings

    created: list[dict[str, str]] = []
    for agent in agents:
        if await _already_granted(session, agent_id=agent.id, row_id=row_id, is_skill=is_skill):
            # Ya asignada (a mano por el operador, o por otro despliegue). NO se
            # anota: la retirada no puede llevarse lo que este despliegue no creó.
            kind_label = "skill" if is_skill else "tool"
            warnings.append(
                f"el agente {agent.name!r} ya tenía la {kind_label} asignada: se"
                " respeta y la retirada no la quitará"
            )
            continue
        if is_skill:
            session.add(AgentSkill(agent_id=agent.id, skill_id=row_id))
            created.append({"agent_id": str(agent.id), "skill_id": str(row_id)})
        else:
            session.add(AgentTool(agent_id=agent.id, tool_id=row_id))
            created.append({"agent_id": str(agent.id), "tool_id": str(row_id)})

    if created:
        refs["agent_skills" if is_skill else "agent_tools"] = created
        await session.flush()
    return refs, warnings


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
async def _load_deploy_targets(
    session: AsyncSession,
    *,
    installation_id: UUID,
    project_id: UUID,
) -> tuple[MarketplaceInstallation, MarketplaceListing, Project]:
    """Carga (instalación, listing, proyecto) o levanta el error que toca.

    Todo bajo la sesión de tenant, así que la RLS decide qué es visible: un id
    de otro tenant llega aquí como «no existe», que es la respuesta correcta.
    """
    installation = (
        await session.execute(
            select(MarketplaceInstallation).where(
                MarketplaceInstallation.id == installation_id,
                MarketplaceInstallation.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if installation is None:
        raise DeployNotFoundError("installation not found")

    listing = (
        await session.execute(
            select(MarketplaceListing).where(MarketplaceListing.id == installation.listing_id)
        )
    ).scalar_one_or_none()
    if listing is None:  # pragma: no cover - FK CASCADE lo hace imposible
        raise DeployNotFoundError("listing not found")

    if installation.status != InstallationStatus.ENABLED.value:
        raise DeployConflictError(
            f"la instalación está {installation.status!r}: solo se despliega lo"
            " habilitado (consiente los permisos pendientes primero)"
        )

    project = (
        await session.execute(
            select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if project is None:
        raise DeployNotFoundError("project not found")
    if project.tenant_id != installation.tenant_id:  # pragma: no cover - la RLS ya lo impide
        raise DeployNotFoundError("project not found")
    return installation, listing, project


async def deploy_installation(
    session: AsyncSession,
    *,
    installation_id: UUID,
    project_id: UUID,
    config: dict[str, Any] | None = None,
    role_map: Any = None,
    actor: str,
    actor_user_id: UUID | None = None,
) -> DeploymentResult:
    """Despliega una instalación en un proyecto y devuelve qué se materializó.

    El caller es dueño de la transacción: si esto levanta, **nada** debe
    persistir (una config inválida no puede dejar media entrada MCP escrita).

    Args:
        installation_id: la instalación del tenant de la sesión.
        project_id: el proyecto destino, del mismo tenant (la RLS lo garantiza).
        config: los valores del formulario del `config_schema`.
        role_map: ver :func:`normalize_role_map`.
        actor: cadena de auditoría (``"user:<uuid>"``).
        actor_user_id: el usuario, para `deployed_by`.

    Raises:
        DeployNotFoundError: instalación o proyecto invisibles para esta sesión.
        DeployConflictError: la instalación no está habilitada.
        DeployError: la config no valida contra el `config_schema`.
    """
    installation, listing, project = await _load_deploy_targets(
        session, installation_id=installation_id, project_id=project_id
    )

    # --- versión + pin ----------------------------------------------------
    version_row = await ensure_listing_version(
        session, listing=listing, version=installation.version
    )
    if version_row is not None and installation.pinned_version_id is None:
        installation.pinned_version_id = version_row.id

    # Sin fila de versión (listing global publicado después de la 0128), el
    # despliegue lee el manifest VIVO del listing. Es lo que ya hacía todo el
    # marketplace antes del ADR 0142; el snapshot llega con la fase 3.
    manifest: dict[str, Any] = dict(
        (version_row.manifest if version_row is not None else None) or listing.manifest or {}
    )
    schema = version_row.config_schema if version_row is not None else None
    if schema is None:
        raw = manifest.get("config_schema")
        schema = dict(raw) if isinstance(raw, dict) else None

    # --- validación ANTES de escribir nada --------------------------------
    values = apply_defaults(schema, config)
    errors = validate_deployment_config(schema, values)
    if errors:
        raise DeployError(
            "la configuración del despliegue no valida contra el `config_schema`",
            errors=errors,
        )

    normalized_roles = normalize_role_map(
        role_map, targets=[str(t) for t in (manifest.get("targets") or []) if isinstance(t, str)]
    )

    # --- idempotencia -----------------------------------------------------
    existing = (
        await session.execute(
            select(MarketplaceDeployment).where(
                MarketplaceDeployment.installation_id == installation.id,
                MarketplaceDeployment.project_id == project.id,
                MarketplaceDeployment.status == DeploymentStatus.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return DeploymentResult(
            deployment_id=existing.id,
            kind=listing.kind,
            deployed_version=existing.deployed_version,
            already_deployed=True,
            created_refs=dict(existing.created_refs or {}),
            warnings=(
                "ya había un despliegue activo de esta instalación en este proyecto:"
                " no se ha cambiado nada (retíralo si quieres re-desplegar con otra"
                " configuración)",
            ),
        )

    # --- materialización por tipo ----------------------------------------
    oauth_pending = False
    if listing.kind == MarketplaceListingKind.MCP_SERVER.value:
        refs, warnings, oauth_pending = await _materialize_mcp_server(
            session,
            project=project,
            listing=listing,
            manifest=manifest,
            values=values,
            role_map=normalized_roles,
        )
    else:
        refs, warnings = await _materialize_agent_grants(
            session,
            project=project,
            installation=installation,
            listing=listing,
            role_map=normalized_roles,
        )

    deployment = MarketplaceDeployment(
        tenant_id=installation.tenant_id,
        installation_id=installation.id,
        project_id=project.id,
        config=values,
        role_map=normalized_roles,
        deployed_version=installation.version,
        status=DeploymentStatus.ACTIVE.value,
        created_refs=refs,
        deployed_by=actor_user_id,
    )
    session.add(deployment)
    await session.flush()

    session.add(
        MarketplaceAuditEntry(
            tenant_id=installation.tenant_id,
            actor=actor,
            action=MarketplaceAuditAction.DEPLOY.value,
            listing_id=listing.id,
            installation_id=installation.id,
            detail={
                "deployment_id": str(deployment.id),
                "project_id": str(project.id),
                "kind": listing.kind,
                "deployed_version": installation.version,
                "role_map": normalized_roles,
                # Solo las CLAVES de la config: un valor que el `config_schema`
                # etiquetó mal como no-secreto no acaba en la auditoría.
                "config_keys": sorted(values),
                "created_refs": refs,
                "warnings": list(warnings),
                "oauth_pending": oauth_pending,
            },
        )
    )
    await session.flush()

    logger.info(
        "marketplace_deployment_created",
        deployment_id=str(deployment.id),
        installation_id=str(installation.id),
        project_id=str(project.id),
        kind=listing.kind,
        created_refs=refs,
    )
    return DeploymentResult(
        deployment_id=deployment.id,
        kind=listing.kind,
        deployed_version=installation.version,
        already_deployed=False,
        created_refs=refs,
        warnings=tuple(warnings),
        oauth_pending=oauth_pending,
    )


async def retire_deployment(
    session: AsyncSession,
    *,
    deployment_id: UUID,
    actor: str,
    actor_user_id: UUID | None = None,
) -> int:
    """Deshace EXACTAMENTE lo que `created_refs` dice, y marca `retired`.

    Devuelve cuántas referencias se retiraron. La fila del despliegue se
    conserva (auditoría). Lo que el operador hubiera añadido a mano no se toca,
    porque no está en `created_refs` — ésa es toda la razón de que exista esa
    columna.
    """
    deployment = (
        await session.execute(
            select(MarketplaceDeployment).where(MarketplaceDeployment.id == deployment_id)
        )
    ).scalar_one_or_none()
    if deployment is None:
        raise DeployNotFoundError("deployment not found")
    if deployment.status == DeploymentStatus.RETIRED.value:
        raise DeployConflictError("el despliegue ya estaba retirado")

    refs: dict[str, Any] = dict(deployment.created_refs or {})
    removed = 0

    project = (
        await session.execute(select(Project).where(Project.id == deployment.project_id))
    ).scalar_one_or_none()

    if project is not None:
        server_names = [str(n) for n in (refs.get("mcp_servers") or [])]
        if server_names:
            keep = [
                dict(s)
                for s in (project.mcp_servers or [])
                if isinstance(s, dict) and str(s.get("name")) not in server_names
            ]
            removed += len(project.mcp_servers or []) - len(keep)
            project.mcp_servers = keep

        policy_keys = [str(k) for k in (refs.get("mcp_tool_roles") or [])]
        if policy_keys:
            policy = {
                str(k): v for k, v in (project.mcp_tool_roles or {}).items() if k not in policy_keys
            }
            removed += len(project.mcp_tool_roles or {}) - len(policy)
            project.mcp_tool_roles = policy

    for entry in refs.get("agent_tools") or []:
        if not isinstance(entry, dict):
            continue
        result = await session.execute(
            select(AgentTool).where(
                AgentTool.agent_id == UUID(str(entry["agent_id"])),
                AgentTool.tool_id == UUID(str(entry["tool_id"])),
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            await session.delete(row)
            removed += 1

    for entry in refs.get("agent_skills") or []:
        if not isinstance(entry, dict):
            continue
        result = await session.execute(
            select(AgentSkill).where(
                AgentSkill.agent_id == UUID(str(entry["agent_id"])),
                AgentSkill.skill_id == UUID(str(entry["skill_id"])),
            )
        )
        row_skill = result.scalar_one_or_none()
        if row_skill is not None:
            await session.delete(row_skill)
            removed += 1

    deployment.status = DeploymentStatus.RETIRED.value
    deployment.retired_at = datetime.now(UTC)
    deployment.retired_by = actor_user_id
    await session.flush()

    session.add(
        MarketplaceAuditEntry(
            tenant_id=deployment.tenant_id,
            actor=actor,
            action=MarketplaceAuditAction.RETIRE.value,
            installation_id=deployment.installation_id,
            detail={
                "deployment_id": str(deployment.id),
                "project_id": str(deployment.project_id),
                # Se conserva lo que había: la fila `retired` sigue contando qué
                # creó, aunque ya no exista.
                "retired_refs": refs,
                "removed": removed,
            },
        )
    )
    await session.flush()

    logger.info(
        "marketplace_deployment_retired",
        deployment_id=str(deployment.id),
        removed=removed,
    )
    return removed


__all__ = [
    "MCP_ENTRY_OVERLAY_KEYS",
    "ROLE_MAP_WILDCARD",
    "DeployConflictError",
    "DeployError",
    "DeployNotFoundError",
    "DeploymentResult",
    "deploy_installation",
    "ensure_listing_version",
    "normalize_role_map",
    "retire_deployment",
    "roles_for",
]
