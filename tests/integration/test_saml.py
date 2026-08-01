"""Integration tests for the GLOBAL SAML 2.0 SSO flow (ADR 0047).

Auth providers are platform-global now (ADR 0047, supersedes the
per-tenant part of ADR 0031): SAML login is addressed by the global
**provider id** (``GET /auth/sso/{provider_id}/saml/login``) and the ACS
is the single GLOBAL ``POST /auth/sso/saml/acs`` (one SP identity for the
whole platform). The issued session proves IDENTITY only — a freshly
provisioned global user has NO tenant and NO membership (access is granted
by admin-assigned membership AFTER login — task_sso_03).

No real IdP: a self-signed in-test RSA key pair plays the IdP. The test
builds a SAML ``Response`` XML, signs the assertion with the IdP private
key via ``python3-saml``'s own ``add_sign`` (the exact mechanism a real
IdP uses), base64-encodes it, and POSTs it to the api-server's GLOBAL
ACS — so the whole SP-side validation (signature against the IdP cert,
audience, time conditions, NameID/attribute extraction) runs end-to-end
offline.

Coverage:

  * SP-initiated: ``/{provider_id}/saml/login`` 302-redirects to the IdP
    SSO URL with a ``SAMLRequest`` + ``RelayState``.
  * ACS happy path → JIT-creates the global user, mints a live Redis
    session + a JWT that ``get_principal`` accepts — with NO active tenant
    and NO membership (ADR 0047).
  * ACS for an EXISTING user → looked up, not duplicated.
  * IdP-initiated (unsolicited, no RelayState) → also succeeds against the
    single enabled global SAML config.
  * tampered/garbage/unsigned assertion → 400.
  * disabled / unknown provider → login 404; ACS with no global config →
    400.
  * import-guard: when ``python3-saml`` is unavailable, both endpoints
    return 501.
  * @pytest.mark.cross_tenant: a RelayState carries the PROVIDER that
    started the flow; a RelayState minted for provider A cannot be steered
    onto a different PROVIDER B (the analogue of the old per-tenant guard).

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the session fixtures create a throwaway DB and flush Redis 15.
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
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from httpx import ASGITransport, AsyncClient

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
# Fake IdP identity + a self-signed signing key/cert
# ---------------------------------------------------------------------------
_IDP_ENTITY_ID = "https://saml-idp.example.test/metadata"
_IDP_SSO_URL = "https://saml-idp.example.test/sso"
# Must match what the router computes from API_SERVER_SSO_REDIRECT_BASE_URL.
_SP_ENTITY_ID = "http://testserver/auth/sso/saml/metadata"
# GLOBAL ACS (ADR 0047) — one SP identity for the whole platform; no tenant.
_SP_ACS_URL = "http://testserver/auth/sso/saml/acs"
_NAME_ID = "Worker@Acme.test"  # mixed case -> flow lowercases it


def _gen_key_and_cert() -> tuple[str, str, str]:
    """Return (private_key_pem, cert_pem, cert_body) for the fake IdP."""
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


_IDP_KEY_PEM, _IDP_CERT_PEM, _IDP_CERT_BODY = _gen_key_and_cert()


def _saml_time(delta_minutes: int = 0) -> str:
    return (datetime.now(tz=UTC) + timedelta(minutes=delta_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_signed_response(*, name_id: str = _NAME_ID) -> str:
    """Build a SAML Response with a signed assertion, base64-encoded.

    Targets the GLOBAL ACS (ADR 0047): the Recipient + Destination are the
    single platform ACS URL, no tenant in the path. The assertion is signed
    with the IdP private key using ``python3-saml``'s own ``add_sign`` —
    exactly what a real IdP does — so the SP-side signature verification
    (against the IdP cert in the global config) passes.
    """
    acs_url = _SP_ACS_URL
    response_id = "_" + uuid4().hex
    assertion_id = "_" + uuid4().hex
    not_before = _saml_time(-5)
    not_on_or_after = _saml_time(5)

    # Sign the ASSERTION standalone first: add_sign finds the first
    # //saml:Issuer and signs its parent, so on a standalone assertion it
    # signs the assertion (which is what `wantAssertionsSigned` requires).
    assertion = f"""<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" \
xmlns:xs="http://www.w3.org/2001/XMLSchema" \
ID="{assertion_id}" Version="2.0" IssueInstant="{_saml_time()}">
  <saml:Issuer>{_IDP_ENTITY_ID}</saml:Issuer>
  <saml:Subject>
    <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">\
{name_id}</saml:NameID>
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
  </saml:AttributeStatement>
</saml:Assertion>"""

    signed_assertion = OneLogin_Saml2_Utils.add_sign(
        assertion,
        _IDP_KEY_PEM,
        _IDP_CERT_PEM,
        sign_algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
        digest_algorithm="http://www.w3.org/2001/04/xmlenc#sha256",
    )
    signed_assertion_str = (
        signed_assertion if isinstance(signed_assertion, str) else signed_assertion.decode("utf-8")
    )
    # Strip the XML declaration add_sign may prepend so it nests cleanly.
    if signed_assertion_str.startswith("<?xml"):
        signed_assertion_str = signed_assertion_str.split("?>", 1)[1].lstrip()

    response = f"""<samlp:Response \
xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" \
xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" \
ID="{response_id}" Version="2.0" IssueInstant="{_saml_time()}" \
Destination="{acs_url}">
  <saml:Issuer>{_IDP_ENTITY_ID}</saml:Issuer>
  <samlp:Status>
    <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
  </samlp:Status>
  {signed_assertion_str}
</samlp:Response>"""

    return base64.b64encode(response.encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------
# DB seed helpers — the table is GLOBAL now (no tenant_id column).
# ---------------------------------------------------------------------------
async def _seed_global_saml(dsn: str, *, enabled: bool = True) -> UUID:
    """Insert the single global SAML config. Returns its provider id."""
    config_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, provider, display_name, enabled,
                 idp_entity_id, idp_sso_url, idp_x509_cert,
                 name_id_format, attribute_mappings)
            VALUES ($1, 'saml', 'Acme SAML', $2, $3, $4, $5, $6, $7::jsonb)
            """,
            config_id,
            enabled,
            _IDP_ENTITY_ID,
            _IDP_SSO_URL,
            _IDP_CERT_BODY,
            "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            json.dumps({}),
        )
    finally:
        await conn.close()
    return config_id


async def _count_users_with_email(dsn: str, email: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT count(*) FROM users WHERE email = $1", email)
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
# App fixture (mirrors the OIDC harness; no IdP HTTP client needed for SAML)
# ---------------------------------------------------------------------------
@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
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
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _login_relay_state(client: AsyncClient, provider_id: UUID) -> str:
    """Hit /{provider_id}/saml/login and return the RelayState the IdP echoes."""
    resp = await client.get(f"/auth/sso/{provider_id}/saml/login")
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    params = dict(httpx.URL(location).params)
    return params["RelayState"]


# ---------------------------------------------------------------------------
# SP-initiated login redirect
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_login_redirects_to_idp(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_saml(migrations_pg_dsn)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(f"/auth/sso/{provider_id}/saml/login")
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    assert location.startswith(_IDP_SSO_URL)
    params = dict(httpx.URL(location).params)
    assert "SAMLRequest" in params
    assert "RelayState" in params


# ---------------------------------------------------------------------------
# ACS happy path — SP-initiated, JIT provisioning of the GLOBAL identity
# (BLOCKED-ON-XMLSEC: this whole assertion-validation path needs the native
# xmlsec backend; it is present in the Docker/CI image and on this dev host)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_acs_creates_user_session_and_jwt(configured_app, migrations_pg_dsn: str) -> None:
    """ACS mints an IDENTITY session: the JIT user has NO active tenant and
    NO membership (ADR 0047 — access is granted by admin assignment)."""
    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_saml(migrations_pg_dsn)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        relay_state = await _login_relay_state(client, provider_id)
        saml_response = _build_signed_response()

        resp = await client.post(
            "/auth/sso/saml/acs",
            data={"SAMLResponse": saml_response, "RelayState": relay_state},
            follow_redirects=False,
        )
        # 303 + Set-Cookie desde el ADR 0133: el ACS ya no contesta el
        # `LoginResponse` JSON, deja la sesión en cookie y bota al panel. El
        # 303 (y no 302/307) es lo que hace que el navegador pase a GET tras
        # este POST. El contrato del salto vive en test_sso_callback_redirect.py.
        assert resp.status_code == 303, resp.text
        token = client.cookies.get("agentic_session")
        assert token

        me = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        me_body = me.json()
        assert me_body["email"] == "worker@acme.test"
        # Identity session: no active tenant, no membership (ADR 0047).
        assert me_body["active_tenant_id"] is None
        assert me_body["memberships"] == []

    assert await _count_users_with_email(migrations_pg_dsn, "worker@acme.test") == 1


@pytest.mark.asyncio
async def test_second_acs_reuses_existing_user(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_saml(migrations_pg_dsn)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        for _ in range(2):
            relay_state = await _login_relay_state(client, provider_id)
            saml_response = _build_signed_response()
            resp = await client.post(
                "/auth/sso/saml/acs",
                data={"SAMLResponse": saml_response, "RelayState": relay_state},
                follow_redirects=False,
            )
            assert resp.status_code == 303, resp.text

    assert await _count_users_with_email(migrations_pg_dsn, "worker@acme.test") == 1


# ---------------------------------------------------------------------------
# IdP-initiated (unsolicited) — no RelayState we minted; resolves to the
# single enabled global SAML config.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_acs_idp_initiated_no_relay_state(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    await _seed_global_saml(migrations_pg_dsn)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        saml_response = _build_signed_response()
        # No RelayState at all — pure IdP-initiated.
        resp = await client.post(
            "/auth/sso/saml/acs",
            data={"SAMLResponse": saml_response},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text
        assert client.cookies.get("agentic_session")


# ---------------------------------------------------------------------------
# Tampered / invalid assertion
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_acs_tampered_response_is_400(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    await _seed_global_saml(migrations_pg_dsn)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        saml_response = _build_signed_response()
        # Flip a chunk of the base64 to break the signature.
        raw = base64.b64decode(saml_response).decode("utf-8")
        tampered = raw.replace("Worker Person", "Attacker Person")
        tampered_b64 = base64.b64encode(tampered.encode("utf-8")).decode("ascii")
        resp = await client.post(
            "/auth/sso/saml/acs",
            data={"SAMLResponse": tampered_b64},
        )
    assert resp.status_code == 400, resp.text
    assert "saml authentication failed" in resp.text.lower()


@pytest.mark.asyncio
async def test_acs_garbage_response_is_400(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    await _seed_global_saml(migrations_pg_dsn)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.post(
            "/auth/sso/saml/acs",
            data={"SAMLResponse": base64.b64encode(b"<not-saml/>").decode("ascii")},
        )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# disabled / unknown provider; missing global config
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_login_disabled_config_is_404(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_saml(migrations_pg_dsn, enabled=False)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(f"/auth/sso/{provider_id}/saml/login")
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_login_unknown_provider_is_404(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    await _seed_global_saml(migrations_pg_dsn)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(f"/auth/sso/{uuid4()}/saml/login")
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_acs_missing_config_is_400(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)  # no SAML config at all

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.post(
            "/auth/sso/saml/acs",
            data={"SAMLResponse": _build_signed_response()},
        )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# Import guard — SAML unavailable returns 501
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_login_returns_501_when_saml_unavailable(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When python3-saml/xmlsec is absent, the flow must NOT crash — it
    reports SAML unavailable (501). We simulate the missing native dep by
    forcing the import-guard to report unavailable."""
    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_saml(migrations_pg_dsn)

    import api_server.routers.sso as sso_router

    monkeypatch.setattr(sso_router, "saml_available", lambda: False)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        login = await client.get(f"/auth/sso/{provider_id}/saml/login")
        acs = await client.post(
            "/auth/sso/saml/acs",
            data={"SAMLResponse": "irrelevant"},
        )
    assert login.status_code == 501, login.text
    assert acs.status_code == 501, acs.text


def test_saml_unavailable_error_from_import_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lazy import guard raises a typed SAMLUnavailableError when the
    native package is missing — the property the 501 path relies on."""
    import builtins

    import api_server.auth.sso.saml as saml_mod

    real_import = builtins.__import__

    def _no_onelogin(name, *args, **kwargs):
        if name.startswith("onelogin"):
            raise ImportError("simulated: python3-saml not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_onelogin)
    assert saml_mod.saml_available() is False
    with pytest.raises(saml_mod.SAMLUnavailableError):
        saml_mod.build_login_url(
            saml_mod.ResolvedSAMLConfig(
                idp_entity_id="x",
                idp_sso_url="https://x/sso",
                idp_x509_cert="cert",
                sp_entity_id="http://testserver/sp",
                sp_acs_url="http://testserver/acs",
            ),
            relay_state="rs",
        )


# ---------------------------------------------------------------------------
# Provider-scoped RelayState (ADR 0047): the analogue of the old
# per-tenant guard. The RelayState carries the PROVIDER that started the
# flow; the ACS recovers THAT provider from the state we minted, so a
# captured RelayState is pinned to its originating provider and cannot be
# steered onto a different one. (The global unique-on-provider constraint
# allows at most one enabled SAML config, so "another provider" is modelled
# by replacing the config with a fresh id after the state was minted.)
# ---------------------------------------------------------------------------
async def _delete_saml_config(dsn: str, config_id: UUID) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("DELETE FROM sso_configurations WHERE id = $1", config_id)
    finally:
        await conn.close()


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_relay_state_is_pinned_to_its_provider(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A RelayState minted by provider A's /login is bound to A's provider
    id. If A is then removed and a DIFFERENT global SAML provider (B) is
    enabled in its place, replaying A's RelayState resolves the now-gone
    provider A (from the stored state) — NOT B — so the ACS rejects it
    (400). This proves the captured state cannot be steered onto another
    provider, the global analogue of the old per-tenant RelayState guard."""
    await _truncate_all(migrations_pg_dsn)
    provider_a = await _seed_global_saml(migrations_pg_dsn)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        # Mint a RelayState while provider A is the enabled global config.
        relay_state_a = await _login_relay_state(client, provider_a)

        # Provider A is removed and a fresh provider B takes the global slot.
        await _delete_saml_config(migrations_pg_dsn, provider_a)
        provider_b = await _seed_global_saml(migrations_pg_dsn)
        assert provider_b != provider_a

        # Replaying A's RelayState resolves provider A from the stored
        # state; A no longer exists, so the ACS answers 400 (it does NOT
        # silently fall through onto provider B).
        resp = await client.post(
            "/auth/sso/saml/acs",
            data={"SAMLResponse": _build_signed_response(), "RelayState": relay_state_a},
        )
    assert resp.status_code == 400, resp.text
    assert "no longer available" in resp.text.lower()
