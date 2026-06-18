"""Pydantic schemas for the `/admin/llm-providers` System-Admin CRUD (Plan 11.2 task_11_2_02).

The provider admin surface (System-Admin only, ADR 0028) has:

  * :class:`LLMProviderResponse` — a provider row as returned by list /
    get / create / update. It is **write-only-safe**: it carries ONLY
    non-secret fields (``id`` / ``kind`` / ``display_name`` / ``base_url``
    / ``is_active`` / ``config`` / timestamps) plus a derived
    ``has_credential`` boolean. It NEVER includes the credential, the
    Vault path's secret, or anything resolved from Vault.

  * :class:`LLMProviderCreateRequest` / :class:`LLMProviderUpdateRequest`
    — accept the credential as :class:`pydantic.SecretStr` per kind. The
    secret is written to Vault by the router and only ``secret_vault_path``
    is persisted; the secret is never stored in a DB column nor echoed.

Per-kind credential shapes mirror the installer (``installer_backend.
config``) so a provider configured at install time and one configured
from the admin UI speak the same fields:

  * ``claude_sdk`` : ``oauth_token`` (subscription OAuth token).
  * ``copilot``    : ``oauth_token`` (Device Flow OAuth token; minting is
                     task_11_2_03).
  * ``azure_foundry`` : ``api_key`` (APIM subscription key) + ``base_url``
                     (the APIM gateway endpoint).
  * ``ollama``     : ``base_url`` (the Ollama endpoint) + optional
                     ``bearer_token`` (Ollama Cloud).

Cross-field rules (a kind's required credential/endpoint must be present
on create) are validated here so the router stays thin.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from api_server.db.llm_providers import (
    SLUG_MAX_LENGTH,
    InvalidProviderSlugError,
    LlmProvider,
    LLMProviderKind,
    validate_provider_slug,
)
from api_server.llm_providers.vault import (
    SECRET_FIELD_API_KEY,
    SECRET_FIELD_BEARER_TOKEN,
    SECRET_FIELD_OAUTH_TOKEN,
)

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


# =============================================================================
# Response (write-only-safe: NEVER carries a secret)
# =============================================================================
class LLMProviderResponse(BaseModel):
    """A provider row as exposed to the admin read/write endpoints.

    Deliberately carries NO secret. ``has_credential`` is the derived
    boolean the UI uses to render "credential set" without ever seeing the
    value. ``secret_vault_path`` (a pointer, not a secret) is surfaced so
    the operator knows where the credential lives — it is never the
    credential itself.
    """

    model_config = _BASE_CONFIG

    id: UUID
    kind: str
    # Stable, unique kebab-case handle (ollama-local / ollama-cloud / …).
    slug: str
    display_name: str
    base_url: str | None
    is_active: bool
    config: dict[str, Any]
    # The Vault POINTER (platform/llm/<id>) — never the secret value.
    secret_vault_path: str | None
    # Derived: a credential has been written to Vault for this provider.
    has_credential: bool
    created_at: datetime
    updated_at: datetime


def to_provider_response(provider: LlmProvider, *, has_credential: bool) -> LLMProviderResponse:
    """Map an ORM ``LlmProvider`` to its (secret-free) response model.

    ``has_credential`` is computed by the router from the persisted
    ``secret_vault_path`` (a pointer present ⇒ a credential was written),
    so the response never needs to touch Vault to render it.
    """
    return LLMProviderResponse(
        id=provider.id,
        kind=provider.kind,
        slug=provider.slug,
        display_name=provider.display_name,
        base_url=provider.base_url,
        is_active=provider.is_active,
        config=dict(provider.config or {}),
        secret_vault_path=provider.secret_vault_path,
        has_credential=has_credential,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


# =============================================================================
# Create
# =============================================================================
class LLMProviderCreateRequest(BaseModel):
    """Create a provider. The credential (when the kind needs one) is a
    :class:`SecretStr` written to Vault — never persisted in a DB column.

    Required per kind (validated below):

      * claude_sdk / copilot : ``oauth_token``.
      * azure_foundry        : ``api_key`` + ``base_url``.
      * ollama               : ``base_url`` (``bearer_token`` optional).
    """

    model_config = _BASE_CONFIG

    kind: LLMProviderKind
    # REQUIRED unique handle. Normalised (trim/lower) + validated as kebab-case
    # by the shared validator — the single source of truth for the slug shape.
    slug: str = Field(min_length=1, max_length=SLUG_MAX_LENGTH)
    display_name: str = Field(min_length=1, max_length=255)
    base_url: str | None = Field(default=None, max_length=2048)
    is_active: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    # Per-kind credential fields (write-only). At most one is meaningful for
    # a given kind; the validator enforces the required one is present.
    oauth_token: SecretStr | None = None
    api_key: SecretStr | None = None
    bearer_token: SecretStr | None = None

    @field_validator("slug")
    @classmethod
    def _normalise_slug(cls, value: str) -> str:
        try:
            return validate_provider_slug(value)
        except InvalidProviderSlugError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def _check_kind_requirements(self) -> LLMProviderCreateRequest:
        _validate_kind_fields(
            kind=self.kind,
            base_url=self.base_url,
            oauth_token=self.oauth_token,
            api_key=self.api_key,
            require_credential=True,
        )
        return self

    def credential_fields(self) -> dict[str, str]:
        """The ``{field: value}`` secret dict to write to Vault (may be empty
        for a local Ollama with no bearer)."""
        return _credential_fields(
            oauth_token=self.oauth_token,
            api_key=self.api_key,
            bearer_token=self.bearer_token,
        )


# =============================================================================
# Update (PUT — full replace of the editable, non-key fields)
# =============================================================================
class LLMProviderUpdateRequest(BaseModel):
    """Update a provider's editable fields. ``kind`` is immutable (a kind
    change is a different provider). A credential field, when supplied,
    rotates the Vault secret; omitting it leaves the existing credential
    untouched.
    """

    model_config = _BASE_CONFIG

    # Optional rename of the unique handle; validated when supplied.
    slug: str | None = Field(default=None, min_length=1, max_length=SLUG_MAX_LENGTH)
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str | None = Field(default=None, max_length=2048)
    is_active: bool | None = None
    config: dict[str, Any] | None = None

    # Supplying a credential rotates it; omitting it keeps the current one.
    oauth_token: SecretStr | None = None
    api_key: SecretStr | None = None
    bearer_token: SecretStr | None = None

    @field_validator("slug")
    @classmethod
    def _normalise_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return validate_provider_slug(value)
        except InvalidProviderSlugError as exc:
            raise ValueError(str(exc)) from exc

    def has_changes(self) -> bool:
        """True when at least one field was supplied on the wire."""
        return bool(self.model_dump(exclude_unset=True))

    def credential_fields(self) -> dict[str, str]:
        """The ``{field: value}`` secret dict to (re)write to Vault, or empty
        when no credential field was supplied (keep the current secret)."""
        return _credential_fields(
            oauth_token=self.oauth_token,
            api_key=self.api_key,
            bearer_token=self.bearer_token,
        )


# =============================================================================
# Test-connection response
# =============================================================================
class LLMProviderTestResponse(BaseModel):
    """Result of ``POST /admin/llm-providers/{id}/test``. Secret-free.

    ``ok`` is the boolean the UI toggles on; ``status`` is the classified
    outcome (ok / auth_error / connection_error / config_error /
    upstream_error); ``detail`` is a human, secret-free message.
    """

    model_config = _BASE_CONFIG

    ok: bool
    status: str
    detail: str


class LLMProviderModelsSyncResponse(BaseModel):
    """Result of ``POST /admin/llm-providers/{id}/sync-models``.

    ``models`` is the model ids discovered from the provider's ``/v1/models``
    and persisted on ``config.models``; ``count`` is their number. An empty
    list means nothing was discovered (no listing API / call failed) and the
    previously-stored list was left untouched."""

    model_config = _BASE_CONFIG

    models: list[str]
    count: int


# ---------------------------------------------------------------------------
# Shared per-kind validation + credential extraction.
# ---------------------------------------------------------------------------
def _validate_kind_fields(
    *,
    kind: LLMProviderKind,
    base_url: str | None,
    oauth_token: SecretStr | None,
    api_key: SecretStr | None,
    require_credential: bool,
) -> None:
    """Raise ``ValueError`` (→ 422) when a kind's required fields are absent.

    ``require_credential`` is True on create (the credential must be set) and
    False on update (a credential may be rotated, but omitting it keeps the
    existing one).
    """
    if kind == LLMProviderKind.CLAUDE_SDK:
        # Two auth modes on the same kind (ADR 0063): an Anthropic `api_key`
        # (→ ANTHROPIC_API_KEY) or a Pro/Max subscription `oauth_token` from
        # `claude setup-token` (→ CLAUDE_CODE_OAUTH_TOKEN). Either satisfies the
        # credential requirement; the resolver routes each to the right env var.
        if require_credential and oauth_token is None and api_key is None:
            raise ValueError(
                "claude_sdk requires a credential: an api_key (Anthropic) or an "
                "oauth_token (Pro/Max subscription, from `claude setup-token`)"
            )
    elif kind == LLMProviderKind.COPILOT:
        if require_credential and oauth_token is None:
            raise ValueError("copilot requires an oauth_token")
    elif kind == LLMProviderKind.AZURE_FOUNDRY:
        if not base_url:
            raise ValueError("azure_foundry requires base_url (the APIM gateway endpoint)")
        if require_credential and api_key is None:
            raise ValueError("azure_foundry requires an api_key")
    elif kind == LLMProviderKind.OLLAMA and not base_url:
        raise ValueError("ollama requires base_url (the Ollama endpoint)")


def _credential_fields(
    *,
    oauth_token: SecretStr | None,
    api_key: SecretStr | None,
    bearer_token: SecretStr | None,
) -> dict[str, str]:
    """Collect the supplied credential fields into a Vault secret dict.

    Only fields that were actually supplied are included (a None field is
    omitted), so an Ollama with no bearer yields an empty dict (no secret to
    write).
    """
    fields: dict[str, str] = {}
    if oauth_token is not None:
        fields[SECRET_FIELD_OAUTH_TOKEN] = oauth_token.get_secret_value()
    if api_key is not None:
        fields[SECRET_FIELD_API_KEY] = api_key.get_secret_value()
    if bearer_token is not None:
        fields[SECRET_FIELD_BEARER_TOKEN] = bearer_token.get_secret_value()
    return fields


__all__ = [
    "LLMProviderCreateRequest",
    "LLMProviderModelsSyncResponse",
    "LLMProviderResponse",
    "LLMProviderTestResponse",
    "LLMProviderUpdateRequest",
    "to_provider_response",
]
