"""Per-IdP OIDC templates (Plan 08 task_08_02).

A *template* is the static, IdP-specific half of an OIDC configuration:
the issuer/discovery URL pattern, the scopes that yield the claims we
care about, and the claim->field mapping (email / full name / groups).
The tenant only supplies the volatile half — ``client_id`` /
``client_secret`` plus any per-deployment parameter (the Azure tenant
id, the Okta/Auth0 domain, the GitLab base URL).

Everything lives in ONE data-driven registry (:data:`OIDC_TEMPLATES`)
keyed by :class:`OIDCTemplateId`. Adding an IdP is a single entry, never
scattered ``if provider == ...`` branches around the codebase.

How it is used (task_08_03 config-write path):

    template = get_template(OIDCTemplateId.AZURE_AD)
    config = template.build_config(params={"tenant": "<azure-tenant-guid>"})
    #  -> {issuer, scopes, claim_mappings}  (sensible defaults)
    # the tenant then layers client_id + the encrypted client_secret on top.

The resulting ``claim_mappings`` dict slots straight into the
``sso_configurations.claim_mappings`` column and is consumed by
:meth:`api_server.auth.sso.oidc.OIDCFlow._map_claims` (email + full_name)
and by the group->role mapper of task_08_11 (the ``groups`` key).

Caveats are encoded as data, not prose:

  * **GitHub** is *not* a standards OIDC provider for web login — it has
    no ``/.well-known/openid-configuration`` and its userinfo is the
    ``/user`` REST endpoint. We still register it so the UI can offer it
    and pre-fill the right claim names; the ``notes`` field records that
    the primary email needs the ``user:email`` scope + a second call.
  * **Facebook** exposes OIDC Limited Login with discovery at
    ``https://www.facebook.com/.well-known/openid-configuration`` but no
    ``groups`` concept — its template maps only email + name.
  * **Apple** returns ``name`` only on the *first* authorization and only
    in the ID token (never from userinfo), so its template marks
    ``name`` best-effort and has no groups.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Final

# --- canonical claim->field keys -------------------------------------------
# The three fields a template maps. Kept as named constants so the registry
# and the extractor never drift on string literals.
FIELD_EMAIL: Final = "email"
FIELD_FULL_NAME: Final = "full_name"
FIELD_GROUPS: Final = "groups"

# OIDC standard claim names (OpenID Connect Core 1.0 §5.1) used as the
# common-case defaults so most templates need no overrides. Groups have no
# OIDC-standard claim, so there is no default fallback for them.
_CLAIM_EMAIL: Final = "email"
_CLAIM_NAME: Final = "name"

# The scope every OIDC request must carry (mirrors oidc.OPENID_SCOPE; kept
# local to avoid a hard import cycle for a one-line constant).
_OPENID_SCOPE: Final = "openid"


class OIDCTemplateError(Exception):
    """Raised for an unknown template id or missing required parameters.

    Surfaced by the config-write path as a 400 (operator picked a bad
    template / forgot a required field), never a 500.
    """


class OIDCTemplateId(enum.StrEnum):
    """Closed set of supported per-IdP templates (Plan 08, scope list)."""

    AZURE_AD = "azure_ad"
    GOOGLE_WORKSPACE = "google_workspace"
    OKTA = "okta"
    AUTH0 = "auth0"
    GITHUB = "github"
    GITLAB = "gitlab"
    APPLE = "apple"
    FACEBOOK = "facebook"


@dataclass(frozen=True)
class OIDCTemplate:
    """The static, IdP-specific defaults for one provider family.

    ``issuer_template`` is a ``str.format``-style pattern; any ``{name}``
    placeholders must be supplied via :meth:`build_config`'s ``params``.
    A template with no placeholders (Google, Facebook, Apple, GitHub)
    needs no params.

    ``claim_mappings`` is local-field -> IdP-claim-name. Only fields the
    IdP actually provides are present (e.g. Apple/Facebook have no
    ``groups``), so the extractor never invents data.
    """

    template_id: OIDCTemplateId
    display_name: str
    issuer_template: str
    default_scopes: tuple[str, ...]
    claim_mappings: dict[str, str]
    # Free-form parameter name -> human description, for the UI to render
    # the extra fields the tenant must fill (e.g. the Azure tenant id).
    required_params: tuple[str, ...] = field(default_factory=tuple)
    # IdPs that are not standards-OIDC for web login (GitHub) carry notes
    # so the UI / docs can warn; purely informational, never magic.
    notes: str | None = None

    def build_issuer(self, params: dict[str, str] | None = None) -> str:
        """Render the issuer URL, substituting any required parameters.

        Raises:
            OIDCTemplateError: a required ``{placeholder}`` was not given.
        """
        params = params or {}
        missing = [p for p in self.required_params if not params.get(p)]
        if missing:
            raise OIDCTemplateError(
                f"template {self.template_id.value!r} requires parameter(s): {', '.join(missing)}"
            )
        try:
            return self.issuer_template.format(**params)
        except KeyError as exc:  # pragma: no cover - guarded by `missing` above
            raise OIDCTemplateError(
                f"template {self.template_id.value!r} missing parameter {exc}"
            ) from exc

    def scopes_with_openid(self) -> list[str]:
        """Default scopes with ``openid`` guaranteed first."""
        scopes = list(self.default_scopes)
        if _OPENID_SCOPE not in scopes:
            scopes.insert(0, _OPENID_SCOPE)
        return scopes

    def build_config(self, params: dict[str, str] | None = None) -> dict[str, object]:
        """Produce the generic-OIDC config defaults for this template.

        Returns a dict with ``issuer``, ``scopes`` and ``claim_mappings``
        ready to merge into a ``sso_configurations`` row. The tenant adds
        ``client_id`` + the (encrypted/Vault) ``client_secret`` and may
        override any default afterwards.
        """
        return {
            "issuer": self.build_issuer(params),
            "scopes": self.scopes_with_openid(),
            "claim_mappings": dict(self.claim_mappings),
        }


# ---------------------------------------------------------------------------
# The registry — ONE place. Each entry's claim mapping was chosen from the
# provider's documented OIDC userinfo / ID-token claim names.
# ---------------------------------------------------------------------------
OIDC_TEMPLATES: Final[dict[OIDCTemplateId, OIDCTemplate]] = {
    OIDCTemplateId.AZURE_AD: OIDCTemplate(
        template_id=OIDCTemplateId.AZURE_AD,
        display_name="Microsoft Entra ID (Azure AD)",
        # v2.0 endpoint; {tenant} is the directory (tenant) GUID or domain.
        issuer_template="https://login.microsoftonline.com/{tenant}/v2.0",
        default_scopes=("openid", "email", "profile"),
        # Entra emits the email in `email` (or `preferred_username`), the
        # display name in `name`, and group object-ids in `groups` when the
        # app manifest opts into the groups claim.
        claim_mappings={
            FIELD_EMAIL: "email",
            FIELD_FULL_NAME: "name",
            FIELD_GROUPS: "groups",
        },
        required_params=("tenant",),
        notes="`tenant` is the Entra directory (tenant) GUID. Enable the "
        "groups optional claim in the app registration for group mapping.",
    ),
    OIDCTemplateId.GOOGLE_WORKSPACE: OIDCTemplate(
        template_id=OIDCTemplateId.GOOGLE_WORKSPACE,
        display_name="Google Workspace",
        issuer_template="https://accounts.google.com",
        default_scopes=("openid", "email", "profile"),
        # Google's standard claims; groups are NOT in the OIDC userinfo —
        # they come from the Admin SDK, out of scope here, so no groups map.
        claim_mappings={
            FIELD_EMAIL: "email",
            FIELD_FULL_NAME: "name",
        },
        notes="Workspace groups are not in OIDC userinfo; group mapping "
        "would require the Directory API (out of scope for the template).",
    ),
    OIDCTemplateId.OKTA: OIDCTemplate(
        template_id=OIDCTemplateId.OKTA,
        display_name="Okta",
        # {domain} e.g. `dev-12345.okta.com`; default authorization server.
        issuer_template="https://{domain}/oauth2/default",
        default_scopes=("openid", "email", "profile", "groups"),
        claim_mappings={
            FIELD_EMAIL: "email",
            FIELD_FULL_NAME: "name",
            FIELD_GROUPS: "groups",
        },
        required_params=("domain",),
        notes="`domain` is the Okta org domain (e.g. dev-12345.okta.com). "
        "Add a `groups` claim to the authorization server for group mapping.",
    ),
    OIDCTemplateId.AUTH0: OIDCTemplate(
        template_id=OIDCTemplateId.AUTH0,
        display_name="Auth0",
        # Auth0 issuer has a trailing slash by spec; {domain} e.g.
        # `acme.eu.auth0.com`.
        issuer_template="https://{domain}/",
        default_scopes=("openid", "email", "profile"),
        # Auth0 namespaces custom claims; groups are commonly surfaced via a
        # rule/action under a namespaced claim. We default to a plain
        # `groups`; the tenant overrides with their namespace if needed.
        claim_mappings={
            FIELD_EMAIL: "email",
            FIELD_FULL_NAME: "name",
            FIELD_GROUPS: "groups",
        },
        required_params=("domain",),
        notes="`domain` is the Auth0 tenant domain. Groups require a "
        "post-login Action adding a (possibly namespaced) `groups` claim.",
    ),
    OIDCTemplateId.GITHUB: OIDCTemplate(
        template_id=OIDCTemplateId.GITHUB,
        display_name="GitHub",
        # GitHub is OAuth2, not standards-OIDC: no discovery document. The
        # issuer is recorded for identity, but the flow uses GitHub's fixed
        # OAuth endpoints + the /user REST userinfo.
        issuer_template="https://github.com",
        default_scopes=("read:user", "user:email"),
        # GitHub /user returns `login` (username) and `name`; the primary
        # email needs the `user:email` scope and a /user/emails call.
        claim_mappings={
            FIELD_EMAIL: "email",
            FIELD_FULL_NAME: "name",
        },
        notes="GitHub is OAuth2, not standards-OIDC (no discovery). Maps "
        "the /user REST claims; primary email needs the user:email scope.",
    ),
    OIDCTemplateId.GITLAB: OIDCTemplate(
        template_id=OIDCTemplateId.GITLAB,
        display_name="GitLab",
        # Works for gitlab.com and self-managed; {base_url} e.g.
        # `https://gitlab.com` or `https://gitlab.acme.internal`.
        issuer_template="{base_url}",
        default_scopes=("openid", "email", "profile"),
        # GitLab OIDC exposes `groups` (full paths) when the `openid`+
        # profile scopes are granted.
        claim_mappings={
            FIELD_EMAIL: "email",
            FIELD_FULL_NAME: "name",
            FIELD_GROUPS: "groups",
        },
        required_params=("base_url",),
        notes="`base_url` is the GitLab instance root URL (no trailing "
        "slash), e.g. https://gitlab.com or a self-managed instance.",
    ),
    OIDCTemplateId.APPLE: OIDCTemplate(
        template_id=OIDCTemplateId.APPLE,
        display_name="Sign in with Apple",
        issuer_template="https://appleid.apple.com",
        default_scopes=("openid", "email", "name"),
        # Apple returns email in the ID token; `name` only on the very
        # first authorization and never from userinfo. No groups.
        claim_mappings={
            FIELD_EMAIL: "email",
            FIELD_FULL_NAME: "name",
        },
        notes="Apple returns the name only on first authorization and only "
        "in the ID token; treat full_name as best-effort. No groups.",
    ),
    OIDCTemplateId.FACEBOOK: OIDCTemplate(
        template_id=OIDCTemplateId.FACEBOOK,
        display_name="Facebook",
        issuer_template="https://www.facebook.com",
        default_scopes=("openid", "email", "public_profile"),
        # Facebook OIDC Limited Login exposes email + name; no groups.
        claim_mappings={
            FIELD_EMAIL: "email",
            FIELD_FULL_NAME: "name",
        },
        notes="Facebook OIDC Limited Login exposes email + name only; no groups concept.",
    ),
}


def get_template(template_id: OIDCTemplateId | str) -> OIDCTemplate:
    """Look a template up by id (enum or raw string).

    Raises:
        OIDCTemplateError: ``template_id`` is not a registered template.
    """
    try:
        key = OIDCTemplateId(template_id)
    except ValueError as exc:
        valid = ", ".join(t.value for t in OIDCTemplateId)
        raise OIDCTemplateError(
            f"unknown OIDC template {template_id!r}; valid templates: {valid}"
        ) from exc
    template = OIDC_TEMPLATES.get(key)
    if template is None:  # pragma: no cover - registry is exhaustive over the enum
        raise OIDCTemplateError(f"no registry entry for template {key.value!r}")
    return template


def list_templates() -> list[OIDCTemplate]:
    """All registered templates, in declaration order (for the UI picker)."""
    return list(OIDC_TEMPLATES.values())


@dataclass(frozen=True)
class ExtractedClaims:
    """The three normalized identity fields a template can pull from a
    userinfo / ID-token payload. ``groups`` is always a list (possibly
    empty); ``email``/``full_name`` are ``None`` when the claim is absent.
    """

    email: str | None
    full_name: str | None
    groups: list[str]


def extract_claims(
    template: OIDCTemplate,
    payload: dict[str, object],
) -> ExtractedClaims:
    """Apply ``template.claim_mappings`` to a userinfo/ID-token ``payload``.

    Mirrors what :meth:`OIDCFlow._map_claims` does for email/full_name, and
    extends it with ``groups`` (used by task_08_11's group->role mapper).
    The group claim is coerced to a ``list[str]``: a single string becomes a
    one-element list, a list is filtered to its string members, anything
    else yields an empty list.
    """
    mapping = template.claim_mappings

    email = _coerce_str(payload.get(mapping.get(FIELD_EMAIL, _CLAIM_EMAIL)))
    full_name = _coerce_str(payload.get(mapping.get(FIELD_FULL_NAME, _CLAIM_NAME)))

    groups: list[str] = []
    groups_claim = mapping.get(FIELD_GROUPS)
    if groups_claim is not None:
        groups = _coerce_str_list(payload.get(groups_claim))

    return ExtractedClaims(email=email, full_name=full_name, groups=groups)


def _coerce_str(value: object) -> str | None:
    """Return ``value`` as a non-empty string, else ``None``."""
    if isinstance(value, str) and value:
        return value
    return None


def _coerce_str_list(value: object) -> list[str]:
    """Normalize a groups claim into a list of non-empty strings."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list | tuple):
        return [item for item in value if isinstance(item, str) and item]
    return []


__all__ = [
    "FIELD_EMAIL",
    "FIELD_FULL_NAME",
    "FIELD_GROUPS",
    "OIDC_TEMPLATES",
    "ExtractedClaims",
    "OIDCTemplate",
    "OIDCTemplateError",
    "OIDCTemplateId",
    "extract_claims",
    "get_template",
    "list_templates",
]
