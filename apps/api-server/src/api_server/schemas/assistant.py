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

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


# ===========================================================================
# Model selection (ADR 0053)
# ===========================================================================
class AssistantModelResponse(BaseModel):
    """The effective model resolved for the tenant's assistant.

    All fields are ``None`` when nothing usable is configured (the chat
    endpoint then returns 503). ``source`` says which tier won
    (``tenant_override`` | ``platform_default``); ``has_tenant_override``
    lets the UI show "overriding" vs "inheriting the platform default".
    """

    model_config = _BASE_CONFIG

    provider_id: str | None = None
    model_id: str | None = None
    source: str | None = None
    provider_kind: str | None = None
    provider_display_name: str | None = None
    has_tenant_override: bool = False
    # ADR 0070: esfuerzo de razonamiento efectivo (None = sin razonar).
    reasoning_effort: str | None = None


class AssistantModelUpdateRequest(BaseModel):
    """Set or clear the tenant model override.

    Provide BOTH ``provider_id`` and ``model_id`` to set the override, or
    BOTH ``None`` to clear it (the assistant then inherits the platform
    default). Providing exactly one is a 422.
    """

    model_config = _BASE_CONFIG

    provider_id: str | None = Field(default=None, max_length=64)
    model_id: str | None = Field(default=None, max_length=255)
    # ADR 0070: opcional; validado por proveedor en el router. "off"/None = sin razonar.
    reasoning_effort: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def _both_or_neither(self) -> AssistantModelUpdateRequest:
        if (self.provider_id is None) != (self.model_id is None):
            raise ValueError("provide both provider_id and model_id to set, or neither to clear")
        return self

    @property
    def is_clear(self) -> bool:
        return self.provider_id is None and self.model_id is None


class AssistantModelOption(BaseModel):
    """One active provider plus the model ids selectable on it."""

    model_config = _BASE_CONFIG

    provider_id: str
    kind: str
    # Unique kebab-case handle — disambiguates same-kind providers in the
    # dropdown (e.g. ``ollama-local`` vs ``ollama-cloud``).
    slug: str
    display_name: str
    models: list[str]


class AssistantModelOptionsResponse(BaseModel):
    model_config = _BASE_CONFIG

    providers: list[AssistantModelOption]
    # ADR 0070: opciones de razonamiento por kind de proveedor (off + niveles).
    reasoning_by_kind: dict[str, list[str]] = Field(default_factory=dict)


class AssistantDefaultModelResponse(BaseModel):
    """The platform default model (System-Admin surface).

    ``provider_id``/``model_id`` are the stored choice (``None`` when unset);
    ``is_valid`` is whether that choice still resolves against the current
    catalogue (a stale default — disabled provider / retired model — reads
    back so the operator can fix it)."""

    model_config = _BASE_CONFIG

    provider_id: str | None = None
    model_id: str | None = None
    is_valid: bool = False
    provider_display_name: str | None = None
    # ADR 0070: esfuerzo de razonamiento del default de plataforma.
    reasoning_effort: str | None = None


class AssistantDefaultModelUpdateRequest(BaseModel):
    """Set or clear the platform default (both fields or neither)."""

    model_config = _BASE_CONFIG

    provider_id: str | None = Field(default=None, max_length=64)
    model_id: str | None = Field(default=None, max_length=255)
    reasoning_effort: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def _both_or_neither(self) -> AssistantDefaultModelUpdateRequest:
        if (self.provider_id is None) != (self.model_id is None):
            raise ValueError("provide both provider_id and model_id to set, or neither to clear")
        return self

    @property
    def is_clear(self) -> bool:
        return self.provider_id is None and self.model_id is None


__all__ = [
    "SUPPORTED_LANGUAGES",
    "AssistantChatRequest",
    "AssistantChatResponse",
    "AssistantDefaultModelResponse",
    "AssistantDefaultModelUpdateRequest",
    "AssistantIdentityResponse",
    "AssistantIdentityUpdateRequest",
    "AssistantModelOption",
    "AssistantModelOptionsResponse",
    "AssistantModelResponse",
    "AssistantModelUpdateRequest",
    "to_identity_response",
]
