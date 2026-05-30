"""Integration tests for MFA WebAuthn / FIDO2 (Plan 08 task_08_10).

WebAuthn is a SECOND opt-in second factor ADDED ALONGSIDE the existing auth
(local login + OIDC + SAML) and next to TOTP (task_08_09). A user with no
registered authenticator logs in EXACTLY as before; only a user with a
confirmed credential is challenged after the password step.

These tests run fully offline — there is no real authenticator. A small
:class:`SoftwareAuthenticator` (an in-process ES256/P-256 key) crafts the
attestation (``fmt=none``) and assertion objects that py_webauthn verifies,
exactly the way a hardware key / browser would, so the SAME production code
path (generate options -> verify response) is exercised end to end.

Coverage:

  * register/begin -> register/finish verifies the attestation and STORES
    the credential (public key + sign_count); the row is tenant-scoped.
  * a confirmed user's login returns ``mfa_required`` advertising
    ``webauthn``; ``login/begin`` + a valid assertion at ``login/finish``
    yields a real session that ``/auth/me`` accepts, and bumps sign_count.
  * a REPLAY (an assertion whose sign_count is not greater than the stored
    one) is rejected (400) and yields no session.
  * cross-tenant (@pytest.mark.cross_tenant): tenant A cannot see / use
    tenant B's credential (RLS) and a B-only authenticator cannot complete
    an A login.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the fixtures create a throwaway DB and flush Redis DB 15.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import struct
from dataclasses import dataclass, field
from hashlib import sha256
from uuid import UUID, uuid4

import asyncpg
import cbor2
import pytest
from alembic import command
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from httpx import ASGITransport, AsyncClient
from webauthn.helpers import bytes_to_base64url

pytestmark = pytest.mark.integration

# Must match the settings the configured_app fixture sets below.
_RP_ID = "localhost"
_ORIGIN = "http://localhost:3000"

# COSE constants for an ES256 (ECDSA P-256 + SHA-256) public key.
_COSE_ALG_ES256 = -7
_COSE_KTY_EC2 = 2
_COSE_CRV_P256 = 1
# AuthenticatorData flag bits (UP = user present, AT = attested cred data).
_FLAG_UP = 0x01
_FLAG_AT = 0x40


# ---------------------------------------------------------------------------
# Offline software authenticator (no real device needed)
# ---------------------------------------------------------------------------
@dataclass
class SoftwareAuthenticator:
    """An in-process ES256 authenticator that crafts WebAuthn ceremonies.

    Holds a P-256 keypair + a credential id + a monotonically increasing
    signature counter, exactly like a real authenticator. ``register`` emits
    an attestation py_webauthn accepts (``fmt=none``); ``authenticate`` emits
    a signed assertion. ``sign_count`` is bumped on each assertion unless
    pinned (to simulate a cloned-token replay with a stale counter).
    """

    rp_id: str = _RP_ID
    credential_id: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    sign_count: int = 0
    _key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )

    # ----- COSE public key (the bytes py_webauthn stores) -----
    def _cose_public_key(self) -> bytes:
        numbers = self._key.public_key().public_numbers()
        x = numbers.x.to_bytes(32, "big")
        y = numbers.y.to_bytes(32, "big")
        # COSE_Key map with integer labels (RFC 8152).
        return cbor2.dumps(
            {
                1: _COSE_KTY_EC2,  # kty
                3: _COSE_ALG_ES256,  # alg
                -1: _COSE_CRV_P256,  # crv
                -2: x,
                -3: y,
            }
        )

    # ----- authenticatorData (rp_id_hash || flags || counter [|| attested]) -----
    def _authenticator_data(self, *, include_attested: bool) -> bytes:
        rp_id_hash = sha256(self.rp_id.encode("utf-8")).digest()
        flags = _FLAG_UP
        if include_attested:
            flags |= _FLAG_AT
        data = rp_id_hash + struct.pack(">B", flags) + struct.pack(">I", self.sign_count)
        if include_attested:
            aaguid = b"\x00" * 16
            cred_id_len = struct.pack(">H", len(self.credential_id))
            data += aaguid + cred_id_len + self.credential_id + self._cose_public_key()
        return data

    @staticmethod
    def _client_data(*, ceremony_type: str, challenge: bytes) -> bytes:
        return json.dumps(
            {
                "type": ceremony_type,
                "challenge": bytes_to_base64url(challenge),
                "origin": _ORIGIN,
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def _sign(self, message: bytes) -> bytes:
        # py_webauthn expects an ASN.1 DER ECDSA signature; cryptography
        # already produces DER, but we normalise via decode/encode so the
        # intent is explicit.
        der = self._key.sign(message, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return encode_dss_signature(r, s)

    # ----- registration ceremony -----
    def register(self, options: dict) -> dict:
        challenge = _b64url_decode(options["challenge"])
        client_data = self._client_data(ceremony_type="webauthn.create", challenge=challenge)
        auth_data = self._authenticator_data(include_attested=True)
        attestation_object = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        cred_id_b64 = bytes_to_base64url(self.credential_id)
        return {
            "id": cred_id_b64,
            "rawId": cred_id_b64,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "attestationObject": bytes_to_base64url(attestation_object),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    # ----- authentication ceremony -----
    def authenticate(self, options: dict, *, bump: bool = True) -> dict:
        if bump:
            self.sign_count += 1
        challenge = _b64url_decode(options["challenge"])
        client_data = self._client_data(ceremony_type="webauthn.get", challenge=challenge)
        auth_data = self._authenticator_data(include_attested=False)
        signature = self._sign(auth_data + sha256(client_data).digest())
        cred_id_b64 = bytes_to_base64url(self.credential_id)
        return {
            "id": cred_id_b64,
            "rawId": cred_id_b64,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
                "userHandle": None,
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }


def _b64url_decode(value: str) -> bytes:
    import base64

    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


# ---------------------------------------------------------------------------
# Seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE webauthn_credentials, user_mfa_totp, scim_tokens, "
            "sso_configurations, user_org_memberships, organizations, users "
            "RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


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


async def _register_and_member(dsn: str, *, tenant_id: UUID, email: str, password: str) -> UUID:
    from api_server.auth.passwords import hash_password

    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name, is_system_admin) "
            "VALUES ($1, $2, $3, $4, false)",
            user_id,
            email,
            hash_password(password),
            "WebAuthn Tester",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active) "
            "VALUES ($1, $2, $3, 'tenant_user', true)",
            uuid4(),
            tenant_id,
            user_id,
        )
    finally:
        await conn.close()
    return user_id


async def _add_membership(dsn: str, *, tenant_id: UUID, user_id: UUID) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active) "
            "VALUES ($1, $2, $3, 'tenant_user', true)",
            uuid4(),
            tenant_id,
            user_id,
        )
    finally:
        await conn.close()


async def _credential_row(dsn: str, *, tenant_id: UUID, user_id: UUID) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(
            "SELECT sign_count, public_key, credential_id, confirmed_at "
            "FROM webauthn_credentials WHERE tenant_id = $1 AND user_id = $2",
            tenant_id,
            user_id,
        )
    finally:
        await conn.close()


async def _issue_tenant_jwt(redis_url: str, *, user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore
    from redis.asyncio import Redis
    from uuid6 import uuid7

    session_id = uuid7()
    redis: Redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        await SessionStore(redis).create(
            session_id, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
        )
    finally:
        await redis.aclose()
    return encode_jwt(user_id=user_id, session_id=session_id, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# App fixture (same shape as test_mfa_totp.configured_app, with RP settings)
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
    monkeypatch.setenv("API_SERVER_WEBAUTHN_RP_ID", _RP_ID)
    monkeypatch.setenv("API_SERVER_WEBAUTHN_ORIGIN", _ORIGIN)
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


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register_authenticator(
    client: AsyncClient, jwt: str, authenticator: SoftwareAuthenticator
) -> None:
    begin = await client.post("/auth/mfa/webauthn/register/begin", headers=_bearer(jwt))
    assert begin.status_code == 200, begin.text
    options = begin.json()["options"]
    attestation = authenticator.register(options)
    finish = await client.post(
        "/auth/mfa/webauthn/register/finish",
        json={"credential": attestation, "label": "Test Key"},
        headers=_bearer(jwt),
    )
    assert finish.status_code == 200, finish.text
    assert len(finish.json()["credentials"]) >= 1


# ---------------------------------------------------------------------------
# Registration verifies + stores the credential
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_register_verifies_and_stores(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    user_id = await _register_and_member(
        migrations_pg_dsn, tenant_id=tenant, email="wa@acme.example.com", password="longenoughpw"
    )
    jwt = await _issue_tenant_jwt(test_redis_url, user_id=user_id, tenant_id=tenant)
    authenticator = SoftwareAuthenticator()

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # No credentials before.
        listed = await client.get("/auth/mfa/webauthn", headers=_bearer(jwt))
        assert listed.status_code == 200, listed.text
        assert listed.json()["credentials"] == []

        await _register_authenticator(client, jwt, authenticator)

        after = await client.get("/auth/mfa/webauthn", headers=_bearer(jwt))
        creds = after.json()["credentials"]
        assert len(creds) == 1
        assert creds[0]["label"] == "Test Key"

    # The public key is stored (not a secret); confirmed; counter starts at 0.
    row = await _credential_row(migrations_pg_dsn, tenant_id=tenant, user_id=user_id)
    assert row is not None
    assert row["confirmed_at"] is not None
    assert bytes(row["credential_id"]) == authenticator.credential_id
    assert bytes(row["public_key"])  # COSE public key bytes present
    assert row["sign_count"] == 0


# ---------------------------------------------------------------------------
# Registration with a tampered attestation fails (no challenge match)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_register_wrong_challenge_fails(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    user_id = await _register_and_member(
        migrations_pg_dsn, tenant_id=tenant, email="wa@acme.example.com", password="longenoughpw"
    )
    jwt = await _issue_tenant_jwt(test_redis_url, user_id=user_id, tenant_id=tenant)
    authenticator = SoftwareAuthenticator()

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        await client.post("/auth/mfa/webauthn/register/begin", headers=_bearer(jwt))
        # Craft an attestation against a DIFFERENT challenge than the server stashed.
        attestation = authenticator.register({"challenge": bytes_to_base64url(b"not-the-one")})
        finish = await client.post(
            "/auth/mfa/webauthn/register/finish",
            json={"credential": attestation},
            headers=_bearer(jwt),
        )
        assert finish.status_code == 400, finish.text

    assert await _credential_row(migrations_pg_dsn, tenant_id=tenant, user_id=user_id) is None


# ---------------------------------------------------------------------------
# Login: mfa_required(webauthn) -> begin -> valid assertion -> session, counter bumps
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_login_webauthn_then_verify(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    user_id = await _register_and_member(
        migrations_pg_dsn, tenant_id=tenant, email="wa@acme.example.com", password="longenoughpw"
    )
    jwt = await _issue_tenant_jwt(test_redis_url, user_id=user_id, tenant_id=tenant)
    authenticator = SoftwareAuthenticator()

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        await _register_authenticator(client, jwt, authenticator)

        # Login: password alone returns mfa_required advertising webauthn.
        login = await client.post(
            "/auth/login",
            json={"email": "wa@acme.example.com", "password": "longenoughpw"},
        )
        assert login.status_code == 200, login.text
        lbody = login.json()
        assert lbody["status"] == "mfa_required"
        assert "access_token" not in lbody
        assert "webauthn" in lbody["mfa_methods"]
        mfa_token = lbody["mfa_token"]

        # Begin the WebAuthn login -> assertion options.
        begin = await client.post("/auth/mfa/webauthn/login/begin", json={"mfa_token": mfa_token})
        assert begin.status_code == 200, begin.text
        options = begin.json()["options"]

        # Sign the assertion (counter -> 1) and finish -> a real session.
        assertion = authenticator.authenticate(options)
        finish = await client.post(
            "/auth/mfa/webauthn/login/finish",
            json={"mfa_token": mfa_token, "credential": assertion},
        )
        assert finish.status_code == 200, finish.text
        token = finish.json()["access_token"]
        assert token

        me = await client.get("/auth/me", headers=_bearer(token))
        assert me.status_code == 200, me.text
        assert me.json()["email"] == "wa@acme.example.com"

    # The stored counter advanced to the asserted value.
    row = await _credential_row(migrations_pg_dsn, tenant_id=tenant, user_id=user_id)
    assert row is not None
    assert row["sign_count"] == 1


# ---------------------------------------------------------------------------
# Replay: an assertion with a stale (non-increasing) sign_count is rejected
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_replay_stale_sign_count_rejected(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    user_id = await _register_and_member(
        migrations_pg_dsn, tenant_id=tenant, email="wa@acme.example.com", password="longenoughpw"
    )
    jwt = await _issue_tenant_jwt(test_redis_url, user_id=user_id, tenant_id=tenant)
    authenticator = SoftwareAuthenticator()

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        await _register_authenticator(client, jwt, authenticator)

        # First login: counter 0 -> 1, accepted.
        login1 = await client.post(
            "/auth/login",
            json={"email": "wa@acme.example.com", "password": "longenoughpw"},
        )
        token1 = login1.json()["mfa_token"]
        begin1 = await client.post("/auth/mfa/webauthn/login/begin", json={"mfa_token": token1})
        ok = await client.post(
            "/auth/mfa/webauthn/login/finish",
            json={
                "mfa_token": token1,
                "credential": authenticator.authenticate(begin1.json()["options"]),
            },
        )
        assert ok.status_code == 200, ok.text

        # Second login: a CLONED token replays a stale counter (pin it to 1,
        # not greater than the stored 1) -> rejected, no session.
        login2 = await client.post(
            "/auth/login",
            json={"email": "wa@acme.example.com", "password": "longenoughpw"},
        )
        token2 = login2.json()["mfa_token"]
        begin2 = await client.post("/auth/mfa/webauthn/login/begin", json={"mfa_token": token2})
        replayed = await client.post(
            "/auth/mfa/webauthn/login/finish",
            json={
                "mfa_token": token2,
                "credential": authenticator.authenticate(begin2.json()["options"], bump=False),
            },
        )
        assert replayed.status_code == 400, replayed.text

    # The stored counter stayed at 1 (the replay never advanced it).
    row = await _credential_row(migrations_pg_dsn, tenant_id=tenant, user_id=user_id)
    assert row is not None
    assert row["sign_count"] == 1


# ---------------------------------------------------------------------------
# Cross-tenant: tenant A cannot see / use tenant B's credential (RLS)
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cross_tenant_isolation(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    # One human, member of BOTH tenants, registers a key only in B.
    user_id = await _register_and_member(
        migrations_pg_dsn, tenant_id=tenant_b, email="multi@example.com", password="longenoughpw"
    )
    await _add_membership(migrations_pg_dsn, tenant_id=tenant_a, user_id=user_id)

    jwt_b = await _issue_tenant_jwt(test_redis_url, user_id=user_id, tenant_id=tenant_b)
    jwt_a = await _issue_tenant_jwt(test_redis_url, user_id=user_id, tenant_id=tenant_a)
    authenticator = SoftwareAuthenticator()

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        await _register_authenticator(client, jwt_b, authenticator)

        # Tenant B sees the credential.
        list_b = await client.get("/auth/mfa/webauthn", headers=_bearer(jwt_b))
        assert len(list_b.json()["credentials"]) == 1

        # Tenant A (RLS-scoped) sees NONE for the same user.
        list_a = await client.get("/auth/mfa/webauthn", headers=_bearer(jwt_a))
        assert list_a.json()["credentials"] == []

    # The DB confirms exactly one row, scoped to tenant B.
    assert await _credential_row(migrations_pg_dsn, tenant_id=tenant_a, user_id=user_id) is None
    assert await _credential_row(migrations_pg_dsn, tenant_id=tenant_b, user_id=user_id) is not None
