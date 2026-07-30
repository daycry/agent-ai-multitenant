"""Pydantic schemas for /conversations endpoints (Plan 03 task_03_03).

Conversation requests carry the project context, optional title, and the
current chat mode (planning by default). Messages carry the author kind
plus the author's UUID (user or agent), the textual content, the active
mode at send time, and optional attachments.

Strict validation:
  - ``current_mode='custom'`` requires ``custom_mode_name``; any other
    mode forbids it (mirrors the DB CHECK).
  - ``author_kind`` and the author_*_id fields must agree (the DB CHECK
    is the last line of defence; this raises 422 before the round-trip).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api_server.db.conversation import (
    ChatMode,
    Conversation,
    Message,
    MessageAuthorKind,
)

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


def _validate_custom_mode_invariants(self: BaseModel) -> BaseModel:
    mode = getattr(self, "current_mode", None)
    name = getattr(self, "custom_mode_name", None)
    if mode == ChatMode.CUSTOM and not name:
        raise ValueError("current_mode='custom' requires custom_mode_name")
    if mode is not None and mode != ChatMode.CUSTOM and name is not None:
        raise ValueError("custom_mode_name is only valid when current_mode='custom'")
    return self


# ---------------------------------------------------------------------------
# Chat-mode catalog (Plan 06.17 task_06_17_11)
# ---------------------------------------------------------------------------
class ChatModeResponse(BaseModel):
    """Una entrada del catálogo de modos de chat para la UI.

    La consume la vista "prompt efectivo" de la sección Persona, que combina el
    ``system_prompt`` del rol del agente con el del modo elegido (fuente única:
    el prompt del modo NO se duplica en el frontend). ``available=False`` marca
    el modo ``custom`` como "No disponible aún" (modos custom de extremo a
    extremo diferidos): la UI lo muestra pero lo deja deshabilitado.
    """

    model_config = _BASE_CONFIG

    name: str
    label_es: str
    label_en: str
    system_prompt: str
    available: bool


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------
class ConversationCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    title: str | None = Field(default=None, max_length=255)
    current_mode: ChatMode = ChatMode.PLANNING
    custom_mode_name: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _check_custom_mode(self) -> ConversationCreateRequest:
        return _validate_custom_mode_invariants(self)  # type: ignore[return-value]


class ConversationUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    title: str | None = Field(default=None, max_length=255)
    current_mode: ChatMode | None = None
    custom_mode_name: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _check_custom_mode(self) -> ConversationUpdateRequest:
        if self.current_mode is None:
            return self
        return _validate_custom_mode_invariants(self)  # type: ignore[return-value]


class ConversationResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    project_id: UUID
    title: str | None
    current_mode: str
    custom_mode_name: str | None
    related_plan_id: UUID | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


def to_conversation_response(c: Conversation) -> ConversationResponse:
    return ConversationResponse(
        id=c.id,
        tenant_id=c.tenant_id,
        project_id=c.project_id,
        title=c.title,
        current_mode=c.current_mode,
        custom_mode_name=c.custom_mode_name,
        related_plan_id=c.related_plan_id,
        created_by=c.created_by,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------
class MessageCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    author_kind: MessageAuthorKind
    # One of these must be set depending on author_kind. For ``user`` the
    # principal's user_id is used if author_user_id is omitted; agents
    # must always pass author_agent_id; system messages leave both NULL.
    author_user_id: UUID | None = None
    author_agent_id: UUID | None = None
    content: str = Field(default="", max_length=64_000)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    related_plan_id: UUID | None = None
    is_summary: bool = False

    @model_validator(mode="after")
    def _author_kind_consistency(self) -> MessageCreateRequest:
        if self.author_kind == MessageAuthorKind.USER and self.author_agent_id is not None:
            raise ValueError("author_kind='user' cannot carry author_agent_id")
        if self.author_kind == MessageAuthorKind.AGENT:
            if self.author_agent_id is None:
                raise ValueError("author_kind='agent' requires author_agent_id")
            if self.author_user_id is not None:
                raise ValueError("author_kind='agent' cannot carry author_user_id")
        if self.author_kind == MessageAuthorKind.SYSTEM and (
            self.author_user_id is not None or self.author_agent_id is not None
        ):
            raise ValueError("author_kind='system' cannot carry author ids")
        return self


class MessageResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    author_kind: str
    author_user_id: UUID | None
    author_agent_id: UUID | None
    content: str
    mode: str
    attachments: list[dict[str, Any]]
    related_plan_id: UUID | None
    is_summary: bool
    created_at: datetime


def to_message_response(m: Message) -> MessageResponse:
    return MessageResponse(
        id=m.id,
        tenant_id=m.tenant_id,
        conversation_id=m.conversation_id,
        author_kind=m.author_kind,
        author_user_id=m.author_user_id,
        author_agent_id=m.author_agent_id,
        content=m.content,
        mode=m.mode,
        attachments=m.attachments,
        related_plan_id=m.related_plan_id,
        is_summary=m.is_summary,
        created_at=m.created_at,
    )


class PlanningRolesResponse(BaseModel):
    """Los roles del equipo del proyecto que pueden hablar en una sesión de
    planificación — es decir, los que se pueden @-mencionar (`task_wf_43`).

    Se devuelve un objeto y no una lista pelada para poder añadir metadatos
    (etiqueta, agente asignado) sin romper a los clientes."""

    roles: list[str]
