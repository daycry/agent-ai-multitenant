"""Integration tests for IdP group → tenant role mapping (Plan 08 task_08_11).

On every SSO login (OIDC + SAML) the IdP may assert the user's groups
(the OIDC ``groups`` claim / a SAML ``groups`` attribute). A tenant's
``sso_configurations.group_role_mappings`` (``{group: role}``) maps those
onto a tenant role; the user's membership role is (re)set to the
highest-privilege role any asserted group maps to.

Coverage:

  * a user whose IdP groups map to ``tenant_admin`` gets that role on
    first login (OIDC) and on SAML login;
  * unmapped groups (or no mapping at all) keep the JIT default
    ``tenant_user``;
  * highest-privilege wins when a user is in several mapped groups;
  * ``system_admin`` / ``system_operator`` are NEVER grantable via a
    group mapping — even when an operator (or a forged claim) puts that
    string in the mapping, the entry is ignored and the user stays at the
    safe role (and never becomes a system admin);
  * with a mapping configured, the role is re-synced on every login (a
    revoked IdP group demotes the user on the next login); a tenant with
    NO mapping keeps the legacy "JIT default, admin promotes manually"
    behaviour (a manual ``tenant_admin`` is not clobbered);
  * the mapping is strictly per-tenant (@pytest.mark.cross_tenant): the
    same IdP group resolves to different roles under each tenant's config,
    and one tenant's mapping never affects another.

Plus fast, pure-logic unit tests of ``resolve_role_from_groups`` (no DB)
covering the same invariants — including the system-role guard.

No real IdP: a :class:`httpx.MockTransport` serves a fake OpenID Provider
(mirrors ``test_jit_provisioning.py``) whose ``groups`` claim is
configurable per test; the SAML path builds + signs an assertion offline
(mirrors ``test_saml.py``).

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the fixtures create a throwaway DB and flush Redis DB 15.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from alembic import command
from api_server.auth.sso.group_mapping import (
    DEFAULT_TENANT_ROLE,
    is_grantable_role,
    resolve_role_from_groups,
)
from api_server.auth.sso.secrets import encrypt_client_secret
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from httpx import ASGITransport, AsyncClient
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey

# python3-saml pulls in the native xmlsec backend. It is present on the dev
# host and in CI (installed from the manylinux wheel), but guard collection so
# a runner without the native libs SKIPS this module cleanly instead of failing
# at import time (keeps CI green regardless of xmlsec availability).
OneLogin_Saml2_Utils = pytest.importorskip(
    "onelogin.saml2.utils",
    reason="python3-saml/xmlsec native backend not installed",
).OneLogin_Saml2_Utils

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fake OIDC IdP — a single identity whose `groups` claim is configurable.
# ---------------------------------------------------------------------------
_ISSUER = "https://idp.example.test"
_CLIENT_ID = "acme-oidc-client"
_CLIENT_SECRET = "super-secret-oidc-value"
_AUTHZ = f"{_ISSUER}/authorize"
_TOKEN = f"{_ISSUER}/token"
_USERINFO = f"{_ISSUER}/userinfo"
_JWKS = f"{_ISSUER}/jwks"
_KID = "test-key-1"
_IDP_EMAIL = "Worker@Acme.example.com"
_NORMALIZED_EMAIL = "worker@acme.example.com"

_SIGNING_KEY = RSAKey.generate_key(2048, parameters={"kid": _KID}, private=True)


def _id_token(*, nonce: str, groups: list[str]) -> str:
    header = {"alg": "RS256", "kid": _KID}
    claims = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "sub": "idp-subject-123",
        "nonce": nonce,
        "email": _IDP_EMAIL,
        "name": "Worker Person",
        "groups": groups,
    }
    return joserfc_jwt.encode(header, claims, _SIGNING_KEY)


class _FakeIdP:
    """Stateful mock OpenID Provider with a configurable groups claim."""

    def __init__(self) -> None:
        self.last_nonce: str | None = None
        self.groups: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == _ISSUER + "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": _ISSUER,
                    "authorization_endpoint": _AUTHZ,
                    "token_endpoint": _TOKEN,
                    "userinfo_endpoint": _USERINFO,
                    "jwks_uri": _JWKS,
                },
            )
        if url == _AUTHZ:  # pragma: no cover - flow only builds the URL
            self.last_nonce = dict(request.url.params).get("nonce")
            return httpx.Response(302, headers={"location": "ignored"})
        if url == _JWKS:
            return httpx.Response(200, json={"keys": [_SIGNING_KEY.as_dict(private=False)]})
        if url == _TOKEN:
            nonce = self.last_nonce or "missing-nonce"
            return httpx.Response(
                200,
                json={
                    "access_token": "fake-access-token",
                    "token_type": "Bearer",
                    "id_token": _id_token(nonce=nonce, groups=self.groups),
                },
            )
        if url == _USERINFO:
            return httpx.Response(
                200,
                json={
                    "sub": "idp-subject-123",
                    "email": _IDP_EMAIL,
                    "name": "Worker Person",
                    "groups": self.groups,
                },
            )
        return httpx.Response(404, json={"error": "not_found"})  # pragma: no cover


# ---------------------------------------------------------------------------
# Fake SAML IdP — self-signed signing key/cert + a signed-assertion builder.
# ---------------------------------------------------------------------------
_SAML_IDP_ENTITY_ID = "https://saml-idp.example.test/metadata"
_SAML_IDP_SSO_URL = "https://saml-idp.example.test/sso"
_SP_ENTITY_ID = "http://testserver/auth/sso/saml/metadata"
_SAML_NAME_ID = "Worker@Acme.test"
_SAML_NORMALIZED_EMAIL = "worker@acme.test"


def _gen_key_and_cert() -> tuple[str, str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "saml-idp.test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(tz=UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(tz=UTC) + timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    body = (
        cert_pem.replace("-----BEGIN CERTIFICATE-----", "")
        .replace("-----END CERTIFICATE-----", "")
        .replace("\n", "")
        .strip()
    )
    return key_pem, cert_pem, body


_SAML_KEY_PEM, _SAML_CERT_PEM, _SAML_CERT_BODY = _gen_key_and_cert()


def _saml_time(delta_minutes: int = 0) -> str:
    return (datetime.now(tz=UTC) + timedelta(minutes=delta_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_signed_response(*, tenant_id: UUID, groups: list[str]) -> str:
    """Build a SAML Response with a signed assertion carrying a `groups`
    multi-valued attribute, base64-encoded."""
    acs_url = f"http://testserver/auth/sso/{tenant_id}/saml/acs"
    response_id = "_" + uuid4().hex
    assertion_id = "_" + uuid4().hex
    not_before = _saml_time(-5)
    not_on_or_after = _saml_time(5)
    group_values = "".join(
        f'<saml:AttributeValue xs:type="xs:string">{g}</saml:AttributeValue>' for g in groups
    )
    assertion = f"""<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" \
xmlns:xs="http://www.w3.org/2001/XMLSchema" \
ID="{assertion_id}" Version="2.0" IssueInstant="{_saml_time()}">
  <saml:Issuer>{_SAML_IDP_ENTITY_ID}</saml:Issuer>
  <saml:Subject>
    <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">\
{_SAML_NAME_ID}</saml:NameID>
    <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
      <saml:SubjectConfirmationData NotOnOrAfter="{not_on_or_after}" \
