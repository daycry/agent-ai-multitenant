"""The rotation drill: rotate every key and prove the data survived (task_prod05_10).

Each of the previous prod-05 test files proves ONE mechanism. This one runs the
sequences an operator actually performs, end to end, in the order the runbook
prescribes — because the failure this plan exists to prevent is not "MultiFernet
is broken", it is "the operator followed the runbook and lost the data anyway".

The shape of every phase below is the same, and it is the only shape that proves
anything: **encrypt/sign BEFORE the rotation, rotate, then read AFTER with the
new configuration only.** A test that keeps both keys in hand at verification
time passes before the feature exists.

Four phases, matching the runbook:

  A. JWT + internal token, two phases. A session and an in-flight agent token
     survive adding the new key, and STOP working when the old one is dropped.
  B. The four at-rest families, three steps, against real database rows: add,
     ``reencrypt-secrets``, drop — and then every real consumer path
     (OIDC secret, TOTP seed, channel secret, webhook secret) still reads with
     the old key GONE from the configuration.
  C. Backups: a bundle written under the retired key restores after the rotation,
     including a legacy format-v1 bundle with no key id.
  D. The rotation job: without Vault it is SKIPPED and alerts; with Vault it
     succeeds, marks the entry ``pending_apply``, and only the explicit
     propagation step clears it and revokes the previous MinIO credential.

WHAT THIS DRILL DOES NOT PROVE. It runs in one process against a real database,
so it exercises the code paths and the data, not the deployment: it does not
restart containers, does not talk to a real Vault or a real MinIO, and cannot
observe the restart window. Those belong to the plan's human tests
(``human_prod05_01`` … ``human_prod05_04``). Saying so here is deliberate — a
green drill must not be read as "the rotation was exercised in production shape".

Pre-condition: the docker-compose postgres is healthy.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.auth import internal_agent as agent_mod
from api_server.auth import jwt as jwt_mod
from api_server.auth.crypto_keys import derive_fernet_key
from api_server.auth.internal_agent import (
    InvalidAgentTokenError,
    decode_agent_token,
    mint_agent_token,
)
from api_server.auth.jwt import InvalidTokenError, decode_jwt, encode_jwt
from api_server.cli.reencrypt_secrets import reencrypt_secrets
from api_server.config import Settings
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from workers.backup_encryption import BackupEncryptionError, BackupEncryptor
from workers.config import Settings as WorkerSettings
from workers.credential_rotation import RotationStatus
from workers.credential_rotation_hvac import (
    KV_FIELD_ACCESS_KEY,
    KV_FIELD_PENDING_APPLY,
    HvacVaultRotationClient,
    revoke_previous_minio_credential,
)
from workers.credential_rotation_task import _rotate_credentials
from workers.secrets import StaticSecretsProvider

pytestmark = pytest.mark.integration

# Material de clave FALSO y sin forma PEM a propósito: el código bajo prueba
# solo ve bytes opacos (cifra y descifra), así que la forma `-----BEGIN ...-----`
# no aportaba nada y disparaba el hook `detect-private-key` — que hace bien en
# ser estricto. No le devuelvas el envoltorio PEM "por realismo".
_FAKE_SP_KEY_MATERIAL = "sp-key-material::drill"

_OLD = "the-key-in-service-until-the-drill-0001"
_NEW = "the-key-that-takes-over-in-the-drill-002"
_OLD_AGENT = "worker-internal-key-until-the-drill-0003"
_NEW_AGENT = "worker-internal-key-after-the-drill-004"


# ===========================================================================
# Phase A — signing keys, two phases
# ===========================================================================
def _session_settings(**overrides: str) -> Settings:
    base: dict[str, str] = {
        "environment": "dev",
        "jwt_secret": _OLD,
        "internal_token_secret": _OLD_AGENT,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _use(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(jwt_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_mod, "get_settings", lambda: settings)


def test_phase_a_a_session_and_an_in_flight_agent_token_survive_the_jwt_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runbook §1 and §2, both keys at once — which is how they are rotated.

    The agent token is the interesting half: it was injected into a container that
    is still running and cannot be re-minted, so if it stops validating, a plan
    execution dies mid-flight.
    """
    # --- before the rotation -------------------------------------------------
    _use(monkeypatch, _session_settings())
    session_token = encode_jwt(user_id=uuid4(), session_id=uuid4())
    agent_id, tenant_id = uuid4(), uuid4()
    agent_token = mint_agent_token(agent_id=agent_id, tenant_id=tenant_id)

    # --- step 1: new key at the head, old key kept ---------------------------
    _use(
        monkeypatch,
        _session_settings(
            jwt_secrets=f"{_NEW},{_OLD}",
            internal_token_secrets=f"{_NEW_AGENT},{_OLD_AGENT}",
        ),
    )
    assert decode_jwt(session_token)["sid"], "the human session did not survive step 1"
    assert decode_agent_token(agent_token).agent_id == agent_id

    # Tokens minted DURING the window are on the new key, so step 3 will not
    # invalidate them.
    fresh_session = encode_jwt(user_id=uuid4(), session_id=uuid4())
    fresh_agent = mint_agent_token(agent_id=agent_id, tenant_id=tenant_id)

    # --- step 3: retire the old keys -----------------------------------------
    _use(
        monkeypatch,
        _session_settings(jwt_secrets=_NEW, internal_token_secrets=_NEW_AGENT),
    )
    assert decode_jwt(fresh_session)["sid"], "a token minted during the window was lost"
    assert decode_agent_token(fresh_agent).agent_id == agent_id
    with pytest.raises(InvalidTokenError):
        decode_jwt(session_token)
    with pytest.raises(InvalidAgentTokenError):
        decode_agent_token(agent_token)


