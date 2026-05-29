"""SAML XML signature + encryption config tests (Plan 08 task_08_05).

task_08_04 covered the security-critical *inbound* direction (verify the
IdP's assertion signature). task_08_05 adds the *outbound* / optional
crypto an enterprise IdP often demands — SP ``AuthnRequest`` signing and
(optional) assertion / NameID encryption — plus the per-config security
policy flags, with the SP private key stored encrypted at rest (never
plaintext), reusing the OIDC Fernet/Vault mechanism.

Two test surfaces:

  * **Config surface (no native crypto needed)** — runs everywhere:
      - ``validate_saml_security`` accepts a config with no key-requiring
        feature, and a config that has a key when one is required;
      - it rejects a config that turns on request signing / encryption
        without an SP cert+key (matching the DB CHECK constraint);
      - the SP private key round-trips through the Fernet at-rest helpers
        and is never stored in clear text.

  * **Crypto surface (BLOCKED-ON-XMLSEC; present on this host + CI)** —
    needs the native ``xmlsec`` backend:
      - with ``authn_requests_signed=True`` the SP-initiated redirect
        carries a ``Signature`` + ``SigAlg`` (the request was signed with
        the SP key);
      - a correctly IdP-signed assertion is ACCEPTED (200 + session);
      - a tampered assertion is REJECTED (400).

No real IdP: a self-signed in-test RSA key pair plays the IdP (signing
assertions) and a *separate* key pair is the SP (signing AuthnRequests),
exactly mirroring a real deployment. Everything runs offline.

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
from onelogin.saml2.utils import OneLogin_Saml2_Utils

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fake IdP + SP identities (two distinct self-signed key pairs)
# ---------------------------------------------------------------------------
_IDP_ENTITY_ID = "https://saml-idp.example.test/metadata"
_IDP_SSO_URL = "https://saml-idp.example.test/sso"
# Must match what the router computes from API_SERVER_SSO_REDIRECT_BASE_URL.
_SP_ENTITY_ID = "http://testserver/auth/sso/saml/metadata"
_NAME_ID = "Signer@Acme.test"


def _gen_key_and_cert(common_name: str) -> tuple[str, str, str]:
    """Return (private_key_pem, cert_pem, cert_body) for a self-signed pair.

    * ``cert_pem`` is the full PEM (header+footer) — what ``add_sign`` and
      python3-saml's signer load.
    * ``cert_body`` is the base64 body only — the form an x509 column
      carries and python3-saml reads back.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
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


_IDP_KEY_PEM, _IDP_CERT_PEM, _IDP_CERT_BODY = _gen_key_and_cert("saml-idp.test")
_SP_KEY_PEM, _SP_CERT_PEM, _SP_CERT_BODY = _gen_key_and_cert("sp.testserver")


