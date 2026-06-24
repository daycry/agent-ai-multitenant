"""Pydantic schemas for /teams endpoints (task_01_06).

A team is a tenant-scoped container of agents. The agents themselves
keep living in /agents; TeamMember is the M:N junction carrying per-team
metadata (role_in_team, is_team_leader, assignment_priority).

Responses bundle members inline so the admin UI can render a team with
its agents in one round-trip. For larger teams this is still cheap --
each row is small and the join is on the composite PK.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api_server.db.domain import MemoryScope, Team, TeamMember

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Member sub-schemas
# ---------------------------------------------------------------------------
class TeamMemberAddRequest(BaseModel):
    model_config = _BASE_CONFIG

    agent_id: UUID
    role_in_team: str | None = Field(default=None, max_length=64)
    is_team_leader: bool = False
    assignment_priority: int = Field(default=100, ge=0, le=1000)


class TeamMemberUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    role_in_team: str | None = Field(default=None, max_length=64)
    is_team_leader: bool | None = None
    assignment_priority: int | None = Field(default=None, ge=0, le=1000)


class TeamMemberResponse(BaseModel):
    model_config = _BASE_CONFIG

    agent_id: UUID
    role_in_team: str | None
    is_team_leader: bool
    assignment_priority: int
    created_at: datetime
    updated_at: datetime


def to_member_response(m: TeamMember) -> TeamMemberResponse:
    return TeamMemberResponse(
        agent_id=m.agent_id,
        role_in_team=m.role_in_team,
        is_team_leader=m.is_team_leader,
        assignment_priority=m.assignment_priority,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------
class TeamCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    default_workflow_template_id: UUID | None = None
    # Política de memoria del equipo (ADR 0071). None = sin política (los miembros
    # caen al memory_scope del agente / default de plataforma).
    memory_scope: MemoryScope | None = None


class TeamUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    default_workflow_template_id: UUID | None = None
    # Modelo por defecto del equipo (Ola A / ADR 0065). Alias JSON `model_config`
    # (igual que en Agent). `{}` = no fija modelo (hereda). `None` = no tocar.
    llm_config: dict[str, Any] | None = Field(default=None, alias="model_config")
    # Modelo del CHAT del equipo, separado del de ejecución. Alias JSON
    # `chat_model_config`. `{}` = el chat hereda el modelo de ejecución. `None` = no tocar.
    chat_llm_config: dict[str, Any] | None = Field(default=None, alias="chat_model_config")
    # Política de memoria del equipo (ADR 0071). `null` explícito = quitar política
    # (heredar); omitir = no tocar; un scope = fijarla (gobierna a los miembros).
    memory_scope: MemoryScope | None = None

    @model_validator(mode="after")
    def _validate_model(self) -> TeamUpdateRequest:
        from api_server.db.platform_settings import (
            InvalidModelConfigError,
            validate_chat_model_config,
            validate_model_config,
        )

        try:
            if self.llm_config:
                validate_model_config(self.llm_config)
            if self.chat_llm_config:  # may pin a concrete provider_id (Feature B)
                validate_chat_model_config(self.chat_llm_config)
        except InvalidModelConfigError as exc:
            raise ValueError(str(exc)) from exc
        return self


class TeamAdoptRequest(BaseModel):
    """Adopción de un equipo built-in como copia editable del tenant (Ola C).

    ``target`` decide el scope de los agentes forkeados: ``project`` →
    ``project_local`` (requiere ``project_id``); ``tenant`` →
    ``global_tenant_template`` (sin ``project_id``). ``model_config`` (alias del
    atributo Python ``llm_config``, igual que en Agent) fija opcionalmente el
    modelo del equipo nuevo (cadena de herencia, ADR 0065)."""

    model_config = _BASE_CONFIG

    target: Literal["project", "tenant"]
    project_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    llm_config: dict[str, Any] | None = Field(default=None, alias="model_config")

    @model_validator(mode="after")
    def _validate(self) -> TeamAdoptRequest:
        if self.target == "project" and self.project_id is None:
            raise ValueError("project_id is required when target='project'")
        if self.target == "tenant" and self.project_id is not None:
            raise ValueError("project_id is not allowed when target='tenant'")
        if self.llm_config:
            from api_server.db.platform_settings import (
                InvalidModelConfigError,
                validate_model_config,
            )

            try:
                validate_model_config(self.llm_config)
            except InvalidModelConfigError as exc:
                raise ValueError(str(exc)) from exc
        return self


class TeamResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    default_workflow_template_id: UUID | None
    is_builtin: bool
    forked_from_team_id: UUID | None
    llm_config: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    # Modelo del CHAT del equipo (separado del de ejecución). `{}` = hereda.
    chat_llm_config: dict[str, Any] = Field(default_factory=dict, alias="chat_model_config")
    # ADR 0071: política de memoria del equipo (None = sin política / heredar).
    memory_scope: str | None = None
    members: list[TeamMemberResponse]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


def to_team_response(t: Team, members: list[TeamMember]) -> TeamResponse:
    # Vía `model_validate` con la clave ALIAS `model_config`: el plugin mypy de
    # Pydantic no expone el kwarg field-name (`llm_config`) cuando hay alias
    # (mismo patrón que `to_agent_response`).
    payload: dict[str, Any] = {
        "id": t.id,
        "tenant_id": t.tenant_id,
        "name": t.name,
        "description": t.description,
        "default_workflow_template_id": t.default_workflow_template_id,
        "is_builtin": t.is_builtin,
        "forked_from_team_id": t.forked_from_team_id,
        "model_config": dict(t.model_config or {}),
        "chat_model_config": dict(t.chat_model_config or {}),
        "memory_scope": t.memory_scope,
        "members": [to_member_response(m) for m in members],
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "deleted_at": t.deleted_at,
    }
    return TeamResponse.model_validate(payload)