# ===========================================================================
# Phase B — the four at-rest families, against real rows
# ===========================================================================
_SEEDED_TABLES = (
    "incoming_webhook_configs",
    "notification_channels",
    "user_mfa_totp",
    "sso_configurations",
    "projects",
    "users",
    "organizations",
)


@pytest.fixture()
def migrated_db(alembic_config: object, migrations_pg_dsn: str) -> Iterator[str]:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    yield migrations_pg_dsn


@pytest.fixture()
def session_factory(
    migrated_db: str, admin_database_url: str
) -> Iterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(admin_database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        asyncio.run(engine.dispose())


def _at_rest_settings(keys: str) -> Settings:
    """One ring for all four families — the "rotate everything" drill."""
    return Settings(
        environment="dev",
        sso_encryption_keys=keys,
        mfa_encryption_keys=keys,
        notification_encryption_keys=keys,
        incoming_webhook_encryption_keys=keys,
    )


def _encrypt_with(raw_key: str, plaintext: str) -> str:
    return Fernet(derive_fernet_key(raw_key)).encrypt(plaintext.encode()).decode("ascii")


async def _seed_at_rest(dsn: str, *, key: str) -> dict[str, UUID]:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f"TRUNCATE {', '.join(_SEEDED_TABLES)} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()

    ids = {k: uuid4() for k in ("sso", "saml", "user", "mfa", "channel", "project", "webhook")}
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            f"Drill {tenant_id}",
            f"drill-{str(tenant_id)[:8]}",
        )
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, provider, display_name, enabled, issuer, client_id,
                 client_secret_encrypted)
            VALUES ($1, 'oidc', 'Drill OIDC', true, 'https://idp.test', 'drill', $2)
            """,
            ids["sso"],
            _encrypt_with(key, "the-oidc-client-secret"),
        )
        # The SAML SP private key lives in the SAME family but a DIFFERENT row
        # (`ck_sso_config_provider_shape` forbids one row being both).
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, provider, display_name, enabled, idp_entity_id, idp_sso_url,
                 idp_x509_cert, sp_private_key_encrypted)
            VALUES ($1, 'saml', 'Drill SAML', true, 'urn:drill:idp',
                    'https://idp.test/sso', 'MIIB-fake-cert', $2)
            """,
            ids["saml"],
            _encrypt_with(key, _FAKE_SP_KEY_MATERIAL),
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'x')",
            ids["user"],
            f"drill-{ids['user']}@example.test",
        )
        await conn.execute(
            "INSERT INTO user_mfa_totp (id, tenant_id, user_id, secret_encrypted) "
            "VALUES ($1, $2, $3, $4)",
            ids["mfa"],
            tenant_id,
            ids["user"],
            _encrypt_with(key, "JBSWY3DPEHPK3PXP"),
        )
        await conn.execute(
            """
            INSERT INTO notification_channels
                (id, scope, channel_type, name, tenant_id, secret_encrypted)
            VALUES ($1, 'tenant', 'slack', 'Drill Slack', $2, $3)
            """,
            ids["channel"],
            tenant_id,
            _encrypt_with(key, "xoxb-drill-token"),
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'Drill')",
            ids["project"],
            tenant_id,
        )
        await conn.execute(
            """
            INSERT INTO incoming_webhook_configs
                (id, tenant_id, project_id, origin, name, signing_secret_encrypted)
            VALUES ($1, $2, $3, 'github', 'Drill hook', $4)
            """,
            ids["webhook"],
            tenant_id,
            ids["project"],
            _encrypt_with(key, "github-drill-hmac"),
        )
    finally:
        await conn.close()
    return ids


