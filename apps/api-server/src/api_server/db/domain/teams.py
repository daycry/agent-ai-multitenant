"""Agregado de equipo: `Team` y su junction `TeamMember`.

Un equipo agrupa agentes con un workflow por defecto; la junction lleva el rol
dentro del equipo, el flag de lider y la prioridad de asignacion. El `tenant_id`
de `TeamMember` se deriva del EQUIPO, no del agente (migracion 0124).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


# =============================================================================
# Team
# =============================================================================
class Team(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "teams"
    __table_args__ = (
        # Único por tenant sobre los vivos (migración 0126, réplica del patrón
        # `uq_tools_tenant_name` de 0077). Antes de 0126 este mismo par de
        # columnas existía como índice NO único (`ix_teams_tenant_name`): servía
        # para buscar, no para impedir el duplicado.
        Index(
            "uq_teams_tenant_name_live",
            "tenant_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_teams_is_builtin",
            "is_builtin",
            postgresql_where=text("is_builtin = true"),
        ),
        Index("ix_teams_forked_from", "forked_from_team_id"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Soft-FK to a future workflow_templates table (Plan 02+). Kept as a
    # nullable UUID without a constraint until that table exists.
    default_workflow_template_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    # Plan 06.17 task_06_17_15 / ADR 0053: `shared_memory_namespace` se retiró.
    # Era un campo muerto (sin lectura productiva en recall/store): la memoria
    # `team_shared` se resuelve por `project.team_id`, no por un namespace. La
    # migración 0082 dropea la columna (reversible).
    # Catalog marker -- same pattern as Skill/Tool.is_builtin.
    is_builtin: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    # Default de modelo del EQUIPO (Ola A / ADR 0055): un nivel de la cadena de
    # herencia plataforma → proyecto → equipo → agente. JSONB ``{}`` = no fija
    # modelo (los agentes del equipo heredan del proyecto/plataforma). Cuando
    # pinea provider+model, sus agentes sin modelo propio lo usan.
    model_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Modelo del CHAT del proyecto, separado del de ejecución (`model_config`). El
    # chat de planificación es interactivo: puede convenir un modelo más rápido/ligero
    # que el (potente pero lento) que los agentes usan para ejecutar tareas reales.
    # JSONB ``{}`` = el chat hereda el `model_config` de ejecución (cadena ADR 0065).
    chat_model_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Política de memoria del EQUIPO (ADR 0071). NULLABLE: NULL = el equipo no fija
    # política y sus miembros caen al memory_scope del agente / default plataforma.
    # Cuando se fija, gobierna la memoria de las ejecuciones de los proyectos de
    # este equipo (resuelto por project.team_id). Mismo enum que Agent.memory_scope.
    memory_scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Adopción de equipos built-in (Ola C / ADR 0066): espejo de los campos
    # forked_from de Agent. Un equipo adoptado enlaza al built-in origen para
    # diff/re-sync; NULL en equipos creados desde cero o built-in de plataforma.
    forked_from_team_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    forked_from_version: Mapped[str | None] = mapped_column(String(32), nullable=True)


# =============================================================================
# TeamMember (M:N junction)
# =============================================================================
class TeamMember(Base, TenantScopedMixin, TimestampMixin):
    """Junction equipo↔agente + `tenant_id` denormalizado (migración 0124).

    El `tenant_id` se deriva del EQUIPO, no del agente: un agente built-in de
    plataforma puede ser miembro del equipo de un tenant (`_verify_agent_visible`
    lo permite a propósito), y esa membresía es del tenant, no de la plataforma.
    """

    __tablename__ = "team_members"
    __table_args__ = (PrimaryKeyConstraint("team_id", "agent_id", name="pk_team_members"),)

    team_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Per-team role label -- can differ from Agent.role (e.g. a backend_dev
    # serving as architect_assistant within a specific team).
    role_in_team: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_team_leader: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    assignment_priority: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("100")
    )