Recipient="{acs_url}"/>
    </saml:SubjectConfirmation>
  </saml:Subject>
  <saml:Conditions NotBefore="{not_before}" NotOnOrAfter="{not_on_or_after}">
    <saml:AudienceRestriction>
      <saml:Audience>{_SP_ENTITY_ID}</saml:Audience>
    </saml:AudienceRestriction>
  </saml:Conditions>
  <saml:AuthnStatement AuthnInstant="{_saml_time()}" SessionIndex="{assertion_id}">
    <saml:AuthnContext>
      <saml:AuthnContextClassRef>\
urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport\
</saml:AuthnContextClassRef>
    </saml:AuthnContext>
  </saml:AuthnStatement>
  <saml:AttributeStatement>
    <saml:Attribute Name="displayName" \
NameFormat="urn:oasis:names:tc:SAML:2.0:attrname-format:basic">
      <saml:AttributeValue xs:type="xs:string">Worker Person</saml:AttributeValue>
    </saml:Attribute>
    <saml:Attribute Name="groups" \
NameFormat="urn:oasis:names:tc:SAML:2.0:attrname-format:basic">
      {group_values}
    </saml:Attribute>
  </saml:AttributeStatement>
</saml:Assertion>"""

    signed_assertion = OneLogin_Saml2_Utils.add_sign(
        assertion,
        _SAML_KEY_PEM,
        _SAML_CERT_PEM,
        sign_algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
        digest_algorithm="http://www.w3.org/2001/04/xmlenc#sha256",
    )
    signed_assertion_str = (
        signed_assertion if isinstance(signed_assertion, str) else signed_assertion.decode("utf-8")
    )
    if signed_assertion_str.startswith("<?xml"):
        signed_assertion_str = signed_assertion_str.split("?>", 1)[1].lstrip()

    response = f"""<samlp:Response \
xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" \
xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" \
ID="{response_id}" Version="2.0" IssueInstant="{_saml_time()}" \
Destination="{acs_url}">
  <saml:Issuer>{_SAML_IDP_ENTITY_ID}</saml:Issuer>
  <samlp:Status>
    <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
  </samlp:Status>
  {signed_assertion_str}
</samlp:Response>"""

    return base64.b64encode(response.encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------
# DB seed + inspection helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _seed_tenant(dsn: str, *, slug: str) -> UUID:
    tenant = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant,
            slug.title(),
            slug,
        )
    finally:
        await conn.close()
    return tenant


async def _seed_oidc_config(
    dsn: str, *, tenant_id: UUID, group_role_mappings: dict[str, str]
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, tenant_id, provider, display_name, enabled, issuer,
                 client_id, client_secret_encrypted, scopes, claim_mappings,
                 group_role_mappings)
            VALUES ($1, $2, 'oidc', 'Acme OIDC', true, $3, $4, $5,
                    $6::jsonb, $7::jsonb, $8::jsonb)
            """,
            uuid4(),
            tenant_id,
            _ISSUER,
            _CLIENT_ID,
            encrypt_client_secret(_CLIENT_SECRET),
            json.dumps(["openid", "email", "profile"]),
            json.dumps({}),
            json.dumps(group_role_mappings),
        )
    finally:
        await conn.close()