def _saml_time(delta_minutes: int = 0) -> str:
    return (datetime.now(tz=UTC) + timedelta(minutes=delta_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_signed_response(*, tenant_id: UUID, name_id: str = _NAME_ID) -> str:
    """Build a SAML Response with an IdP-signed assertion, base64-encoded."""
    acs_url = f"http://testserver/auth/sso/{tenant_id}/saml/acs"
    response_id = "_" + uuid4().hex
    assertion_id = "_" + uuid4().hex
    not_before = _saml_time(-5)
    not_on_or_after = _saml_time(5)

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
      <saml:AttributeValue xs:type="xs:string">Signer Person</saml:AttributeValue>
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
# DB seed helpers
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


async def _seed_saml_config(
    dsn: str,
    *,
    tenant_id: UUID,
    enabled: bool = True,
    authn_requests_signed: bool = False,
    sp_cert_body: str | None = None,
    sp_private_key_encrypted: str | None = None,
) -> None:
    """Insert a `saml` row. SP key columns default to NULL (no SP crypto)."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, tenant_id, provider, display_name, enabled,
                 idp_entity_id, idp_sso_url, idp_x509_cert,
                 name_id_format, attribute_mappings,
                 sp_x509_cert, sp_private_key_encrypted, authn_requests_signed)
            VALUES ($1, $2, 'saml', 'Acme SAML', $3, $4, $5, $6, $7, $8::jsonb,
                    $9, $10, $11)
            """,
            uuid4(),
            tenant_id,
            enabled,
            _IDP_ENTITY_ID,
            _IDP_SSO_URL,
            _IDP_CERT_BODY,
            "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            json.dumps({}),
            sp_cert_body,
            sp_private_key_encrypted,
            authn_requests_signed,
        )
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
# App fixture (mirrors test_saml.py)
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


# ===========================================================================
# Config surface — pure Python, NO native crypto required (runs everywhere)
# ===========================================================================
def test_validate_security_noop_when_no_crypto_features() -> None:
    """A config that enables no key-requiring feature needs no SP key."""
    from api_server.auth.sso.saml import ResolvedSAMLConfig, validate_saml_security

    config = ResolvedSAMLConfig(
        idp_entity_id="https://idp/meta",
        idp_sso_url="https://idp/sso",
        idp_x509_cert="cert",
        sp_entity_id="http://testserver/sp",
        sp_acs_url="http://testserver/acs",
    )
    # No exception.
    validate_saml_security(config)


def test_validate_security_rejects_signing_without_sp_key() -> None:
    """Request signing with no SP cert+key is an invariant violation."""
    from api_server.auth.sso.saml import (
        ResolvedSAMLConfig,
        SAMLConfigError,
        validate_saml_security,
    )

    config = ResolvedSAMLConfig(
        idp_entity_id="https://idp/meta",
        idp_sso_url="https://idp/sso",
        idp_x509_cert="cert",
        sp_entity_id="http://testserver/sp",
        sp_acs_url="http://testserver/acs",
        authn_requests_signed=True,  # but no sp_x509_cert / sp_private_key
    )
    with pytest.raises(SAMLConfigError) as exc:
        validate_saml_security(config)
    assert "AuthnRequest signing" in str(exc.value)


def test_validate_security_rejects_encryption_without_sp_key() -> None:
    from api_server.auth.sso.saml import (
        ResolvedSAMLConfig,
        SAMLConfigError,
        validate_saml_security,
    )

    config = ResolvedSAMLConfig(
        idp_entity_id="https://idp/meta",
        idp_sso_url="https://idp/sso",
        idp_x509_cert="cert",
        sp_entity_id="http://testserver/sp",
        sp_acs_url="http://testserver/acs",
        want_assertions_encrypted=True,
        want_name_id_encrypted=True,
    )
    with pytest.raises(SAMLConfigError) as exc:
        validate_saml_security(config)
    msg = str(exc.value)
    assert "assertion encryption" in msg
    assert "NameID encryption" in msg


def test_validate_security_passes_with_sp_key() -> None:
    """A key-requiring feature is fine once the SP cert+key are present."""
    from api_server.auth.sso.saml import ResolvedSAMLConfig, validate_saml_security

    config = ResolvedSAMLConfig(
        idp_entity_id="https://idp/meta",
        idp_sso_url="https://idp/sso",
        idp_x509_cert="cert",
        sp_entity_id="http://testserver/sp",
        sp_acs_url="http://testserver/acs",
        sp_x509_cert=_SP_CERT_BODY,
        sp_private_key=_SP_KEY_PEM,
        authn_requests_signed=True,
        want_assertions_encrypted=True,
        want_name_id_encrypted=True,
    )
    validate_saml_security(config)


def test_to_settings_wires_sp_key_and_flags() -> None:
    """to_settings exposes the SP key pair + the security policy flags."""
    from api_server.auth.sso.saml import (
        DIGEST_ALGORITHM,
        SIGNATURE_ALGORITHM,
        ResolvedSAMLConfig,
    )

    config = ResolvedSAMLConfig(
        idp_entity_id="https://idp/meta",
        idp_sso_url="https://idp/sso",
        idp_x509_cert="idpcert",
        sp_entity_id="http://testserver/sp",
        sp_acs_url="http://testserver/acs",
        sp_x509_cert=_SP_CERT_BODY,
        sp_private_key=_SP_KEY_PEM,
        authn_requests_signed=True,
        want_assertions_signed=True,
        want_assertions_encrypted=True,
        want_name_id_encrypted=True,
    )
    settings = config.to_settings()
    assert settings["sp"]["x509cert"] == _SP_CERT_BODY
    assert settings["sp"]["privateKey"] == _SP_KEY_PEM
    sec = settings["security"]
    assert sec["authnRequestsSigned"] is True
    assert sec["wantAssertionsSigned"] is True
    assert sec["wantAssertionsEncrypted"] is True
    assert sec["wantNameIdEncrypted"] is True
    assert sec["signatureAlgorithm"] == SIGNATURE_ALGORITHM
    assert sec["digestAlgorithm"] == DIGEST_ALGORITHM


def test_to_settings_omits_sp_key_when_absent() -> None:
    """With no SP key material, the SP block carries neither key field."""
    from api_server.auth.sso.saml import ResolvedSAMLConfig

    config = ResolvedSAMLConfig(
        idp_entity_id="https://idp/meta",
        idp_sso_url="https://idp/sso",
        idp_x509_cert="idpcert",
        sp_entity_id="http://testserver/sp",
        sp_acs_url="http://testserver/acs",
    )
    sp = config.to_settings()["sp"]
    assert "x509cert" not in sp
    assert "privateKey" not in sp


def test_sp_private_key_round_trips_encrypted_at_rest() -> None:
    """The SP private key is Fernet-encrypted at rest, never plaintext,
    and resolves back to the exact PEM."""
    import os

    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    os.environ["API_SERVER_SSO_ENCRYPTION_KEY"] = "test-sso-encryption-key"
    os.environ.setdefault("API_SERVER_JWT_SECRET", "test-secret")
    get_settings.cache_clear()
    reset_engine_cache()
    try:
        from api_server.auth.sso.secrets import (
            encrypt_client_secret,
            resolve_sp_private_key,
        )

        ciphertext = encrypt_client_secret(_SP_KEY_PEM)
        # The stored value is opaque — the PEM body must not leak.
        assert "PRIVATE KEY" not in ciphertext
        assert ciphertext != _SP_KEY_PEM

        recovered = resolve_sp_private_key(
            sp_private_key_ref=None,
            sp_private_key_encrypted=ciphertext,
            vault_resolver=None,
        )
        assert recovered == _SP_KEY_PEM
    finally:
        get_settings.cache_clear()
        reset_engine_cache()


def test_resolve_sp_private_key_none_when_unset() -> None:
    """No SP key configured → None (a valid state: SP crypto disabled)."""
    from api_server.auth.sso.secrets import resolve_sp_private_key

    assert (
        resolve_sp_private_key(
            sp_private_key_ref=None,
            sp_private_key_encrypted=None,
            vault_resolver=None,
        )
        is None
    )


# ===========================================================================
# Crypto surface — BLOCKED-ON-XMLSEC (needs the native xmlsec backend; it is
# present on this host + the Docker/CI image, so the full path runs here)
# ===========================================================================
def _encrypt_sp_key() -> str:
    """Fernet-encrypt the SP private key with the test SSO key.

    Must run after the env var is set; the seed helpers call this only
    inside a configured test, so set the key locally to be safe.
    """
    import os

    from api_server.auth.sso.secrets import encrypt_client_secret
    from api_server.config import get_settings

    os.environ["API_SERVER_SSO_ENCRYPTION_KEY"] = "test-sso-encryption-key"
    os.environ.setdefault("API_SERVER_JWT_SECRET", "test-secret")
    get_settings.cache_clear()
    return encrypt_client_secret(_SP_KEY_PEM)


@pytest.mark.asyncio
async def test_signed_authn_request_carries_signature(
    configured_app, migrations_pg_dsn: str
) -> None:
    """With authn_requests_signed=True + an SP key, the SP-initiated
    redirect URL carries a `Signature` + `SigAlg` — the AuthnRequest was
    signed with the SP private key."""
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_saml_config(
        migrations_pg_dsn,
        tenant_id=tenant,
        authn_requests_signed=True,
        sp_cert_body=_SP_CERT_BODY,
        sp_private_key_encrypted=_encrypt_sp_key(),
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(f"/auth/sso/{tenant}/saml/login")
    assert resp.status_code == 302, resp.text
    params = dict(httpx.URL(resp.headers["location"]).params)
    assert "SAMLRequest" in params
    # The signing artifacts of the HTTP-Redirect binding.
    assert "Signature" in params
    assert "SigAlg" in params
    assert "sha256" in params["SigAlg"]


@pytest.mark.asyncio
async def test_correctly_signed_assertion_accepted(configured_app, migrations_pg_dsn: str) -> None:
    """A correctly IdP-signed assertion is accepted: 200 + a live session
    even when the tenant additionally signs its own AuthnRequests."""
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_saml_config(
        migrations_pg_dsn,
        tenant_id=tenant,
        authn_requests_signed=True,
        sp_cert_body=_SP_CERT_BODY,
        sp_private_key_encrypted=_encrypt_sp_key(),
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        # IdP-initiated form keeps the test focused on signature validation.
        saml_response = _build_signed_response(tenant_id=tenant)
        resp = await client.post(
            f"/auth/sso/{tenant}/saml/acs",
            data={"SAMLResponse": saml_response},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["token_type"] == "bearer"
        token = body["access_token"]
        me = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        assert me.json()["email"] == "signer@acme.test"


@pytest.mark.asyncio
async def test_tampered_assertion_rejected(configured_app, migrations_pg_dsn: str) -> None:
    """A tampered signed assertion breaks the signature → 400."""
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_saml_config(
        migrations_pg_dsn,
        tenant_id=tenant,
        authn_requests_signed=True,
        sp_cert_body=_SP_CERT_BODY,
        sp_private_key_encrypted=_encrypt_sp_key(),
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        saml_response = _build_signed_response(tenant_id=tenant)
        raw = base64.b64decode(saml_response).decode("utf-8")
        tampered = raw.replace("Signer Person", "Attacker Person")
        tampered_b64 = base64.b64encode(tampered.encode("utf-8")).decode("ascii")
        resp = await client.post(
            f"/auth/sso/{tenant}/saml/acs",
            data={"SAMLResponse": tampered_b64},
        )
    assert resp.status_code == 400, resp.text
    assert "saml authentication failed" in resp.text.lower()


@pytest.mark.asyncio
async def test_unsigned_assertion_rejected(configured_app, migrations_pg_dsn: str) -> None:
    """An assertion with NO signature is rejected (wantAssertionsSigned)."""
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_saml_config(migrations_pg_dsn, tenant_id=tenant)

    acs_url = f"http://testserver/auth/sso/{tenant}/saml/acs"
    not_before = _saml_time(-5)
    not_on_or_after = _saml_time(5)
    # A well-formed but UNSIGNED Response — no ds:Signature anywhere.
    unsigned = f"""<samlp:Response \
xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" \
xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" \
ID="_{uuid4().hex}" Version="2.0" IssueInstant="{_saml_time()}" \
Destination="{acs_url}">
  <saml:Issuer>{_IDP_ENTITY_ID}</saml:Issuer>
  <samlp:Status>
    <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
  </samlp:Status>
  <saml:Assertion ID="_{uuid4().hex}" Version="2.0" IssueInstant="{_saml_time()}">
    <saml:Issuer>{_IDP_ENTITY_ID}</saml:Issuer>
    <saml:Subject>
      <saml:NameID \
Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">x@acme.test</saml:NameID>
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
    <saml:AuthnStatement AuthnInstant="{_saml_time()}" SessionIndex="_x">
      <saml:AuthnContext>
        <saml:AuthnContextClassRef>\
urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport\
</saml:AuthnContextClassRef>
      </saml:AuthnContext>
    </saml:AuthnStatement>
  </saml:Assertion>
</samlp:Response>"""
    unsigned_b64 = base64.b64encode(unsigned.encode("utf-8")).decode("ascii")

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.post(
            f"/auth/sso/{tenant}/saml/acs",
            data={"SAMLResponse": unsigned_b64},
        )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# Cross-tenant isolation: tenant A's SP-signing config never bleeds into B
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_sp_signing_config_isolated_per_tenant(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tenant A signs its AuthnRequests (SP key set); tenant B does not.
    B's login must succeed WITHOUT a Signature (its own config, not A's),
    proving the SP key/flags resolve under RLS per tenant — A's never
    leak into B's request."""
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    await _seed_saml_config(
        migrations_pg_dsn,
        tenant_id=tenant_a,
        authn_requests_signed=True,
        sp_cert_body=_SP_CERT_BODY,
        sp_private_key_encrypted=_encrypt_sp_key(),
    )
    await _seed_saml_config(migrations_pg_dsn, tenant_id=tenant_b)  # no SP crypto

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        a = await client.get(f"/auth/sso/{tenant_a}/saml/login")
        b = await client.get(f"/auth/sso/{tenant_b}/saml/login")

    assert a.status_code == 302, a.text
    assert b.status_code == 302, b.text
    a_params = dict(httpx.URL(a.headers["location"]).params)
    b_params = dict(httpx.URL(b.headers["location"]).params)
    assert "Signature" in a_params  # A signs
    assert "Signature" not in b_params  # B does not — A's key did not leak
