"""Pydantic schemas for the personal-assistant endpoints (Plan 10 task_10_14).

Two surfaces:

  * ``/assistant/chat`` — POST a question, get the assistant's answer plus
    the names of the cross-project read tools it invoked.
  * ``/assistant/identity`` — GET/PUT the tenant-level customizable identity
    (name, avatar, tone, language, system_prompt override, enabled tools).

Both are gated to Tenant Admins of a tenant whose
``personal_assistant_enabled`` toggle is ON (the router dependency does the
gating; these schemas only shape the payloads).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from api_server.assistant.config import (
    DEFAULT_ENABLED_TOOLS,
    SUPPORTED_LANGUAGES,
    AssistantIdentity,
)

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class AssistantChatRequest(BaseModel):
    model_config = _BASE_CONFIG

    message: str = Field(min_length=1, max_length=4000)


class AssistantChatResponse(BaseModel):
    model_config = _BASE_CONFIG

    answer: str
    tools_called: list[str]
    rounds: int


class AssistantIdentityResponse(BaseModel):
    model_config = _BASE_CONFIG

    name: str
    avatar_url: str | None = None
    tone: str
    language: str
    system_prompt_override: str | None = None
    enabled_tools: list[str]


class AssistantIdentityUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=120)
    avatar_url: str | None = Field(default=None, max_length=2048)
    tone: str = Field(min_length=1, max_length=200)
    language: str = Field(default="es")
    system_prompt_override: str | None = Field(default=None, max_length=8000)
    enabled_tools: list[str] = Field(default_factory=lambda: list(DEFAULT_ENABLED_TOOLS))

    def to_identity(self) -> AssistantIdentity:
        # ``from_dict`` clamps language to the supported set and intersects
        # the tool list with the catalogue, so this is the single coercion
        # point.
        return AssistantIdentity.from_dict(self.model_dump())


def to_identity_response(identity: AssistantIdentity) -> AssistantIdentityResponse:
    return AssistantIdentityResponse(
        name=identity.name,
        avatar_url=identity.avatar_url,
        tone=identity.tone,
        language=identity.language,
        system_prompt_override=identity.system_prompt_override,
        enabled_tools=list(identity.enabled_tools),
    )


__all__ = [
    "SUPPORTED_LANGUAGES",
    "AssistantChatRequest",
    "AssistantChatResponse",
    "AssistantIdentityResponse",
    "AssistantIdentityUpdateRequest",
    "to_identity_response",
]