async def _seed_saml_config(
    dsn: str, *, tenant_id: UUID, group_role_mappings: dict[str, str]
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, tenant_id, provider, display_name, enabled,
                 idp_entity_id, idp_sso_url, idp_x509_cert,
                 name_id_format, attribute_mappings, group_role_mappings)
            VALUES ($1, $2, 'saml', 'Acme SAML', true, $3, $4, $5, $6,
                    $7::jsonb, $8::jsonb)
            """,
            uuid4(),
            tenant_id,
            _SAML_IDP_ENTITY_ID,
            _SAML_IDP_SSO_URL,
            _SAML_CERT_BODY,
            "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            json.dumps({}),
            json.dumps(group_role_mappings),
        )
    finally:
        await conn.close()


async def _membership_role(dsn: str, *, tenant_id: UUID, email: str) -> str | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            """
            SELECT m.role
              FROM user_org_memberships m
              JOIN users u ON u.id = m.user_id
             WHERE m.tenant_id = $1 AND u.email = $2
            """,
            tenant_id,
            email,
        )
    finally:
        await conn.close()


async def _user_is_system_admin(dsn: str, *, email: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT is_system_admin FROM users WHERE email = $1", email)
    finally:
        await conn.close()


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE sso_configurations, user_org_memberships, organizations, users "
            "RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# App fixture: real api-server with the mocked IdP injected into the flow
# ---------------------------------------------------------------------------
@pytest.fixture()
def idp() -> _FakeIdP:
    return _FakeIdP()


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    idp: _FakeIdP,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")

    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")
    monkeypatch.setenv("API_SERVER_SSO_ENCRYPTION_KEY", "test-sso-encryption-key")
    monkeypatch.setenv("API_SERVER_SSO_REDIRECT_BASE_URL", "http://testserver")
    monkeypatch.delenv("API_SERVER_VAULT_TOKEN", raising=False)

    from api_server.auth.deps import reset_redis_cache
    from api_server.auth.sso.oidc import OIDCFlow
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache
    from api_server.routers import mcp as mcp_router
    from api_server.routers.sso import get_oidc_http_client

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()
    OIDCFlow.reset_discovery_cache()
    mcp_router.reset_vault_resolver_cache()

    from api_server.main import create_app

    app = create_app()

    def _mock_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(idp.handler))

    app.dependency_overrides[get_oidc_http_client] = _mock_client

    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        OIDCFlow.reset_discovery_cache()
        get_settings.cache_clear()


async def _oidc_login(client: AsyncClient, tenant_id: UUID, idp: _FakeIdP) -> httpx.Response:
    resp = await client.get(f"/auth/sso/{tenant_id}/oidc/login")
    assert resp.status_code == 307, resp.text
    params = dict(httpx.URL(resp.headers["location"]).params)
    idp.last_nonce = params["nonce"]
    return await client.get(
        "/auth/sso/oidc/callback",
        params={"code": "fake-auth-code", "state": params["state"]},
    )


# ===========================================================================
# Pure-logic unit tests of the resolver (no DB) — fast invariant coverage.
# ===========================================================================
def test_resolver_maps_group_to_admin() -> None:
    role = resolve_role_from_groups(["platform-admins"], {"platform-admins": "tenant_admin"})
    assert role == "tenant_admin"


def test_resolver_unmapped_groups_keep_default() -> None:
    assert resolve_role_from_groups(["random-group"], {"platform-admins": "tenant_admin"}) == (
        DEFAULT_TENANT_ROLE
    )
    # No groups / no mapping → default.
    assert resolve_role_from_groups([], {}) == DEFAULT_TENANT_ROLE


def test_resolver_highest_privilege_wins() -> None:
    mapping = {"staff": "tenant_user", "admins": "tenant_admin"}
    # Order in the asserted-groups list must not matter.
    assert resolve_role_from_groups(["staff", "admins"], mapping) == "tenant_admin"
    assert resolve_role_from_groups(["admins", "staff"], mapping) == "tenant_admin"


def test_resolver_never_grants_system_role() -> None:
    # A misconfigured / forged mapping pointing a group at a platform role
    # is ignored — the user stays at the default, never a system role.
    assert not is_grantable_role("system_admin")
    assert not is_grantable_role("system_operator")
    assert resolve_role_from_groups(["evil"], {"evil": "system_admin"}) == DEFAULT_TENANT_ROLE
    assert resolve_role_from_groups(["ops"], {"ops": "system_operator"}) == DEFAULT_TENANT_ROLE
    # Even mixed with a legit grant, the system entry is simply skipped and
    # the legit grant still applies.
    assert (
        resolve_role_from_groups(
            ["evil", "admins"], {"evil": "system_admin", "admins": "tenant_admin"}
        )
        == "tenant_admin"
    )


def test_resolver_non_string_group_values_are_ignored() -> None:
    # The OIDC/SAML extractors already coerce to list[str]; the resolver is
    # also robust to an empty list.
    assert resolve_role_from_groups([], {"x": "tenant_admin"}) == DEFAULT_TENANT_ROLE


# ===========================================================================
# OIDC end-to-end: group → role on login
# ===========================================================================
@pytest.mark.asyncio
async def test_oidc_group_maps_to_tenant_admin(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(
        migrations_pg_dsn, tenant_id=tenant, group_role_mappings={"platform-admins": "tenant_admin"}
    )
    idp.groups = ["platform-admins", "engineering"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await _oidc_login(client, tenant, idp)
        assert resp.status_code == 200, resp.text

    assert (
        await _membership_role(migrations_pg_dsn, tenant_id=tenant, email=_NORMALIZED_EMAIL)
        == "tenant_admin"
    )


@pytest.mark.asyncio
async def test_oidc_unmapped_groups_keep_tenant_user(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(
        migrations_pg_dsn, tenant_id=tenant, group_role_mappings={"platform-admins": "tenant_admin"}
    )
    # The user is NOT in the mapped group.
    idp.groups = ["engineering", "interns"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await _oidc_login(client, tenant, idp)
        assert resp.status_code == 200, resp.text

    assert (
        await _membership_role(migrations_pg_dsn, tenant_id=tenant, email=_NORMALIZED_EMAIL)
        == "tenant_user"
    )


@pytest.mark.asyncio
async def test_oidc_no_mapping_keeps_default(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    """A tenant that never configured a mapping keeps the JIT default even
    when the IdP asserts groups."""
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant, group_role_mappings={})
    idp.groups = ["platform-admins"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await _oidc_login(client, tenant, idp)
        assert resp.status_code == 200, resp.text

    assert (
        await _membership_role(migrations_pg_dsn, tenant_id=tenant, email=_NORMALIZED_EMAIL)
        == "tenant_user"
    )


@pytest.mark.asyncio
async def test_oidc_system_admin_never_granted_via_group(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    """A mapping that points a group at ``system_admin`` is ignored: the
    user stays at the safe default AND never becomes a system admin."""
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    # Note: this row is seeded directly (bypassing the API validator) to
    # prove the LOGIN path itself refuses to honour a system role even if
    # one somehow lands in the column.
    await _seed_oidc_config(
        migrations_pg_dsn, tenant_id=tenant, group_role_mappings={"superusers": "system_admin"}
    )
    idp.groups = ["superusers"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await _oidc_login(client, tenant, idp)
        assert resp.status_code == 200, resp.text

    assert (
        await _membership_role(migrations_pg_dsn, tenant_id=tenant, email=_NORMALIZED_EMAIL)
        == "tenant_user"
    )
    assert await _user_is_system_admin(migrations_pg_dsn, email=_NORMALIZED_EMAIL) is False


@pytest.mark.asyncio
async def test_oidc_role_resyncs_on_revoked_group(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    """With a mapping configured the IdP is authoritative: a first login in
    the admin group grants admin; a later login WITHOUT that group demotes
    the user back to the default on the next login."""
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(
        migrations_pg_dsn, tenant_id=tenant, group_role_mappings={"platform-admins": "tenant_admin"}
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        idp.groups = ["platform-admins"]
        first = await _oidc_login(client, tenant, idp)
        assert first.status_code == 200, first.text
        assert (
            await _membership_role(migrations_pg_dsn, tenant_id=tenant, email=_NORMALIZED_EMAIL)
            == "tenant_admin"
        )

        # IdP revokes the admin group; next login re-syncs down.
        idp.groups = ["engineering"]
        second = await _oidc_login(client, tenant, idp)
        assert second.status_code == 200, second.text

    assert (
        await _membership_role(migrations_pg_dsn, tenant_id=tenant, email=_NORMALIZED_EMAIL)
        == "tenant_user"
    )


# ===========================================================================
# Cross-tenant: the mapping is strictly per-tenant
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_group_mapping_is_per_tenant(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    """The SAME IdP group resolves to DIFFERENT roles under each tenant's
    own config — tenant A maps it to admin, tenant B does not map it. Each
    tenant's membership reflects only its own mapping."""
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    # Tenant A maps the group to admin; tenant B maps a DIFFERENT group, so
    # for B the same asserted group is unmapped → default.
    await _seed_oidc_config(
        migrations_pg_dsn, tenant_id=tenant_a, group_role_mappings={"staff": "tenant_admin"}
    )
    await _seed_oidc_config(
        migrations_pg_dsn, tenant_id=tenant_b, group_role_mappings={"other-group": "tenant_admin"}
    )
    idp.groups = ["staff"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        a_resp = await _oidc_login(client, tenant_a, idp)
        assert a_resp.status_code == 200, a_resp.text
        b_resp = await _oidc_login(client, tenant_b, idp)
        assert b_resp.status_code == 200, b_resp.text

    # Same user (linked by email) but a per-tenant membership + role each.
    assert (
        await _membership_role(migrations_pg_dsn, tenant_id=tenant_a, email=_NORMALIZED_EMAIL)
        == "tenant_admin"
    )
    assert (
        await _membership_role(migrations_pg_dsn, tenant_id=tenant_b, email=_NORMALIZED_EMAIL)
        == "tenant_user"
    )


# ===========================================================================
# SAML end-to-end: groups attribute → role on login
# ===========================================================================
@pytest.mark.asyncio
async def test_saml_group_maps_to_tenant_admin(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_saml_config(
        migrations_pg_dsn, tenant_id=tenant, group_role_mappings={"platform-admins": "tenant_admin"}
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        saml_response = _build_signed_response(
            tenant_id=tenant, groups=["platform-admins", "engineering"]
        )
        resp = await client.post(
            f"/auth/sso/{tenant}/saml/acs",
            data={"SAMLResponse": saml_response},
        )
        assert resp.status_code == 200, resp.text

    assert (
        await _membership_role(migrations_pg_dsn, tenant_id=tenant, email=_SAML_NORMALIZED_EMAIL)
        == "tenant_admin"
    )


@pytest.mark.asyncio
async def test_saml_unmapped_group_keeps_tenant_user(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_saml_config(
        migrations_pg_dsn, tenant_id=tenant, group_role_mappings={"platform-admins": "tenant_admin"}
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        saml_response = _build_signed_response(tenant_id=tenant, groups=["engineering"])
        resp = await client.post(
            f"/auth/sso/{tenant}/saml/acs",
            data={"SAMLResponse": saml_response},
        )
        assert resp.status_code == 200, resp.text

    assert (
        await _membership_role(migrations_pg_dsn, tenant_id=tenant, email=_SAML_NORMALIZED_EMAIL)
        == "tenant_user"
    )
