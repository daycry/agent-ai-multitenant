"""Per-IdP OIDC template tests (Plan 08 task_08_02).

The template registry (:mod:`api_server.auth.sso.templates`) is pure
data + helpers — no IdP, no DB, no Redis — so these tests run fully
offline. They assert three things the task mandates:

  * every registered template yields a VALID generic-OIDC config
    (a well-formed https issuer, ``openid`` always present, a claim map
    that at least carries email + name) and that the config slots into
    the real :class:`api_server.auth.sso.oidc.ResolvedOIDCConfig` and is
    consumed correctly by the generic flow's claim mapper;
  * the claim mapping extracts email / name / groups from a
    representative userinfo payload for each provider;
  * an unknown template id raises :class:`OIDCTemplateError`.

A representative userinfo payload per provider is built from each
template's own claim names, so the test stays in lock-step with the
registry: change a claim name in the registry and the payload follows.
"""

from __future__ import annotations

import pytest
from api_server.auth.sso.oidc import OPENID_SCOPE, OIDCFlow, ResolvedOIDCConfig
from api_server.auth.sso.templates import (
    FIELD_EMAIL,
    FIELD_FULL_NAME,
    FIELD_GROUPS,
    OIDC_TEMPLATES,
    OIDCTemplate,
    OIDCTemplateError,
    OIDCTemplateId,
    extract_claims,
    get_template,
    list_templates,
)

pytestmark = pytest.mark.integration

# The required parameter each parametrized template needs to render its
# issuer. Templates without placeholders are absent here.
_PARAMS_BY_TEMPLATE: dict[OIDCTemplateId, dict[str, str]] = {
    OIDCTemplateId.AZURE_AD: {"tenant": "00000000-1111-2222-3333-444444444444"},
    OIDCTemplateId.OKTA: {"domain": "dev-12345.okta.com"},
    OIDCTemplateId.AUTH0: {"domain": "acme.eu.auth0.com"},
    OIDCTemplateId.GITLAB: {"base_url": "https://gitlab.com"},
}

# Representative values placed under each provider's mapped claim names.
_SAMPLE_EMAIL = "Worker@Acme.test"
_SAMPLE_NAME = "Worker Person"
_SAMPLE_GROUPS = ["engineering", "admins"]


def _params_for(template: OIDCTemplate) -> dict[str, str]:
    return _PARAMS_BY_TEMPLATE.get(template.template_id, {})


def _sample_payload(template: OIDCTemplate) -> dict[str, object]:
    """Build a userinfo payload using THIS template's claim names."""
    mapping = template.claim_mappings
    payload: dict[str, object] = {"sub": "idp-subject-123"}
    payload[mapping[FIELD_EMAIL]] = _SAMPLE_EMAIL
    payload[mapping[FIELD_FULL_NAME]] = _SAMPLE_NAME
    if FIELD_GROUPS in mapping:
        payload[mapping[FIELD_GROUPS]] = list(_SAMPLE_GROUPS)
    return payload


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------
def test_registry_covers_every_template_id() -> None:
    """One entry per enum member — no gaps, no extras."""
    assert set(OIDC_TEMPLATES.keys()) == set(OIDCTemplateId)
    # Keys and the entries' own ids agree.
    for tid, template in OIDC_TEMPLATES.items():
        assert template.template_id == tid
    # list_templates() exposes them all.
    assert {t.template_id for t in list_templates()} == set(OIDCTemplateId)


def test_all_eight_providers_present() -> None:
    """The plan names exactly these eight IdPs (scope guard)."""
    expected = {
        OIDCTemplateId.AZURE_AD,
        OIDCTemplateId.GOOGLE_WORKSPACE,
        OIDCTemplateId.OKTA,
        OIDCTemplateId.AUTH0,
        OIDCTemplateId.GITHUB,
        OIDCTemplateId.GITLAB,
        OIDCTemplateId.APPLE,
        OIDCTemplateId.FACEBOOK,
    }
    assert set(OIDCTemplateId) == expected


# ---------------------------------------------------------------------------
# Each template yields a valid generic OIDC config
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("template", list_templates(), ids=lambda t: t.template_id.value)
def test_template_builds_valid_generic_config(template: OIDCTemplate) -> None:
    config = template.build_config(_params_for(template))

    # issuer: a well-formed https URL with no leftover placeholders.
    issuer = config["issuer"]
    assert isinstance(issuer, str)
    assert issuer.startswith("https://"), issuer
    assert "{" not in issuer and "}" not in issuer, issuer

    # scopes: openid always present, non-empty, no duplicates.
    scopes = config["scopes"]
    assert isinstance(scopes, list)
    assert OPENID_SCOPE in scopes
    assert len(scopes) == len(set(scopes))

    # claim map: email + full_name are mapped for every provider.
    claim_mappings = config["claim_mappings"]
    assert isinstance(claim_mappings, dict)
    assert FIELD_EMAIL in claim_mappings
    assert FIELD_FULL_NAME in claim_mappings


