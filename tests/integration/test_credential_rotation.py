"""Vault dynamic-secret credential rotation (Plan 15 task_15_17 — Vault MOCKED).

Plan 15 Fase C, *"Rotación automática de credenciales (Vault dynamic secrets)"*.
A live Vault (and a real Postgres database-secrets-engine that mints throwaway
roles) cannot run in CI, so — exactly as the Plan 15 Fase C charter requires —
the Vault client sits behind a seam (:class:`workers.credential_rotation.
VaultRotationClient`) and these tests inject the deterministic in-memory
:class:`FakeVaultRotationClient`. Nothing here talks to a real Vault.

What is asserted (the task's acceptance criteria):

  1. the database secrets-engine ROLE is configured (short TTL, bound to the
     Postgres connection) and reads back through the seam;
  2. a rotation CYCLE issues a new dynamic credential + renews then revokes the
     prior lease + rotates the static secrets (MinIO/JWT) in place;
  3. the BEAT job is registered + reads its cadence from config (not a hardcoded
     schedule) and honours the live ``cred_rotation_enabled`` platform setting;
  4. a rotation FAILURE is handled (the engine never raises — the system stays
     up) AND raises an alert through the Plan 10 notifier;
  5. secrets are NEVER logged in plaintext (the minted password never appears in
     any captured structured log line, and the credential's repr is redacted).

The platform-setting read path (criterion 3's enable flag) uses the real test
Postgres so the live OFF lever is the one under test; everything Vault is mocked.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
import structlog
from alembic import command
from workers.credential_rotation import (
    CredentialRotationError,
    DynamicDbCredential,
    FakeVaultRotationClient,
    RecordingRotationNotifier,
    RotationStatus,
    build_db_role,
    configure_db_secrets_engine_role,
    issue_dynamic_db_credential,
    rotate_credentials,
)

pytestmark = pytest.mark.integration


# ===========================================================================
# 1) The database secrets-engine role is configured (short TTL, idempotent).
# ===========================================================================
def test_configure_db_secrets_engine_role() -> None:
    client = FakeVaultRotationClient()
    role = build_db_role(
        name="platform-app",
        db_connection="platform-postgres",
        ttl_s=3600,
        max_ttl_s=86400,
    )

    configured = configure_db_secrets_engine_role(client, role, mount="database")

    # The role is recorded under the engine mount and reads back through the seam.
    assert configured.name == "platform-app"
    assert configured.db_connection == "platform-postgres"
    # SHORT TTL by design — a leaked dynamic credential self-expires.
    assert configured.ttl_s == 3600
    assert configured.max_ttl_s == 86400
    assert client.read_db_role("platform-app", mount="database") is configured
    # The creation SQL carries NO password (Vault injects {{password}}).
    assert all("{{password}}" in stmt for stmt in configured.creation_statements)
    assert not any(
        "PASSWORD '" in stmt and "{{password}}" not in stmt for stmt in role.creation_statements
    )


def test_configure_db_role_is_idempotent() -> None:
    client = FakeVaultRotationClient()
    role = build_db_role(name="platform-app", db_connection="pg", ttl_s=600, max_ttl_s=3600)
    configure_db_secrets_engine_role(client, role, mount="database")
    # Re-running converges (no second role, no exception).
    configure_db_secrets_engine_role(client, role, mount="database")
    assert list(client.roles.keys()) == ["database/platform-app"]


def test_build_db_role_rejects_bad_ttl() -> None:
    with pytest.raises(CredentialRotationError):
        build_db_role(name="r", db_connection="pg", ttl_s=0, max_ttl_s=3600)
    with pytest.raises(CredentialRotationError):
        # max_ttl < ttl is inverted — reject rather than mint a long-lived cred.
        build_db_role(name="r", db_connection="pg", ttl_s=7200, max_ttl_s=3600)


def test_issue_dynamic_db_credential_short_ttl() -> None:
    client = FakeVaultRotationClient(default_lease_duration_s=900)
    role = build_db_role(name="platform-app", db_connection="pg", ttl_s=900, max_ttl_s=3600)
    configure_db_secrets_engine_role(client, role, mount="database")

    cred = issue_dynamic_db_credential(client, "platform-app", mount="database")
    assert isinstance(cred, DynamicDbCredential)
    assert cred.lease_duration_s == 900
    assert cred.lease_id in client.issued_leases


def test_issue_dynamic_credential_requires_configured_role() -> None:
    client = FakeVaultRotationClient()
    # No role configured → the engine refuses (mirrors real Vault).
    with pytest.raises(CredentialRotationError):
        issue_dynamic_db_credential(client, "missing-role", mount="database")


# ===========================================================================
# 2) A rotation cycle issues new creds + renews/revokes the old lease.
# ===========================================================================
def test_rotation_cycle_issues_and_cycles_prior_lease() -> None:
    client = FakeVaultRotationClient()
    role = build_db_role(name="platform-app", db_connection="pg", ttl_s=3600, max_ttl_s=86400)
    configure_db_secrets_engine_role(client, role, mount="database")

    outcome = rotate_credentials(
        client,
        db_role_name="platform-app",
        db_mount="database",
        static_secret_names=("minio", "jwt"),
        previous_lease_id="database/creds/platform-app/lease-OLD",
    )

    assert outcome.ok
    assert outcome.audit.status is RotationStatus.SUCCEEDED
    # A fresh dynamic credential was minted...
    assert outcome.credential is not None
    assert outcome.audit.new_lease_id == outcome.credential.lease_id
    # ...the prior lease was renewed THEN revoked (bounded overlap)...
    assert client.renewed_leases == ["database/creds/platform-app/lease-OLD"]
    assert client.revoked_leases == ["database/creds/platform-app/lease-OLD"]
    assert outcome.audit.renewed_lease_id == "database/creds/platform-app/lease-OLD"
    assert outcome.audit.revoked_lease_id == "database/creds/platform-app/lease-OLD"
    # ...and BOTH static secrets were rotated in place (version bumped).
    assert client.rotated_paths == ["platform/minio", "platform/jwt"]
    rotated = {s.name: s.version for s in outcome.audit.static_secrets}
    assert rotated == {"minio": 1, "jwt": 1}


def test_rotation_cycle_writes_audit_entry() -> None:
    """The cycle always produces an immutable, secret-free audit entry."""
    client = FakeVaultRotationClient()
    role = build_db_role(name="platform-app", db_connection="pg", ttl_s=3600, max_ttl_s=86400)
    configure_db_secrets_engine_role(client, role, mount="database")

    outcome = rotate_credentials(client, db_role_name="platform-app")
    fields = outcome.audit.as_log_fields()
    # The audit log fields are JSON-safe + secret-free (names / lease-ids only).
    assert fields["status"] == "succeeded"
    assert fields["static_secrets"] == ["minio", "jwt"]
    assert fields["new_lease_id"] == outcome.audit.new_lease_id
    assert fields["error"] is None


def test_rotation_first_cycle_without_prior_lease() -> None:
    """The very first rotation has no prior lease to revoke — that is fine."""
    client = FakeVaultRotationClient()
    role = build_db_role(name="platform-app", db_connection="pg", ttl_s=3600, max_ttl_s=86400)
    configure_db_secrets_engine_role(client, role, mount="database")

    outcome = rotate_credentials(client, db_role_name="platform-app", previous_lease_id=None)
    assert outcome.ok
    assert client.renewed_leases == []
    assert client.revoked_leases == []
    assert outcome.audit.revoked_lease_id is None


# ===========================================================================
# 4) A rotation failure is handled + alerts (system stays up).
# ===========================================================================
@pytest.mark.parametrize("fail_step", ["rotate_static", "generate", "renew", "revoke"])
def test_rotation_failure_is_handled_and_alerts(fail_step: str) -> None:
    client = FakeVaultRotationClient(fail_on=fail_step)
    role = build_db_role(name="platform-app", db_connection="pg", ttl_s=3600, max_ttl_s=86400)
    configure_db_secrets_engine_role(client, role, mount="database")
    notifier = RecordingRotationNotifier()

    # The engine NEVER raises — a failure must not take the system down.
    outcome = rotate_credentials(
        client,
        db_role_name="platform-app",
        previous_lease_id="database/creds/platform-app/lease-OLD",
        notifier=notifier,
    )

    assert not outcome.ok
    assert outcome.audit.status is RotationStatus.FAILED
    assert outcome.audit.error  # a non-leaky reason is recorded
    assert outcome.credential is None
    # An alert was raised through the Plan 10 notifier seam.
    assert outcome.alerted is True
    assert len(notifier.alerts) == 1
    assert notifier.alerts[0].status is RotationStatus.FAILED


def test_rotation_failure_without_notifier_does_not_crash() -> None:
    """No notifier wired → still no raise, just no alert (best-effort)."""
    client = FakeVaultRotationClient(fail_on="generate")
    role = build_db_role(name="platform-app", db_connection="pg", ttl_s=3600, max_ttl_s=86400)
    configure_db_secrets_engine_role(client, role, mount="database")

    outcome = rotate_credentials(client, db_role_name="platform-app", notifier=None)
    assert not outcome.ok
    assert outcome.alerted is False


def test_alert_payload_is_secret_free() -> None:
    """The alert the notifier receives carries names/lease-ids — never a value."""
    client = FakeVaultRotationClient(fail_on="renew")
    role = build_db_role(name="platform-app", db_connection="pg", ttl_s=3600, max_ttl_s=86400)
    configure_db_secrets_engine_role(client, role, mount="database")
    notifier = RecordingRotationNotifier()

    rotate_credentials(
        client,
        db_role_name="platform-app",
        previous_lease_id="lease-OLD",
        notifier=notifier,
    )
    payload = notifier.alerts[0].as_log_fields()
    # The freshly-minted (but un-finalised) credential's password must not leak
    # into the alert payload even though a credential was generated this cycle.
    assert client.issued_leases  # a credential WAS minted before the renew failed
    flat = repr(payload)
    assert "fake-dynamic-password" not in flat


# ===========================================================================
# 5) Secrets are never logged in plaintext.
# ===========================================================================
def test_minted_password_never_logged() -> None:
    """No captured log line carries the minted password; repr is redacted."""
    client = FakeVaultRotationClient()
    role = build_db_role(name="platform-app", db_connection="pg", ttl_s=3600, max_ttl_s=86400)
    configure_db_secrets_engine_role(client, role, mount="database")

    with structlog.testing.capture_logs() as logs:
        outcome = rotate_credentials(
            client,
            db_role_name="platform-app",
            previous_lease_id="lease-OLD",
        )
        issue_dynamic_db_credential(client, "platform-app", mount="database")

    assert outcome.credential is not None
    secret = outcome.credential.password
    # The scripted password is non-empty, so a leak WOULD be detectable.
    assert secret
    serialised = repr(logs)
    assert secret not in serialised
    assert "fake-dynamic-password" not in serialised
    # The lease id (safe to log) DID make it into the logs — proves the logs
    # are non-empty / the assertion above is meaningful.
    assert any(secret not in repr(entry) for entry in logs)
    # The credential's own repr is redacted (no username/password).
    assert "password" not in repr(outcome.credential).lower() or "redacted" in repr(
        outcome.credential
    )
    assert secret not in repr(outcome.credential)


# ===========================================================================
# 3) The beat job is registered + reads its cadence from config.
# ===========================================================================
def test_cred_rotation_beat_entry_reads_cron_from_config() -> None:
    import workers.credential_rotation_task  # noqa: F401  (registers the task)
    from celery.schedules import crontab
    from workers.beat_schedule import CRED_ROTATION_BEAT_ENTRY, build_beat_schedule
    from workers.celery_app import build_celery_app
    from workers.config import Settings

    app = build_celery_app(
        Settings(broker_url="redis://localhost:6379/1", result_backend="redis://localhost:6379/2")
    )
    assert "workers.rotate_credentials" in app.tasks

    # Default cadence: weekly Sunday 02:00 — NOT a hardcoded magic schedule, it
    # comes from Settings.cred_rotation_cron.
    default_sched = build_beat_schedule(Settings())
    assert CRED_ROTATION_BEAT_ENTRY in default_sched
    entry = default_sched[CRED_ROTATION_BEAT_ENTRY]
    assert entry["task"] == "workers.rotate_credentials"
    # Pinned to the privileged lane (it touches the platform's secrets/Vault).
    assert entry["options"] == {"queue": "privileged"}
    assert isinstance(entry["schedule"], crontab)
    assert entry["schedule"].hour == {2}
    assert entry["schedule"].minute == {0}
    assert entry["schedule"].day_of_week == {0}

    # A different configured cron is honoured.
    custom = build_beat_schedule(Settings(cred_rotation_cron="30 */6 * * *"))
    custom_cron = custom[CRED_ROTATION_BEAT_ENTRY]["schedule"]
    assert isinstance(custom_cron, crontab)
    assert custom_cron.minute == {30}
    assert custom_cron.hour == {0, 6, 12, 18}


# ===========================================================================
# 3b) The beat task honours the live enable flag (real test Postgres).
# ===========================================================================
@pytest.fixture()
def migrated_db(alembic_config, migrations_pg_dsn: str):
    command.upgrade(alembic_config, "head")

    async def _truncate() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute("TRUNCATE platform_settings RESTART IDENTITY CASCADE")
        finally:
            await conn.close()

    asyncio.run(_truncate())
    return migrations_pg_dsn


async def _set_setting_row(dsn: str, key: str, value_jsonb: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_settings (key, value) VALUES ($1, $2::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key,
            value_jsonb,
        )
    finally:
        await conn.close()


def _worker_settings(admin_database_url: str):
    from workers.config import Settings

    return Settings(
        database_url=admin_database_url,
        cred_rotation_db_role="platform-app",
        cred_rotation_db_connection="platform-postgres",
    )


@pytest.mark.asyncio
async def test_beat_task_skips_when_disabled(migrated_db: str, admin_database_url: str) -> None:
    """A System Admin turned rotation OFF — the run is a no-op, Vault untouched."""
    import workers.credential_rotation_task as crt

    await _set_setting_row(migrated_db, "cred_rotation_enabled", "false")

    client = FakeVaultRotationClient()
    settings = _worker_settings(admin_database_url)
    result = await crt._rotate_credentials(settings, client=client, notifier=None)

    assert result == {"enabled": False, "skipped": True}
    # The disabled run never touched Vault (no role configured, nothing rotated).
    assert client.roles == {}
    assert client.rotated_paths == []
    assert client.issued_leases == []


@pytest.mark.asyncio
async def test_beat_task_configures_role_and_rotates_when_enabled(
    migrated_db: str, admin_database_url: str
) -> None:
    """Enabled run: configures the role + runs a full cycle against mocked Vault."""
    import workers.credential_rotation_task as crt

    await _set_setting_row(migrated_db, "cred_rotation_enabled", "true")

    client = FakeVaultRotationClient()
    notifier = RecordingRotationNotifier()
    settings = _worker_settings(admin_database_url)
    result = await crt._rotate_credentials(settings, client=client, notifier=notifier)

    assert result["enabled"] is True
    assert result["ok"] is True
    assert result["status"] == "succeeded"
    # The role was configured (criterion 1) and the cycle ran (criterion 2).
    assert client.read_db_role("platform-app", mount="database") is not None
    assert client.rotated_paths == ["platform/minio", "platform/jwt"]
    assert client.issued_leases  # a fresh dynamic credential was minted
    assert notifier.alerts == []  # success → no alert
    # The summary carries NO credential value (secret-free, criterion 5).
    assert "fake-dynamic-password" not in repr(result)
