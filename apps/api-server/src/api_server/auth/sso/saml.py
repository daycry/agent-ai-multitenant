"""SAML 2.0 Service-Provider flow (Plan 08 task_08_04).

SSO is *added alongside* the existing email+password login and the
generic OIDC flow of Phase A (:mod:`api_server.auth.sso.oidc`). A SAML
login ends exactly like both of those: a server-side Redis session plus
a JWT — no stateless-JWT-after-SAML path.

The flow, end to end:

  1. **SP-initiated login** — ``GET /auth/sso/{tenant_id}/saml/login``
     builds a SAML ``AuthnRequest`` and 302-redirects the browser to the
     tenant IdP's SSO URL (HTTP-Redirect binding).
  2. **Assertion Consumer Service (ACS)** — ``POST /auth/sso/saml/acs``
     receives the IdP's ``SAMLResponse`` (HTTP-POST binding), verifies
     its XML signature against the IdP's x509 cert, validates conditions
     (audience, NotOnOrAfter, ...), then extracts the NameID + attributes.
  3. **JIT** — resolve/create the local user, mint the session + JWT.

**IdP-initiated** login is the same ACS endpoint receiving an unsolicited
``SAMLResponse`` (no in-flight ``RelayState`` / request id to correlate);
the SP-initiated path additionally validates the in-response-to request id.

Native dependency: SAML signature verification needs ``python3-saml``,
which binds the native ``xmlsec``/``libxml2`` libraries. Those are not
always installable on every platform. To keep the api-server importable
*everywhere* (so local login + OIDC never break because a native lib is
missing), ``python3-saml`` is imported **lazily inside the functions**.
When the import fails, the flow raises :class:`SAMLUnavailableError`,
which the router maps to a clean ``501 Not Implemented`` — SAML is simply
unavailable on that node, the rest of auth is untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

# Default NameID format requested in the AuthnRequest and used when a
# tenant config does not pin one explicitly.
DEFAULT_NAME_ID_FORMAT = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"


class SAMLError(Exception):
    """Any failure processing the SAML flow (bad config, invalid response).

    The router maps this onto a 4xx for client-attributable problems
    (an invalid/forged assertion) or a 500 for operator misconfiguration;
    the message stays generic for the client.
    """


class SAMLUnavailableError(SAMLError):
    """``python3-saml`` (native ``xmlsec``) could not be imported.

    Raised by the lazy import guard. The router maps it to ``501 Not
    Implemented`` so a node without the native crypto stack reports SAML
    as unavailable instead of crashing — local login and OIDC keep
    working regardless.
    """


def _import_saml() -> Any:
    """Lazily import ``python3-saml`` (and its native ``xmlsec`` backend).

    Kept out of module scope so importing this module never pulls in the
    native libs — the app boots fine on a node without them. Returns the
    ``OneLogin_Saml2_Auth`` class.

    Raises:
        SAMLUnavailableError: the package or its native backend is absent.
    """
    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth
    except ImportError as exc:  # pragma: no cover - exercised via the guard test
        raise SAMLUnavailableError(
            "SAML support is unavailable: python3-saml (and its native "
            "xmlsec/libxml2 backend) is not installed on this node"
        ) from exc
    return OneLogin_Saml2_Auth


def saml_available() -> bool:
    """Whether ``python3-saml`` can be imported on this node.

    A cheap probe the router uses to short-circuit to a 501 before doing
    any DB work, and the import-guard test asserts against.
    """
    try:
        _import_saml()
    except SAMLUnavailableError:
        return False
    return True


@dataclass(frozen=True)
class ResolvedSAMLConfig:
    """A per-tenant SAML config, ready to feed the flow.

    Built by the router from a ``sso_configurations`` row. The flow never
    touches the DB — it only sees this plain dataclass.

    ``attribute_mappings`` maps SAML attribute names onto local user
    fields, e.g. ``{"email": "...", "full_name": "..."}``. An empty
    mapping falls back to the NameID for the email and a small set of
    common attribute names for the full name.
    """

    idp_entity_id: str
    idp_sso_url: str
    idp_x509_cert: str
    sp_entity_id: str
    sp_acs_url: str
    name_id_format: str = DEFAULT_NAME_ID_FORMAT
    attribute_mappings: dict[str, str] = field(default_factory=dict)

    def to_settings(self) -> dict[str, Any]:
        """The ``python3-saml`` settings dict for this config.

        SP signing/encryption keys are out of scope for task_08_04 (they
        land in task_08_05 — XML signing/encryption); here the SP is
        unsigned and only *verifies* the IdP's signature, which is the
        security-critical direction for a login flow.
        """
        return {
            "strict": True,
            "debug": False,
            "sp": {
                "entityId": self.sp_entity_id,
                "assertionConsumerService": {
                    "url": self.sp_acs_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                },
                "NameIDFormat": self.name_id_format,
            },
            "idp": {
                "entityId": self.idp_entity_id,
                "singleSignOnService": {
                    "url": self.idp_sso_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
                "x509cert": self.idp_x509_cert,
            },
            "security": {
                # Require the IdP to sign the assertion (or the response).
                # This is the property that makes the login trustworthy.
                "wantAssertionsSigned": True,
                "wantMessagesSigned": False,
                "wantNameId": True,
                "requestedAuthnContext": False,
                # Accept single-label hosts (e.g. an internal hostname, or
                # `testserver` in tests). python3-saml rejects them by
                # default; the SP/ACS URLs here are operator-configured,
                # not user input, so this is safe.
                "allowSingleLabelDomains": True,
            },
        }


@dataclass(frozen=True)
class SAMLUserInfo:
    """Resolved identity from a validated SAML assertion.

    ``email`` is the JIT lookup key. ``attributes`` is the full attribute
    statement so later tasks (group→role mapping, task_08_11) can read
    more without re-parsing.
    """

    name_id: str
    email: str
    full_name: str | None
    attributes: dict[str, list[str]]


# Common SAML attribute names IdPs use for email / display name when the
# tenant config does not map them explicitly. Checked in order.
_EMAIL_FALLBACK_ATTRS = (
    "email",
    "mail",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "urn:oid:0.9.2342.19200300.100.1.3",
)
_NAME_FALLBACK_ATTRS = (
    "displayName",
    "name",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    "urn:oid:2.16.840.1.113730.3.1.241",
    "cn",
)


def _build_request_data(*, acs_url: str, post_data: dict[str, str] | None = None) -> dict[str, Any]:
    """Shape a ``python3-saml`` request-data dict from our ACS URL.

    ``python3-saml`` reads ``https``/``http_host``/``script_name`` to
    decide the SP's own URLs and to compare the assertion's
    ``Destination`` against the ACS. We derive them from the configured
    public ACS URL so the SP self-view matches what the IdP was told.
    """
    from urllib.parse import urlparse

    parsed = urlparse(acs_url)
    https = "on" if parsed.scheme == "https" else "off"
    host = parsed.netloc
    request_data: dict[str, Any] = {
        "https": https,
        "http_host": host,
        "script_name": parsed.path,
        "get_data": {},
        "post_data": post_data or {},
    }
    return request_data


def build_login_url(config: ResolvedSAMLConfig, *, relay_state: str) -> str:
    """Build the IdP SSO redirect URL for an SP-initiated login.

    Returns the absolute URL (AuthnRequest encoded in the query string,
    HTTP-Redirect binding) the browser must be 302'd to.

    Raises:
        SAMLUnavailableError: ``python3-saml`` is not installed.
        SAMLError: the AuthnRequest could not be built.
    """
    auth_cls = _import_saml()
    request_data = _build_request_data(acs_url=config.sp_acs_url)
    try:
        auth = auth_cls(request_data, old_settings=config.to_settings())
        return str(auth.login(return_to=relay_state))
    except SAMLUnavailableError:
        raise
    except Exception as exc:  # python3-saml raises a zoo of error types.
        raise SAMLError("failed to build the SAML AuthnRequest") from exc


def process_acs_response(
    config: ResolvedSAMLConfig,
    *,
    post_data: dict[str, str],
    request_id: str | None,
) -> SAMLUserInfo:
    """Validate a ``SAMLResponse`` at the ACS and extract the identity.

    Verifies the XML signature against the IdP cert, the audience, and the
    time conditions. When ``request_id`` is set (SP-initiated), the
    response's ``InResponseTo`` must match it; when ``None`` (IdP-initiated
    / unsolicited), that correlation is skipped.

    Raises:
        SAMLUnavailableError: ``python3-saml`` is not installed.
        SAMLError: the response is invalid, unsigned, or missing identity.
    """
    auth_cls = _import_saml()
    request_data = _build_request_data(acs_url=config.sp_acs_url, post_data=post_data)
    try:
        auth = auth_cls(request_data, old_settings=config.to_settings())
        auth.process_response(request_id=request_id)
    except SAMLUnavailableError:
        raise
    except Exception as exc:  # parse/verify errors surface as a generic failure.
        raise SAMLError("failed to process the SAML response") from exc

    if not auth.is_authenticated():
        reason = auth.get_last_error_reason() or "; ".join(auth.get_errors())
        raise SAMLError(f"SAML response validation failed: {reason or 'unknown'}")

    return _extract_identity(config, auth)


def _extract_identity(config: ResolvedSAMLConfig, auth: OneLogin_Saml2_Auth) -> SAMLUserInfo:
    """Map a validated assertion's NameID + attributes to a SAMLUserInfo."""
    name_id = auth.get_nameid()
    if not name_id:
        raise SAMLError("SAML assertion has no NameID")

    # python3-saml returns each attribute as a list of string values.
    raw_attrs = auth.get_attributes() or {}
    attributes: dict[str, list[str]] = {
        str(k): [str(v) for v in (vals or [])] for k, vals in raw_attrs.items()
    }

    email = _resolve_email(config, name_id, attributes)
    full_name = _resolve_full_name(config, attributes)
    return SAMLUserInfo(
        name_id=name_id,
        email=email.lower(),
        full_name=full_name,
        attributes=attributes,
    )