@pytest.mark.parametrize("template", list_templates(), ids=lambda t: t.template_id.value)
def test_config_feeds_the_generic_oidc_flow(template: OIDCTemplate) -> None:
    """A template-built config slots into ResolvedOIDCConfig and the
    generic flow's claim mapper resolves email + name from it — proving
    the template output is wire-compatible with task_08_01's flow."""
    config = template.build_config(_params_for(template))
    resolved = ResolvedOIDCConfig(
        issuer=str(config["issuer"]),
        client_id="acme-client",
        client_secret="secret",
        scopes=list(config["scopes"]),  # type: ignore[arg-type]
        claim_mappings=dict(config["claim_mappings"]),  # type: ignore[arg-type]
    )
    # openid survives the flow's own scope normalization.
    assert OPENID_SCOPE in resolved.scope_string().split()

    flow = OIDCFlow(http_client=None)  # type: ignore[arg-type]  # no I/O in _map_claims
    userinfo = flow._map_claims(resolved, _sample_payload(template))
    assert userinfo.email == _SAMPLE_EMAIL.lower()
    assert userinfo.full_name == _SAMPLE_NAME
    assert userinfo.subject == "idp-subject-123"


# ---------------------------------------------------------------------------
# Claim extraction (email / name / groups) per provider
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("template", list_templates(), ids=lambda t: t.template_id.value)
def test_extract_claims_from_representative_payload(template: OIDCTemplate) -> None:
    extracted = extract_claims(template, _sample_payload(template))

    assert extracted.email == _SAMPLE_EMAIL
    assert extracted.full_name == _SAMPLE_NAME

    if FIELD_GROUPS in template.claim_mappings:
        assert extracted.groups == _SAMPLE_GROUPS
    else:
        # Providers without a groups concept (Apple/Facebook/Google/GitHub)
        # never surface groups, even if the payload happens to carry some.
        assert extracted.groups == []


def test_groups_template_extracts_groups() -> None:
    """A provider that maps groups (Azure AD) returns them as a list."""
    template = get_template(OIDCTemplateId.AZURE_AD)
    extracted = extract_claims(
        template,
        {"email": "a@b.test", "name": "A B", "groups": ["g1", "g2"]},
    )
    assert extracted.groups == ["g1", "g2"]


def test_groupless_template_ignores_present_groups() -> None:
    """Google has no groups mapping → groups claim in the payload is ignored."""
    template = get_template(OIDCTemplateId.GOOGLE_WORKSPACE)
    extracted = extract_claims(
        template,
        {"email": "a@b.test", "name": "A B", "groups": ["should", "be", "ignored"]},
    )
    assert extracted.groups == []


def test_extract_claims_coerces_single_group_string_to_list() -> None:
    template = get_template(OIDCTemplateId.OKTA)
    extracted = extract_claims(template, {"email": "a@b.test", "name": "A B", "groups": "lonely"})
    assert extracted.groups == ["lonely"]


def test_extract_claims_missing_email_is_none() -> None:
    template = get_template(OIDCTemplateId.GOOGLE_WORKSPACE)
    extracted = extract_claims(template, {"name": "No Email"})
    assert extracted.email is None
    assert extracted.full_name == "No Email"


def test_extract_claims_drops_non_string_group_members() -> None:
    template = get_template(OIDCTemplateId.GITLAB)
    extracted = extract_claims(
        template,
        {"email": "a@b.test", "name": "A B", "groups": ["ok", 123, None, "also-ok"]},
    )
    assert extracted.groups == ["ok", "also-ok"]


# ---------------------------------------------------------------------------
# Issuer rendering + required params
# ---------------------------------------------------------------------------
def test_azure_issuer_interpolates_tenant() -> None:
    template = get_template(OIDCTemplateId.AZURE_AD)
    issuer = template.build_issuer({"tenant": "my-tenant-guid"})
    assert issuer == "https://login.microsoftonline.com/my-tenant-guid/v2.0"


def test_okta_issuer_interpolates_domain() -> None:
    template = get_template(OIDCTemplateId.OKTA)
    issuer = template.build_issuer({"domain": "dev-99.okta.com"})
    assert issuer == "https://dev-99.okta.com/oauth2/default"


def test_gitlab_self_managed_base_url() -> None:
    template = get_template(OIDCTemplateId.GITLAB)
    issuer = template.build_issuer({"base_url": "https://gitlab.acme.internal"})
    assert issuer == "https://gitlab.acme.internal"


def test_parameterless_template_needs_no_params() -> None:
    """Google has no placeholders — build_config with no params works."""
    template = get_template(OIDCTemplateId.GOOGLE_WORKSPACE)
    assert template.build_config()["issuer"] == "https://accounts.google.com"


@pytest.mark.parametrize(
    ("template_id", "params"),
    [
        (OIDCTemplateId.AZURE_AD, {}),
        (OIDCTemplateId.AZURE_AD, {"tenant": ""}),
        (OIDCTemplateId.OKTA, {}),
        (OIDCTemplateId.AUTH0, {"wrong": "x"}),
        (OIDCTemplateId.GITLAB, {}),
    ],
)
def test_missing_required_param_raises(template_id: OIDCTemplateId, params: dict[str, str]) -> None:
    template = get_template(template_id)
    with pytest.raises(OIDCTemplateError):
        template.build_config(params)


# ---------------------------------------------------------------------------
# Unknown template -> error
# ---------------------------------------------------------------------------
def test_unknown_template_id_raises() -> None:
    with pytest.raises(OIDCTemplateError) as exc:
        get_template("totally-not-a-provider")
    # Error lists the valid templates to help the operator.
    assert "azure_ad" in str(exc.value)


def test_get_template_accepts_enum_and_string() -> None:
    by_enum = get_template(OIDCTemplateId.OKTA)
    by_string = get_template("okta")
    assert by_enum is by_string
