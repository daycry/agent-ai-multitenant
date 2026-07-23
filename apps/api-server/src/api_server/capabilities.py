"""Hub de Capacidad — set efectivo REAL por entidad (Plan 06.17 task_06_17_08).

La "estrella polar" del Plan 06.17: capacitar a un agente/proyecto/equipo es
dotarlo de CAPACIDAD por cuatro vías —**SABER** (KBs+RAG), **RECORDAR** (memoria
por scope), **SER** (persona/modelo) y **HACER** (tools/comandos)—. Este módulo
es la pieza compartida que ``GET /{entity}/{id}/capabilities`` (en los routers
de agents/projects/teams) proyecta sobre un único contrato JSON, sin que cada
router reimplemente la lógica.

Frontera con 06.18 (NO duplicar): la sección **HACER** **delega/compone** con
la pieza pura :func:`api_server.agent_tools_enforcement.compute_effective_tools`
y el mismo cálculo que sirve ``GET /agents/{id}/effective-tools``. Este módulo
NO recalcula la intersección agente∩modo; reutiliza el punto único.

Honestidad de estado (Plan 06.17, regla 4): el contrato nunca finge capacidad.
Un agente global avisa que **no ve conocimiento de proyecto** (ADR 0054); un
agente sin ``model_config`` avisa "modelo no configurado" (ADR 0055); un
``memory_scope=private`` avisa que el agente IA no memoriza en silencio.

Multi-tenancy: todas las queries van bajo la sesión tenant-scoped del router
(RLS). El router resuelve el 404 cuando la entidad no es visible ANTES de
construir el resumen, de modo que cross-tenant nunca llega aquí.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from shared_domain.tool_names import to_canonical_set
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.agent_tools_enforcement import (
    ToolWarning,
    compute_effective_tools,
    resolve_project_mcp_tool_names,
)
from api_server.db.domain import Agent, AgentTool, Project, Tool
from api_server.db.knowledge import AgentKnowledgeBase, KnowledgeBase, KnowledgeBaseProject
from api_server.db.memory import MemoryEntry
from api_server.schemas.catalog import tool_is_runtime_wired

# Etiquetas de NIVEL de conocimiento (estrella polar, tabla de niveles). Una KB
# visible se etiqueta por la vía por la que se concedió: al rol del agente
# (``agent_knowledge_bases``), al stack del proyecto (``kb_projects``) o por ser
# built-in del catálogo de plataforma.
LEVEL_ROLE = "rol"
LEVEL_STACK = "stack"
LEVEL_PLATFORM = "plataforma"

# Códigos estables de los avisos honestos del Hub. Son el identificador
# idioma-neutral que el frontend usa para emparejar/destacar cada aviso (p. ej.
# el de agente global, ADR 0054) sin inspeccionar el texto castellano —la rama
# EN dejaba de funcionar al hacerlo (follow-up bilingual-warnings). Los avisos de
# la sección HACER reusan los códigos de :mod:`agent_tools_enforcement`.
WARN_MODEL_NOT_CONFIGURED = "model_not_configured"
WARN_PRIVATE_MEMORY = "private_memory_scope"
WARN_GLOBAL_AGENT = "global_agent_no_project_context"
WARN_TEAM_NO_MEMBERS = "team_no_members"


# ---------------------------------------------------------------------------
# Schemas del contrato (compartidos por los tres routers)
# ---------------------------------------------------------------------------
class CapabilityWarning(BaseModel):
    """Un aviso honesto del Hub en forma BILINGÜE estructurada (06.17 + follow-up).

    Cada aviso lleva un ``code`` estable (idioma-neutral) y el mismo mensaje en
    ``es`` y ``en`` (los dos únicos idiomas de esta versión). Antes los warnings
    eran ``list[str]`` SOLO en castellano y el frontend los pintaba verbatim,
    dejando muerta la rama EN; ahora el frontend elige el idioma activo por el
    contrato y empareja el aviso de agente global (ADR 0054) por su ``code``.
    """

    model_config = ConfigDict(populate_by_name=True)

    code: str
    es: str
    en: str


class CapabilityKB(BaseModel):
    """Una KB visible, etiquetada por su nivel (rol/stack/plataforma)."""

    model_config = ConfigDict(populate_by_name=True)

    kb_id: UUID
    name: str
    #: ``rol`` (granteada al agente), ``stack`` (al proyecto) o ``plataforma``
    #: (built-in del catálogo global). Una KB built-in granteada al proyecto se
    #: etiqueta ``plataforma`` (gana el origen del catálogo sobre la vía).
    level: str
    is_builtin: bool = False


class CapabilitySaber(BaseModel):
    """Sección SABER: el conocimiento curado que la entidad consulta."""

    model_config = ConfigDict(populate_by_name=True)

    knowledge_bases: list[CapabilityKB] = Field(default_factory=list)


class CapabilityMemoryScope(BaseModel):
    """Conteo de memorias en un scope concreto."""

    model_config = ConfigDict(populate_by_name=True)

    scope: str
    count: int


class CapabilityRecordar(BaseModel):
    """Sección RECORDAR: la memoria por scope (y, en agente, su memory_scope)."""

    model_config = ConfigDict(populate_by_name=True)

    #: El ``memory_scope`` configurado en el agente (None para proyecto/equipo).
    memory_scope: str | None = None
    memory: list[CapabilityMemoryScope] = Field(default_factory=list)


class CapabilitySer(BaseModel):
    """Sección SER: persona/modelo. Solo poblada para un agente."""

    model_config = ConfigDict(populate_by_name=True)

    #: ``True`` cuando el ``model_config`` trae ``provider`` y ``model`` no vacíos.
    model_configured: bool = False
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    system_prompt_present: bool = False
    #: Nivel que fija el modelo EFECTIVO en la cadena de herencia (Ola D / ADR
    #: 0065): ``"agent" | "team" | "project" | "platform"``. ``None`` si no se
    #: resolvió (p. ej. el agente sí pinea su modelo). Solo informativo.
    model_origin: str | None = None


class CapabilityHacer(BaseModel):
    """Sección HACER: el set efectivo de tools (compuesto con 06.18).

    Mismo shape que el contrato ``effective-tools`` (06.18) en lo esencial:
    ``effective`` (canónicas que el runtime ejecuta), ``unrestricted`` (sin
    restricción per-agente), ``shell_exec_effective``. Para EQUIPO es la UNIÓN
    agregada de las tools efectivas de los miembros (ADR 0053).
    """

    model_config = ConfigDict(populate_by_name=True)

    effective: list[str] = Field(default_factory=list)
    unrestricted: bool = False
    shell_exec_effective: bool = False


class CapabilitiesResponse(BaseModel):
    """El contrato del Hub de Capacidad por entidad (06.17 task_06_17_08)."""

    model_config = ConfigDict(populate_by_name=True)

    entity_type: str
    entity_id: UUID
    saber: CapabilitySaber
    recordar: CapabilityRecordar
    #: Solo poblada para un agente; ``None`` para proyecto/equipo.
    ser: CapabilitySer | None = None
    hacer: CapabilityHacer
    #: Avisos honestos BILINGÜES (agente global sin contexto de proyecto, modelo
    #: no configurado, memory_scope=private silencioso, equipo sin miembros…).
    #: Cada uno = ``{code, es, en}``; el frontend renderiza el idioma activo.
    warnings: list[CapabilityWarning] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Builders puros / queries compartidas
# ---------------------------------------------------------------------------
def build_ser(agent: Agent) -> tuple[CapabilitySer, list[CapabilityWarning]]:
    """Sección SER de un agente + avisos (modelo no configurado).

    ``model_configured`` es ``True`` solo cuando el ``model_config`` trae
    ``provider`` y ``model`` no vacíos (ADR 0055: un ``{}`` legacy o un spec sin
    proveedor/modelo no cuenta como configurado y dispararía el default en
    dispatch). Devuelve también el aviso "modelo no configurado" (bilingüe)
    cuando procede.
    """
    cfg = agent.model_config or {}
    provider = cfg.get("provider")
    model = cfg.get("model")
    temperature = cfg.get("temperature")
    provider_str = str(provider).strip() if isinstance(provider, str) else None
    model_str = str(model).strip() if isinstance(model, str) else None
    configured = bool(provider_str) and bool(model_str)

    warnings: list[CapabilityWarning] = []
    if not configured:
        warnings.append(
            CapabilityWarning(
                code=WARN_MODEL_NOT_CONFIGURED,
                es=(
                    "modelo no configurado: el agente usará el default "
                    "operator-configurable en dispatch (define provider y model "
                    "en model_config)"
                ),
                en=(
                    "model not configured: the agent will use the "
                    "operator-configurable default at dispatch (set provider and "
                    "model in model_config)"
                ),
            )
        )

    temp_val: float | None
    try:
        temp_val = float(temperature) if temperature is not None else None
    except (TypeError, ValueError):
        temp_val = None

    return (
        CapabilitySer(
            model_configured=configured,
            provider=provider_str or None,
            model=model_str or None,
            temperature=temp_val,
            system_prompt_present=bool((agent.system_prompt or "").strip()),
        ),
        warnings,
    )


async def kbs_for_project(session: AsyncSession, *, project_id: UUID) -> list[CapabilityKB]:
    """KBs visibles para un proyecto vía ``kb_projects`` (nivel stack/plataforma).

    Una KB built-in (catálogo global) granteada al proyecto se etiqueta
    ``plataforma``; el resto, ``stack``. Tenant-scoped por RLS; se filtran las
    soft-deleted.
    """
    rows = await session.execute(
        select(KnowledgeBase.id, KnowledgeBase.name, KnowledgeBase.is_builtin)
        .join(KnowledgeBaseProject, KnowledgeBaseProject.kb_id == KnowledgeBase.id)
        .where(
            KnowledgeBaseProject.project_id == project_id,
            KnowledgeBase.deleted_at.is_(None),
        )
        .order_by(KnowledgeBase.name, KnowledgeBase.id)
    )
    return [
        CapabilityKB(
            kb_id=kid,
            name=name,
            level=LEVEL_PLATFORM if is_builtin else LEVEL_STACK,
            is_builtin=bool(is_builtin),
        )
        for kid, name, is_builtin in rows.all()
    ]


async def kbs_for_agent_role(session: AsyncSession, *, agent_id: UUID) -> list[CapabilityKB]:
    """KBs granteadas al ROL del agente vía ``agent_knowledge_bases`` (nivel rol).

    Una KB built-in granteada al rol se sigue etiquetando ``plataforma`` (el
    origen del catálogo gana sobre la vía). Tenant-scoped por RLS.
    """
    rows = await session.execute(
        select(KnowledgeBase.id, KnowledgeBase.name, KnowledgeBase.is_builtin)
        .join(AgentKnowledgeBase, AgentKnowledgeBase.kb_id == KnowledgeBase.id)
        .where(
            AgentKnowledgeBase.agent_id == agent_id,
            KnowledgeBase.deleted_at.is_(None),
        )
        .order_by(KnowledgeBase.name, KnowledgeBase.id)
    )
    return [
        CapabilityKB(
            kb_id=kid,
            name=name,
            level=LEVEL_PLATFORM if is_builtin else LEVEL_ROLE,
            is_builtin=bool(is_builtin),
        )
        for kid, name, is_builtin in rows.all()
    ]


def merge_kbs(*lists: list[CapabilityKB]) -> list[CapabilityKB]:
    """Une KBs de varias vías deduplicando por ``kb_id``.

    Si una KB aparece por más de una vía conserva la PRIMERA etiqueta de nivel
    (el orden de las listas decide la prioridad: rol antes que stack en un
    agente). Ordena por nombre para una salida determinista.
    """
    by_id: dict[UUID, CapabilityKB] = {}
    for kbs in lists:
        for kb in kbs:
            if kb.kb_id not in by_id:
                by_id[kb.kb_id] = kb
    return sorted(by_id.values(), key=lambda k: (k.name, str(k.kb_id)))


async def memory_counts(
    session: AsyncSession,
    *,
    project_id: UUID | None = None,
    team_id: UUID | None = None,
) -> list[CapabilityMemoryScope]:
    """Conteo de memorias por scope para un proyecto/equipo (RECORDAR).

    Cuenta ``memory_entries`` (no soft-deleted) agrupadas por ``scope``,
    acotadas al ``project_id`` (scope ``project_shared``) y/o ``team_id`` (scope
    ``team_shared``) y SIEMPRE las ``global`` del tenant. Tenant-scoped por RLS.
    Sin filtro (agente puro sin proyecto) devuelve solo las ``global``.
    """
    conditions = [MemoryEntry.scope == "global"]
    if project_id is not None:
        conditions.append(MemoryEntry.project_id == project_id)
    if team_id is not None:
        conditions.append(MemoryEntry.team_id == team_id)

    rows = await session.execute(
        select(MemoryEntry.scope, func.count())
        .where(MemoryEntry.deleted_at.is_(None), or_(*conditions))
        .group_by(MemoryEntry.scope)
        .order_by(MemoryEntry.scope)
    )
    return [CapabilityMemoryScope(scope=scope, count=count) for scope, count in rows.all()]


async def hacer_for_agent(
    session: AsyncSession, *, agent: Agent
) -> tuple[CapabilityHacer, list[CapabilityWarning]]:
    """Sección HACER de un agente: delega en ``compute_effective_tools`` (06.18).

    Reutiliza EXACTAMENTE el mismo cálculo que ``GET /agents/{id}/effective-tools``
    (el punto único de intersección agente∩modo + cruce de ``allowed_commands``),
    sin modo (el Hub muestra el set base del dispatch). Devuelve el shape recortado
    para el Hub + los ``warnings`` honestos BILINGÜES del cálculo (tool no
    ejecutable, shell_exec sin commands), tomados de ``result.warnings_i18n``.
    """
    rows = await session.execute(
        select(Tool)
        .join(AgentTool, AgentTool.tool_id == Tool.id)
        .where(AgentTool.agent_id == agent.id, Tool.deleted_at.is_(None))
        .order_by(Tool.name, Tool.id)
    )
    assigned_tools = list(rows.scalars().all())
    assigned_names: list[str] | None = [t.name for t in assigned_tools] if assigned_tools else None
    shell_exec_assigned = any(t.name == "shell_exec" for t in assigned_tools)

    wired_canonical_names: set[str] = set()
    for tool in assigned_tools:
        if tool_is_runtime_wired(tool.name, tool.implementation_type):
            wired_canonical_names |= to_canonical_set([tool.name])

    # ADR 0128: load the full project once — it feeds both the shell_exec cross
    # (allowed_commands) and the MCP tools the project contributes to the run
    # allowlist, so the Hub's effective set stays consistent with
    # `GET /agents/{id}/effective-tools`.
    allowed_commands_non_empty = False
    project_mcp_tool_names: frozenset[str] = frozenset()
    if agent.project_id is not None:
        project = (
            await session.execute(
                select(Project).where(Project.id == agent.project_id, Project.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if project is not None:
            allowed_commands_non_empty = bool(project.allowed_commands)
            project_mcp_tool_names = await resolve_project_mcp_tool_names(
                session, project, role=agent.role
            )

    result = compute_effective_tools(
        assigned_names,
        None,
        mode_name=None,
        shell_exec_assigned=shell_exec_assigned,
        allowed_commands_non_empty=allowed_commands_non_empty,
        wired_canonical_names=wired_canonical_names,
        project_mcp_tool_names=project_mcp_tool_names,
    )
    return (
        CapabilityHacer(
            effective=result.effective,
            unrestricted=result.unrestricted,
            shell_exec_effective=result.shell_exec_effective,
        ),
        [_tool_warning_to_capability(w) for w in result.warnings_i18n],
    )


def _tool_warning_to_capability(warning: ToolWarning) -> CapabilityWarning:
    """Adapta un ``ToolWarning`` de 06.18 al ``CapabilityWarning`` del Hub.

    Mismo ``code`` y mismos textos ``es``/``en``: la sección HACER no reescribe
    los avisos del cálculo efectivo, solo cambia de envoltura para unificar el
    contrato bilingüe de ``warnings``.
    """
    return CapabilityWarning(code=warning.code, es=warning.es, en=warning.en)


def agent_global_warning(agent: Agent) -> list[CapabilityWarning]:
    """Aviso honesto BILINGÜE para un agente global (ADR 0054).

    Un agente sin ``project_id`` (``global_builtin`` / ``global_tenant_template``)
    no tiene contexto de proyecto propio para la lectura de KBs/memoria; lo
    avisamos explícitamente (la resolución task-scoped del ADR 0054 ocurre en
    tiempo de run, no en esta vista estática)."""
    if agent.project_id is None:
        return [
            CapabilityWarning(
                code=WARN_GLOBAL_AGENT,
                es=(
                    "Agente global: es una plantilla read-only sin contexto de "
                    "proyecto propio, por eso esta vista no muestra conocimiento ni "
                    "memoria de proyecto. En una tarea de proyecto sí usará el "
                    "contexto de la tarea (ADR 0054). Para personalizarlo "
                    "(conocimiento, tools, skills o modelo), fórkalo a tu proyecto "
                    "desde la pantalla de Agentes, o adopta su equipo desde Equipos."
                ),
                en=(
                    "Global agent: a read-only template with no project context of "
                    "its own, so this view shows no project knowledge or memory. In "
                    "a project task it will use the task context (ADR 0054). To "
                    "customize it (knowledge, tools, skills or model), fork it into "
                    "your project from the Agents screen, or adopt its team from Teams."
                ),
            )
        ]
    return []


def private_memory_warning(memory_scope: str) -> list[CapabilityWarning]:
    """Aviso BILINGÜE de ``memory_scope=private`` silencioso para un agente IA."""
    if memory_scope == "private":
        return [
            CapabilityWarning(
                code=WARN_PRIVATE_MEMORY,
                es=(
                    "memory_scope=private: un agente IA no memoriza nada en este "
                    "scope (elige team_shared/project_shared/global para que "
                    "recuerde entre runs)"
                ),
                en=(
                    "memory_scope=private: an AI agent remembers nothing in this "
                    "scope (choose team_shared/project_shared/global so it "
                    "remembers across runs)"
                ),
            )
        ]
    return []


__all__ = [
    "LEVEL_PLATFORM",
    "LEVEL_ROLE",
    "LEVEL_STACK",
    "WARN_GLOBAL_AGENT",
    "WARN_MODEL_NOT_CONFIGURED",
    "WARN_PRIVATE_MEMORY",
    "WARN_TEAM_NO_MEMBERS",
    "CapabilitiesResponse",
    "CapabilityHacer",
    "CapabilityKB",
    "CapabilityMemoryScope",
    "CapabilityRecordar",
    "CapabilitySaber",
    "CapabilitySer",
    "CapabilityWarning",
    "agent_global_warning",
    "build_ser",
    "hacer_for_agent",
    "kbs_for_agent_role",
    "kbs_for_project",
    "memory_counts",
    "merge_kbs",
    "private_memory_warning",
]
