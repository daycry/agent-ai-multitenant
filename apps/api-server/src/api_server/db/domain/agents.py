"""Agregado de capacidad: quien trabaja y con que.

`Agent` es la plantilla reutilizable (IA o humana). Sus dos vocabularios
declarativos son `Skill` --fragmento de prompt, sin codigo ejecutable-- y `Tool`
--funcion ejecutable con esquemas y nivel de seguridad--, y `AgentSkill` /
`AgentTool` son las junctions M:N que se los atan a un agente con proficiencia y
configuracion propias.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from api_server.db.domain.enums import (
    SkillCategory,
    ToolCategory,
)


# =============================================================================
# Agent
# =============================================================================
class Agent(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "agents"
    __table_args__ = (
        Index(
            "ix_agents_tenant_role",
            "tenant_id",
            "role",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_agents_scope_project", "scope", "project_id"),
        Index("ix_agents_forked_from", "forked_from_agent_id"),
        # Unicidad del nombre por tenant, PARTIDA en dos por `project_id`
        # (migración 0126). NO es un único `(tenant_id, name)`: un agente
        # `project_local` forkeado de su plantilla `global_tenant_template`
        # conserva el nombre por diseño, así que ese índice habría roto el fork.
        Index(
            "uq_agents_tenant_project_name_live",
            "tenant_id",
            "project_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND project_id IS NOT NULL"),
        ),
        Index(
            "uq_agents_tenant_name_global_live",
            "tenant_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND project_id IS NULL"),
        ),
        # scope <-> project_id invariant (spec §5.7.5):
        #   global_builtin / global_tenant_template -> project_id IS NULL
        #   project_local                           -> project_id IS NOT NULL
        CheckConstraint(
            "(scope = 'project_local' AND project_id IS NOT NULL)"
            " OR (scope IN ('global_builtin', 'global_tenant_template')"
            "     AND project_id IS NULL)",
            name="ck_agents_scope_project_consistency",
        ),
        # agent_type enum value set (Plan 16 task_16_01). The column itself
        # ships with the domain-minimum migration (0002) as String(16) NOT
        # NULL DEFAULT 'ai'; migration 0066 adds this CHECK so the DB enforces
        # the AgentType value set (ai|human) instead of accepting any text.
        CheckConstraint(
            "agent_type IN ('ai', 'human')",
            name="ck_agents_agent_type",
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # AI vs human agent (Plan 16). Values are the :class:`AgentType` StrEnum
    # (ai|human) stored as TEXT — same string-backed-enum convention as
    # `scope`/`AgentScope`. Existing rows default to 'ai' (no behaviour change
    # for AI agents). DB-enforced by ck_agents_agent_type (migration 0066).
    agent_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ai'"))
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    memory_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'private'")
    )
    review_capability: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    max_concurrent_tasks: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    is_template: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    # --- Linked-vs-forked (spec §5.7.5) ---
    scope: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'project_local'")
    )
    # NULL except when scope=project_local. FK is deferred to migration
    # (Project ORM is defined later in this file but ForeignKey resolves
    # by table name at metadata-finalize time).
    project_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Self-FK: if this agent is a fork, points to the origin. ON DELETE
    # SET NULL keeps the local copy alive even if the global vanishes.
    forked_from_agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Semver of the global at fork time. Used to compute diffs and to
    # decide whether "merge upstream improvements" is needed.
    forked_from_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Semver the linked is pinned to. NULL = follow the global's 'stable'.
    anchored_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Agent(id={self.id!r}, name={self.name!r}, role={self.role!r})"


# =============================================================================
# Skill
# =============================================================================
class Skill(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "skills"
    __table_args__ = (
        Index(
            "ix_skills_tenant_category",
            "tenant_id",
            "category",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Único por tenant sobre los vivos (migración 0126, patrón 0077).
        Index(
            "uq_skills_tenant_name_live",
            "tenant_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_skills_is_builtin",
            "is_builtin",
            postgresql_where=text("is_builtin = true"),
        ),
        # Cerramos `category` al conjunto del seed (ADR 0050, migración 0078).
        # El value set se deriva de `SkillCategory`, la única declaración.
        CheckConstraint(
            "category IN (" + ", ".join(f"'{c.value}'" for c in SkillCategory) + ")",
            name="ck_skills_category",
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_fragment: Mapped[str] = mapped_column(Text, nullable=False)
    # ADR 0100 (pieza 1): provenance del marketplace — espejo de forked_from_*.
    # NULL = fila nativa; poblado = materializada desde una instalación (la
    # des-materialización de uninstall/revoke busca por source_installation_id).
    source_listing_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_listings.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_installation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_installations.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_version: Mapped[str | None] = mapped_column(Text, nullable=True)

    # List of tool UUIDs. JSONB rather than a junction table -- the
    # association is a *recommendation*, not a hard FK, and tools may
    # come from outside this tenant's catalog (built-ins).
    required_tools: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Catalog marker. true => visible to all tenants via SELECT RLS
    # policy (migration 0005). Writes still go through tenant-isolation
    # (only the platform tenant / BYPASSRLS can create built-ins).
    is_builtin: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))


# =============================================================================
# Tool
# =============================================================================
class Tool(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tools"
    __table_args__ = (
        Index(
            "ix_tools_tenant_category",
            "tenant_id",
            "category",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_tools_is_builtin",
            "is_builtin",
            postgresql_where=text("is_builtin = true"),
        ),
        # No two LIVE tools of the same tenant may share a name (a soft-deleted
        # name may be reused). Partial unique index because PostgreSQL UNIQUE
        # constraints cannot carry a WHERE clause (task_06_18_04, ADR 0049).
        Index(
            "uq_tools_tenant_name",
            "tenant_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("timeout_seconds > 0", name="ck_tools_timeout_positive"),
        # Closed taxonomy value sets (ADR 0049). The category list mirrors
        # ToolCategory; security_level / implementation_type mirror their enums.
        CheckConstraint(
            "category IN (" + ", ".join(f"'{c.value}'" for c in ToolCategory) + ")",
            name="ck_tools_category",
        ),
        CheckConstraint(
            "security_level IN ('safe', 'sandboxed', 'privileged')",
            name="ck_tools_security_level",
        ),
        CheckConstraint(
            "implementation_type IN ("
            "'builtin', 'python_function', 'http_endpoint', 'mcp_tool', 'docker_command')",
            name="ck_tools_implementation_type",
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)

    input_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    output_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    implementation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    implementation_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    security_level: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'safe'")
    )

    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("60"))
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Catalog marker -- see Skill.is_builtin.
    # ADR 0100 (pieza 1): provenance del marketplace — espejo de forked_from_*.
    # NULL = fila nativa; poblado = materializada desde una instalación (la
    # des-materialización de uninstall/revoke busca por source_installation_id).
    source_listing_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_listings.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_installation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_installations.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_version: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_builtin: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))


# =============================================================================
# Agent-Skill (M:N junction)
# =============================================================================
class AgentSkill(Base, TenantScopedMixin, TimestampMixin):
    """Composite PK (agent_id, skill_id) + `tenant_id` denormalizado.

    Hasta la migración 0124 esta tabla NO tenía `tenant_id` — «RLS vía la
    visibilidad del padre», decía la 0002. Eso valía para el borrado en cascada
    y no para nada más: sin columna no hay policy, y a nivel de BD cualquier
    sesión leía las asignaciones de otro tenant (las FK se comprueban como
    propietario e IGNORAN la RLS, así que tampoco protegían la escritura).

    **No hay que pasar `tenant_id` al construir la fila**: el trigger
    `trg_agent_skills_set_tenant_id` lo DERIVA del agente propietario y rechaza
    cualquier valor que lo contradiga — también para los roles BYPASSRLS, que
    son los que ninguna policy vigila.
    """

    __tablename__ = "agent_skills"
    __table_args__ = (PrimaryKeyConstraint("agent_id", "skill_id", name="pk_agent_skills"),)

    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    proficiency: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'standard'")
    )


# =============================================================================
# Agent-Tool (M:N junction)
# =============================================================================
class AgentTool(Base, TenantScopedMixin, TimestampMixin):
    """Junction agente↔tool + `tenant_id` denormalizado (migración 0124).

    `config_override` es el dato con valor real de esta tabla, y era legible
    cross-tenant antes de la 0124. Igual que en :class:`AgentSkill`, el
    `tenant_id` lo estampa el trigger desde el agente propietario.
    """

    __tablename__ = "agent_tools"
    __table_args__ = (PrimaryKeyConstraint("agent_id", "tool_id", name="pk_agent_tools"),)

    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tools.id", ondelete="CASCADE"),
        nullable=False,
    )
    config_override: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


# =============================================================================
# AgentPromptVersion — el historial del prompt (`task_gov_02`, migración 0143)
# =============================================================================
class AgentPromptVersion(Base, UUIDPrimaryKeyMixin, TenantScopedMixin):
    """Una versión del prompt de un agente. **Append-only.**

    Hasta la migración 0143, `PUT /agents/{id}` sobrescribía `system_prompt` y
    `model_config.system_prompts` sin dejar rastro: si la calidad de un agente
    caía, no había forma de saber qué cambió ni de volver.

    **No lleva `TimestampMixin` a propósito**, y es la diferencia más visible con
    el resto de las tablas de este módulo: `updated_at` no tiene sentido en una
    fila de historial, y su ausencia es la señal en el esquema de que el
    invariante append-only del repositorio
    (:mod:`api_server.db.agent_prompt_version_repo` — sólo `append` y `list`) no
    es una convención opcional.

    Qué guarda cada columna, que no es intercambiable:

    * ``system_prompt`` y ``persona`` son los valores **crudos** (el campo plano y
      ``model_config.system_prompts``). Son los que un humano lee en el diff: si
      sólo se guardase el texto efectivo, editar el idioma NO preferido no
      aparecería en el historial aunque haya generado una versión.
    * ``prompt_hash`` es el sello del texto **efectivo** —lo que
      :func:`api_server.agent_persona.resolve_agent_persona` resuelve y lo único
      que ve el modelo—, y es lo que ``task_gov_03`` mezcla en
      ``executions.prompt_version``. Se persiste en vez de recalcularse al leer
      porque recalcularlo lo ataría al resolutor de HOY: el día que cambie la
      precedencia bilingüe o el cap, los sellos de los runs viejos se moverían y
      dejarían de identificar lo que de verdad corrió.
    """

    __tablename__ = "agent_prompt_versions"
    __table_args__ = (
        # Dos cosas con un índice: impide dos filas con el mismo número —dos
        # `PUT` simultáneos aterrizan como 409 vía `flush_or_conflict`, no como
        # historial duplicado— y resuelve «la última versión de este agente»
        # (`ORDER BY version DESC LIMIT 1`) como recorrido hacia atrás, sin Sort.
        UniqueConstraint("agent_id", "version", name="uq_agent_prompt_versions_agent_version"),
        CheckConstraint("version >= 1", name="ck_agent_prompt_versions_version_positive"),
    )

    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    persona: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # SET NULL en la FK: el historial sobrevive al usuario que lo escribió. NULL
    # también es el autor HONESTO de la fila de base — la que registra el prompt
    # que ya existía antes de que hubiera historial y cuyo autor nadie apuntó.
    changed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # La cadena. SET NULL y no CASCADE: borrar un eslabón no debe llevarse el
    # resto del historial por delante.
    parent_version_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_prompt_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"AgentPromptVersion(agent_id={self.agent_id!r}, version={self.version!r})"
