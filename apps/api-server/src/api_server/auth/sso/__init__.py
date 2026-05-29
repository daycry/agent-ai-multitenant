"""Enterprise SSO for the api-server (Plan 08).

Phase A ships the generic OIDC flow. SSO is *added alongside* the
existing email+password login (``routers/auth.py``): it reuses the same
server-side Redis session model (:class:`api_server.auth.sessions.SessionStore`)
and the same JWT minting (:func:`api_server.auth.jwt.encode_jwt`). There
is no stateless-JWT-after-OIDC path — a successful OIDC callback creates
a Redis session exactly like local login, so logout/revocation stay
uniform across both auth methods.

Submodules:

  * :mod:`api_server.auth.sso.secrets`     — resolve the OIDC client
    secret from Vault or Fernet-encrypted-at-rest. Never plaintext in DB.
  * :mod:`api_server.auth.sso.state_store` — short-lived Redis store for
    the anti-CSRF ``state`` + replay-guard ``nonce``.
  * :mod:`api_server.auth.sso.oidc`        — discovery, authorize-URL
    construction, code→token exchange, userinfo fetch, nonce validation.
"""

from __future__ import annotations

from api_server.auth.sso.oidc import (
    OIDCError,
    OIDCFlow,
    OIDCUserInfo,
    ResolvedOIDCConfig,
)
from api_server.auth.sso.saml import (
    DEFAULT_NAME_ID_FORMAT,
    ResolvedSAMLConfig,
    SAMLError,
    SAMLUnavailableError,
    SAMLUserInfo,
    build_login_url,
    process_acs_response,
    saml_available,
)
from api_server.auth.sso.secrets import (
    SSOSecretError,
    decrypt_client_secret,
    encrypt_client_secret,
    resolve_client_secret,
)
from api_server.auth.sso.state_store import (
    LoginState,
    OIDCStateStore,
    SAMLLoginState,
    SAMLRelayStateStore,
)
from api_server.auth.sso.templates import (
    OIDC_TEMPLATES,
    ExtractedClaims,
    OIDCTemplate,
    OIDCTemplateError,
    OIDCTemplateId,
    extract_claims,
    get_template,
    list_templates,
)

__all__ = [
    "DEFAULT_NAME_ID_FORMAT",
    "OIDC_TEMPLATES",
    "ExtractedClaims",
    "LoginState",
    "OIDCError",
    "OIDCFlow",
    "OIDCStateStore",
    "OIDCTemplate",
    "OIDCTemplateError",
    "OIDCTemplateId",
    "OIDCUserInfo",
    "ResolvedOIDCConfig",
    "ResolvedSAMLConfig",
    "SAMLError",
    "SAMLLoginState",
    "SAMLRelayStateStore",
    "SAMLUnavailableError",
    "SAMLUserInfo",
    "SSOSecretError",
    "build_login_url",
    "decrypt_client_secret",
    "encrypt_client_secret",
    "extract_claims",
    "get_template",
    "list_templates",
    "process_acs_response",
    "resolve_client_secret",
    "saml_available",
]