async def _fetch(dsn: str, table: str, column: str, row_id: UUID) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        return str(
            await conn.fetchval(
                f"SELECT {column} FROM {table} WHERE id = $1",
                row_id,  # - test-local
            )
        )
    finally:
        await conn.close()


def test_phase_b_every_at_rest_consumer_still_reads_after_the_old_key_is_dropped(
    migrated_db: str,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runbook §3-6, all three steps, verified through the REAL consumer helpers.

    Verifying through ``decrypt_totp_secret`` / ``decrypt_client_secret`` /
    ``decrypt_secret`` / ``decrypt_signing_secret`` rather than a bare Fernet is
    the point: it is those four functions that the login flow, the token exchange,
    the dispatcher and the webhook verifier actually call. A rotation that leaves
    the raw bytes readable but breaks one of those helpers is still an outage.
    """
    from api_server.auth.mfa import secrets as mfa_secrets
    from api_server.auth.sso import secrets as sso_secrets
    from api_server.webhooks import secrets as webhook_secrets
    from notification_dispatcher import secrets as dispatcher_secrets
    from notification_dispatcher.config import Settings as DispatcherSettings

    ids = asyncio.run(_seed_at_rest(migrated_db, key=_OLD))

    # --- step 1: new key at the head -----------------------------------------
    both = _at_rest_settings(f"{_NEW},{_OLD}")

    # --- step 2: re-encrypt ---------------------------------------------------
    async def run_reencrypt() -> None:
        async with session_factory() as session:
            report = await reencrypt_secrets(session, settings=both, dry_run=False)
        assert report.migrated == 5, report.render()
        assert report.unreadable == 0, report.render()

    asyncio.run(run_reencrypt())

    # --- step 3: the old key is GONE from the configuration -------------------
    after = _at_rest_settings(_NEW)
    for module in (sso_secrets, mfa_secrets, webhook_secrets):
        monkeypatch.setattr(module, "get_settings", lambda: after)

    assert (
        sso_secrets.decrypt_client_secret(
            asyncio.run(
                _fetch(migrated_db, "sso_configurations", "client_secret_encrypted", ids["sso"])
            )
        )
        == "the-oidc-client-secret"
    )
    assert (
        mfa_secrets.decrypt_totp_secret(
            asyncio.run(_fetch(migrated_db, "user_mfa_totp", "secret_encrypted", ids["mfa"]))
        )
        == "JBSWY3DPEHPK3PXP"
    )
    assert (
        webhook_secrets.decrypt_signing_secret(
            asyncio.run(
                _fetch(
                    migrated_db,
                    "incoming_webhook_configs",
                    "signing_secret_encrypted",
                    ids["webhook"],
                )
            )
        )
        == "github-drill-hmac"
    )
    # The dispatcher is a SEPARATE service with its own settings: the pair
    # contract is what this line exercises.
    assert (
        dispatcher_secrets.decrypt_secret(
            asyncio.run(
                _fetch(migrated_db, "notification_channels", "secret_encrypted", ids["channel"])
            ),
            DispatcherSettings(environment="dev", notification_encryption_keys=_NEW),
        )
        == "xoxb-drill-token"
    )

    # And the old key really is retired: it can no longer read the row.
    stored = asyncio.run(_fetch(migrated_db, "user_mfa_totp", "secret_encrypted", ids["mfa"]))
    monkeypatch.setattr(mfa_secrets, "get_settings", lambda: _at_rest_settings(_OLD))
    with pytest.raises(mfa_secrets.MfaSecretError):
        mfa_secrets.decrypt_totp_secret(stored)


def test_phase_b_skipping_the_reencryption_is_what_destroys_the_data(
    migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative control, and the reason step 2 is not optional.

    Without this the suite would only ever show the happy path, and "why can I
    not just add the key and drop the old one next month?" would have no answer
    in the tests.
    """
    from api_server.auth.mfa import secrets as mfa_secrets

    ids = asyncio.run(_seed_at_rest(migrated_db, key=_OLD))
    stored = asyncio.run(_fetch(migrated_db, "user_mfa_totp", "secret_encrypted", ids["mfa"]))

    monkeypatch.setattr(mfa_secrets, "get_settings", lambda: _at_rest_settings(_NEW))
    with pytest.raises(mfa_secrets.MfaSecretError):
        mfa_secrets.decrypt_totp_secret(stored)


# ===========================================================================
# Phase C — backups
# ===========================================================================
def test_phase_c_a_bundle_written_under_the_retired_key_still_restores(
    tmp_path: Any,
) -> None:
    """Runbook §7. Includes a LEGACY v1 bundle (no key id), because the bundles
    already in object storage are v1 and they are the ones a real DR would need.
    """
    key_name = "backup_encryption_key"
    keys_name = "backup_encryption_keys"

    before = BackupEncryptor(
        provider=StaticSecretsProvider(values={key_name: _OLD}), vault_key_name=key_name
    )
    old_bundle = tmp_path / "before.tar.enc"
    plaintext = tmp_path / "bundle.tar"
    plaintext.write_bytes(b"pg_dump output" * 500)
    before.encrypt_file(plaintext, old_bundle)

    # A v1 blob, built the way the pre-prod-05 code built them.
    legacy_header = b"AGENTBK1" + bytes([1])
    nonce = os.urandom(12)
    import hashlib

    legacy = tmp_path / "legacy.tar.enc"
    legacy.write_bytes(
        legacy_header
        + nonce
        + AESGCM(hashlib.sha256(_OLD.encode()).digest()).encrypt(
            nonce, b"a bundle from before the key id", legacy_header
        )
    )

    # --- rotate: new key at the head, old key KEPT (runbook §7 has no step 3) --
    rotated = BackupEncryptor(
        provider=StaticSecretsProvider(values={keys_name: f"{_NEW},{_OLD}"}),
        vault_key_name=key_name,
    )
    restored = tmp_path / "restored.tar"
    rotated.decrypt_file(old_bundle, restored)
    assert restored.read_bytes() == plaintext.read_bytes()
    assert rotated.decrypt_bytes(legacy.read_bytes()) == b"a bundle from before the key id"

    # A bundle written after the rotation needs only the new key.
    new_bundle = tmp_path / "after.tar.enc"
    rotated.encrypt_file(plaintext, new_bundle)
    only_new = BackupEncryptor(
        provider=StaticSecretsProvider(values={key_name: _NEW}), vault_key_name=key_name
    )
    assert only_new.decrypt_bytes(new_bundle.read_bytes()) == plaintext.read_bytes()

    # ...and dropping the old key is what makes the OLD bundles unreadable, with
    # a message that names the missing key id instead of crying corruption.
    with pytest.raises(BackupEncryptionError) as excinfo:
        only_new.decrypt_bytes(old_bundle.read_bytes())
    assert "not in the configured ring" in str(excinfo.value)


# ===========================================================================
# Phase D — the rotation job
# ===========================================================================
class InvalidPath(Exception):  # noqa: N818 - the name IS the contract, see below
    """Stands in for ``hvac.exceptions.InvalidPath``.

    The adapter distinguishes "absent KV path" from "Vault is down" by matching
    the exception's CLASS NAME (so it never imports hvac). A double called
    ``InvalidPathError`` would therefore exercise the wrong branch, which is why
    the ruff naming rule is suppressed here rather than obeyed.
    """


class _DrillVault:
    """A whole fake Vault: KV v2 + database secrets engine + sys leases."""

    def __init__(self) -> None:
        self.kv: dict[str, dict[str, str]] = {}
        self.versions: dict[str, int] = {}
        self.roles: dict[str, dict[str, Any]] = {}
        self.revoked_leases: list[str] = []
        outer = self

        class _KvV2:
            def create_or_update_secret(
                self, *, mount_point: str, path: str, secret: dict[str, str]
            ) -> dict[str, Any]:
                outer.kv[path] = dict(secret)
                outer.versions[path] = outer.versions.get(path, 0) + 1
                return {"data": {"version": outer.versions[path]}}

            def read_secret_version(self, *, mount_point: str, path: str) -> dict[str, Any]:
                if path not in outer.kv:
                    raise InvalidPath(path)
                return {"data": {"data": dict(outer.kv[path])}}

        class _Database:
            def create_role(self, **kwargs: Any) -> None:
                outer.roles[kwargs["name"]] = kwargs

            def read_role(self, *, name: str, mount_point: str) -> dict[str, Any]:
                cfg = outer.roles[name]
                return {
                    "data": {
                        "db_name": cfg["db_name"],
                        "default_ttl": cfg["default_ttl"],
                        "max_ttl": cfg["max_ttl"],
                        "creation_statements": cfg["creation_statements"],
                    }
                }

            def generate_credentials(self, *, name: str, mount_point: str) -> dict[str, Any]:
                return {
                    "lease_id": f"database/creds/{name}/lease-1",
                    "lease_duration": 3600,
                    "data": {"username": "v-tmp", "password": "generated-by-vault"},
                }

        class _Sys:
            def renew_lease(self, *, lease_id: str, increment: int) -> dict[str, Any]:
                return {"lease_duration": increment}

            def revoke_lease(self, *, lease_id: str) -> None:
                outer.revoked_leases.append(lease_id)

        self.secrets = type(
            "S", (), {"kv": type("K", (), {"v2": _KvV2()})(), "database": _Database()}
        )()
        self.sys = _Sys()


class _DrillMinio:
    def __init__(self) -> None:
        self.live: list[str] = []
        self.revoked: list[str] = []
        self._n = 0

    def mint(self) -> tuple[str, str]:
        self._n += 1
        access = f"DRILLACCESSKEY{self._n:06d}"
        self.live.append(access)
        return access, f"drill-secret-{self._n}"

    def revoke(self, access_key: str) -> None:
        self.live.remove(access_key)
        self.revoked.append(access_key)


class _DrillNotifier:
    def __init__(self) -> None:
        self.alerts: list[Any] = []

    def alert_failure(self, audit: Any) -> None:
        self.alerts.append(audit)


def _worker_settings() -> WorkerSettings:
    """Worker settings with an unreachable DB so the enable-flag read falls back
    to "enabled" — the branch we want — without needing a live database."""
    return WorkerSettings(
        database_url="postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none",
        cred_rotation_static_secrets=["minio", "jwt"],
    )


def test_phase_d_without_vault_the_job_skips_loudly_and_rotates_nothing() -> None:
    """Runbook §job, row `skipped`. The pre-prod-05 behaviour was a SUCCEEDED
    audit against an in-memory dict — a weekly lie the runbook then pointed
    emergency revocation at."""
    notifier = _DrillNotifier()
    summary = asyncio.run(_rotate_credentials(_worker_settings(), client=None, notifier=notifier))

    assert summary["status"] == str(RotationStatus.SKIPPED)
    assert summary["ok"] is False
    assert summary["alerted"] is True
    assert len(notifier.alerts) == 1


def test_phase_d_with_vault_the_job_rotates_marks_pending_apply_and_only_then_revokes() -> None:
    """Runbook §8, the whole add-then-remove sequence.

    The ordering assertions are the substance: after the cycle BOTH MinIO
    credentials exist (nothing has been cut off), and the old one disappears only
    when the explicit propagation step runs.
    """
    vault = _DrillVault()
    minio = _DrillMinio()
    client = HvacVaultRotationClient(vault, minio_rotator=minio)
    notifier = _DrillNotifier()

    # First cycle: no previous credential to replace.
    first = asyncio.run(_rotate_credentials(_worker_settings(), client=client, notifier=notifier))
    assert first["ok"] is True, first
    assert first["status"] == str(RotationStatus.SUCCEEDED)
    assert sorted(first["static_secrets"]) == ["jwt", "minio"]
    assert vault.kv["platform/minio"][KV_FIELD_PENDING_APPLY] == "true"
    assert vault.kv["platform/jwt"][KV_FIELD_PENDING_APPLY] == "true"
    first_access_key = vault.kv["platform/minio"][KV_FIELD_ACCESS_KEY]

    # Second cycle: now there IS a previous credential.
    second = asyncio.run(_rotate_credentials(_worker_settings(), client=client, notifier=notifier))
    assert second["ok"] is True
    assert second["static_secret_versions"]["minio"] == 2, "the KV version must advance"

    # Both credentials are live: no cut-over window.
    assert len(minio.live) == 2
    assert minio.revoked == []

    # --- propagation happened out of band; NOW revoke -------------------------
    revoked = revoke_previous_minio_credential(client, minio, path="platform/minio")
    assert revoked == first_access_key
    assert minio.revoked == [first_access_key]
    assert minio.live == [vault.kv["platform/minio"][KV_FIELD_ACCESS_KEY]]
    assert vault.kv["platform/minio"][KV_FIELD_PENDING_APPLY] == "false"

    # Nothing alerted: a healthy cycle is a silent one.
    assert notifier.alerts == []


def test_phase_d_a_rotated_jwt_value_is_usable_as_the_head_of_the_ring() -> None:
    """Closes the loop between phase D and phase A: the value the job writes to
    Vault has to be something the api-server can actually be configured with —
    long enough for the HMAC floor, and usable as a signing key.

    Without this, "rotate JWT" would be two disconnected features: a job that
    writes a value and a ring that accepts one, with nobody checking they fit.
    """
    vault = _DrillVault()
    client = HvacVaultRotationClient(vault, minio_rotator=_DrillMinio())
    client.rotate_static_secret(path="platform/jwt", mount="secret")
    rotated_value = vault.kv["platform/jwt"]["value"]

    assert len(rotated_value) >= 32, "shorter than the staging/prod HMAC floor"
    settings = Settings(
        environment="dev",
        jwt_secret="unused-because-the-list-wins",
        jwt_secrets=f"{rotated_value},{_OLD}",
    )
    assert settings.jwt_secret_ring[0] == rotated_value
    token = jwt_mod.sign_claims(
        {
            "sub": str(uuid4()),
            "sid": str(uuid4()),
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        },
        secret=settings.jwt_secret_ring[0],
        algorithm=settings.jwt_algorithm,
    )
    assert jwt_mod.verify_claims_any(
        token, secrets=settings.jwt_secret_ring, algorithm=settings.jwt_algorithm
    )["sid"]