def _first(attributes: dict[str, list[str]], key: str) -> str | None:
    vals = attributes.get(key)
    if vals:
        return vals[0]
    return None


def _resolve_email(
    config: ResolvedSAMLConfig, name_id: str, attributes: dict[str, list[str]]
) -> str:
    """Resolve the user's email: mapped attribute → fallbacks → NameID.

    When the NameID format IS emailAddress, the NameID itself is a valid
    last-resort email.
    """
    mapped = config.attribute_mappings.get("email")
    if mapped:
        value = _first(attributes, mapped)
        if value:
            return value
        raise SAMLError(f"SAML assertion is missing mapped email attribute {mapped!r}")

    for candidate in _EMAIL_FALLBACK_ATTRS:
        value = _first(attributes, candidate)
        if value:
            return value

    if "@" in name_id:
        return name_id
    raise SAMLError("could not resolve an email from the SAML assertion")


def _resolve_full_name(config: ResolvedSAMLConfig, attributes: dict[str, list[str]]) -> str | None:
    mapped = config.attribute_mappings.get("full_name")
    if mapped:
        return _first(attributes, mapped)
    for candidate in _NAME_FALLBACK_ATTRS:
        value = _first(attributes, candidate)
        if value:
            return value
    return None


__all__ = [
    "DEFAULT_NAME_ID_FORMAT",
    "ResolvedSAMLConfig",
    "SAMLError",
    "SAMLUnavailableError",
    "SAMLUserInfo",
    "build_login_url",
    "process_acs_response",
    "saml_available",
]
