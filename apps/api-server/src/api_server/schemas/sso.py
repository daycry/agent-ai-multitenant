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


# ===========================================================================
# SAML 2.0 per-tenant config CRUD (Plan 08 task_08_06) — the Tenant-Admin UI.
#
# These mirror the OIDC schemas above for the SAML provider. The same
# cardinal rules apply: the SP private key (a secret) is NEVER echoed back
# — the read shape only reports whether one is set and which store holds
# it. The IdP signing cert and the SP public cert are NOT secret (the
# operator pastes/registers them), so they round-trip in clear.
# ===========================================================================
_IDP_ENTITY_ID_MAX = 512
_IDP_SSO_URL_MAX = 1024
_NAME_ID_FORMAT_MAX = 128
_SP_KEY_REF_MAX = 512

# The default NameID format mirrors the DB server_default; the UI offers a
# small closed picker but the API accepts any non-empty URN up to the
# column bound.
DEFAULT_SAML_NAME_ID_FORMAT = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"


class SAMLConfigResponse(BaseModel):
    """A tenant's SAML config as returned to the UI — never the SP key.

    The IdP signing cert (``idp_x509_cert``) and the SP public cert
    (``sp_x509_cert``) are not secret and round-trip in clear. The SP
    PRIVATE key never crosses this boundary: ``has_sp_private_key`` +
    ``sp_private_key_source`` (``"vault"`` / ``"encrypted"`` / ``None``)
    let the UI render a "key configured" indicator without the value ever
    leaving the server.
    """

    model_config = _BASE_CONFIG

    id: UUID
    provider: str
    display_name: str | None
    enabled: bool
    idp_entity_id: str
    idp_sso_url: str
    idp_x509_cert: str
    name_id_format: str
    attribute_mappings: dict[str, str]
    sp_x509_cert: str | None
    has_sp_private_key: bool
    sp_private_key_source: str | None
    authn_requests_signed: bool
    want_assertions_signed: bool
    want_assertions_encrypted: bool
    want_name_id_encrypted: bool
    created_at: datetime
    updated_at: datetime


class SAMLConfigUpsertRequest(BaseModel):
    """Create-or-replace body for a tenant's SAML config.

    The SP private key is optional on write: send ``sp_private_key``
    (plaintext PEM — Fernet-encrypted at rest before it reaches the DB) or
    ``sp_private_key_ref`` (a ``vault:`` pointer). Sending both is
    rejected. On an *edit*, sending neither leaves the existing stored key
    in place.

    The signing/encryption invariant (mirrors the DB CHECK constraint and
    :func:`api_server.auth.sso.saml.validate_saml_security`): enabling
    AuthnRequest signing, assertion encryption, or NameID encryption
    requires BOTH an SP cert and an SP private key. We surface a 422 here
    so the operator gets a clear message instead of a DB error — but only
    when the request itself supplies (or already-implies) no key. The
    router does the final cross-check against the stored key on edit.
    """

    model_config = _BASE_CONFIG

    display_name: str | None = Field(default=None, max_length=_DISPLAY_NAME_MAX)
    enabled: bool = Field(default=False)
    idp_entity_id: str = Field(min_length=1, max_length=_IDP_ENTITY_ID_MAX)
    idp_sso_url: str = Field(min_length=1, max_length=_IDP_SSO_URL_MAX)
    idp_x509_cert: str = Field(min_length=1)
    name_id_format: str = Field(
        default=DEFAULT_SAML_NAME_ID_FORMAT, min_length=1, max_length=_NAME_ID_FORMAT_MAX
    )
    attribute_mappings: dict[str, str] = Field(default_factory=dict)
    # SP public cert is not secret; the IdP needs it to verify/encrypt.
    sp_x509_cert: str | None = Field(default=None)
    # Exactly-zero-or-one of these. Plaintext PEM is encrypted server-side
    # and never stored as-is; the ref is a Vault pointer resolved at login.
    sp_private_key: str | None = Field(default=None, min_length=1)
    sp_private_key_ref: str | None = Field(default=None, max_length=_SP_KEY_REF_MAX)
    authn_requests_signed: bool = Field(default=False)
    want_assertions_signed: bool = Field(default=True)
    want_assertions_encrypted: bool = Field(default=False)
    want_name_id_encrypted: bool = Field(default=False)

    @model_validator(mode="after")
    def _at_most_one_key_form(self) -> SAMLConfigUpsertRequest:
        if self.sp_private_key is not None and self.sp_private_key_ref is not None:
            raise ValueError(
                "provide at most one of sp_private_key (plaintext PEM) or "
                "sp_private_key_ref (Vault pointer), never both"
            )
        return self


class SPMetadataResponse(BaseModel):
    """The SP-side identifiers the operator must register at the IdP.

    The ACS URL is per-tenant (so an IdP-initiated, unsolicited Response
    reaches the right tenant's config); the EntityID is the stable value
    the IdP knows this SP by.
    """

    model_config = _BASE_CONFIG

    sp_entity_id: str
    acs_url: str


class IdPMetadataParseRequest(BaseModel):
    """Raw SAML 2.0 IdP metadata XML pasted/uploaded by the operator."""

    model_config = _BASE_CONFIG

    metadata_xml: str = Field(min_length=1)


class IdPMetadataParseResponse(BaseModel):
    """The fields extracted from an IdP metadata document.

    Pre-fills the SAML config form. ``sso_url`` / ``x509_cert`` may be
    empty if the document omits an HTTP-Redirect SSO binding or a signing
    certificate; the UI then asks the operator to fill them manually.
    """

    model_config = _BASE_CONFIG

    entity_id: str
    sso_url: str
    x509_cert: str
    name_id_format: str | None


__all__ = [
    "DEFAULT_SAML_NAME_ID_FORMAT",
    "CallbackUrlResponse",
    "IdPMetadataParseRequest",
    "IdPMetadataParseResponse",
    "OIDCTemplateResponse",
    "SAMLConfigResponse",
    "SAMLConfigUpsertRequest",
    "SPMetadataResponse",
    "SSOConfigResponse",
    "SSOConfigUpsertRequest",
]
