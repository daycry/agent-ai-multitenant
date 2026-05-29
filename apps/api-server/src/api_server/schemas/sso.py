"""Pydantic schemas for the per-tenant OIDC SSO config CRUD (Plan 08 task_08_03).

These back the Tenant-Admin "SSO configuration" UI. The cardinal rule
(CLAUDE.md: no plaintext secrets in the DB, and never echo a stored
secret back to a client) shapes the I/O contract:

  * **Write** (:class:`SSOConfigUpsertRequest`): the operator *may* send
    a ``client_secret`` (plaintext, encrypted at rest before it ever
    touches the DB) OR a ``client_secret_ref`` (a Vault pointer). On an
    edit, omitting both keeps the previously stored secret untouched.
  * **Read** (:class:`SSOConfigResponse`): NEVER carries the secret.
    It exposes only whether a secret is configured and which backing
    store holds it (``client_secret_source``), so the UI can show
    "secret set / not set" without leaking the value.

A template (:class:`OIDCTemplateResponse`) is the static IdP-specific
half (issuer pattern, default scopes, claim mappings) the UI offers in a
picker; the tenant layers ``client_id`` + secret + any required param
(Azure tenant id, Okta domain, ...) on top.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

# Field length bounds mirror the SSOConfiguration columns so the API
# rejects over-long values with a 422 instead of a 500 at INSERT time.
_ISSUER_MAX = 512
_CLIENT_ID_MAX = 255
_DISPLAY_NAME_MAX = 120
_SECRET_REF_MAX = 512


class SSOConfigResponse(BaseModel):
    """A tenant's OIDC config as returned to the UI — never the secret.

    ``has_client_secret`` + ``client_secret_source`` let the UI render a
    "credential configured" indicator without the value ever leaving the
    server. ``client_secret_source`` is ``"vault"`` (a Vault ref),
    ``"encrypted"`` (Fernet at rest), or ``None`` (no secret set yet).
    """

    model_config = _BASE_CONFIG

    id: UUID
    provider: str
    display_name: str | None
    enabled: bool
    issuer: str
    client_id: str
    scopes: list[str]
    claim_mappings: dict[str, str]
    has_client_secret: bool
    client_secret_source: str | None
    created_at: datetime
    updated_at: datetime


class SSOConfigUpsertRequest(BaseModel):
    """Create-or-replace body for a tenant's OIDC config.

    The secret is optional on write: send ``client_secret`` (plaintext —
    encrypted at rest before it reaches the DB) or ``client_secret_ref``
    (a ``vault:`` pointer). Sending both is rejected. On an *edit*,
    sending neither leaves the existing stored secret in place.
    """

    model_config = _BASE_CONFIG

    display_name: str | None = Field(default=None, max_length=_DISPLAY_NAME_MAX)
    enabled: bool = Field(default=False)
    issuer: str = Field(min_length=1, max_length=_ISSUER_MAX)
    client_id: str = Field(min_length=1, max_length=_CLIENT_ID_MAX)
    # Exactly-zero-or-one of these. Plaintext is encrypted server-side and
    # never stored as-is; the ref is a Vault pointer resolved at login.
    client_secret: str | None = Field(default=None, min_length=1)
    client_secret_ref: str | None = Field(default=None, max_length=_SECRET_REF_MAX)
    scopes: list[str] = Field(default_factory=lambda: ["openid", "email", "profile"])
    claim_mappings: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _at_most_one_secret_form(self) -> SSOConfigUpsertRequest:
        if self.client_secret is not None and self.client_secret_ref is not None:
            raise ValueError(
                "provide at most one of client_secret (plaintext) or "
                "client_secret_ref (Vault pointer), never both"
            )
        return self


class OIDCTemplateResponse(BaseModel):
    """A per-IdP template surfaced to the UI picker (read-only).

    Projects :class:`api_server.auth.sso.templates.OIDCTemplate`. The UI
    uses ``required_params`` to render the extra fields a template needs
    (e.g. the Azure tenant id) and ``default_scopes`` / ``claim_mappings``
    to pre-fill the form.
    """

    model_config = _BASE_CONFIG

    template_id: str
    display_name: str
    issuer_template: str
    default_scopes: list[str]
    claim_mappings: dict[str, str]
    required_params: list[str]
    notes: str | None


class CallbackUrlResponse(BaseModel):
    """The OIDC redirect/callback URL to register in the IdP allowlist."""

    model_config = _BASE_CONFIG

    callback_url: str


__all__ = [
    "CallbackUrlResponse",
    "OIDCTemplateResponse",
    "SSOConfigResponse",
    "SSOConfigUpsertRequest",
]
