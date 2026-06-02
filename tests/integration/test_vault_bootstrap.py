"""Vault bootstrap — init + unseal + KV v2 + policies (Plan 15 task_15_09).

Exercises the Phase-B Vault bootstrap orchestration
(:mod:`installer_backend.vault_bootstrap`) with Vault MOCKED behind the
``hvac``-like :class:`installer_backend.vault_bootstrap.VaultClient` seam (the
in-memory :class:`FakeVaultClient`) — NO real ``vault operator init`` / unseal /
mount runs. The real hvac adapter is exercised only by the plan's Tests Humanos.

Coverage (per the task contract):
  * ``init`` returns the Shamir unseal keys + root token EXACTLY ONCE;
  * ``unseal`` applies the threshold (a sealed vault becomes unsealed);
  * KV v2 is enabled at the platform mount (``secret/``);
  * the expected per-service policies are written with the right (least-priv,
    read-only) capabilities, scoped to the paths the service resolves;
  * the unseal keys / root token are NEVER logged nor persisted in plaintext
    (redacted ``repr``; nothing written to disk; nothing in the logs);
  * a re-bootstrap of an already-initialised vault is DETECTED — no double-init.
"""

from __future__ import annotations

import logging

import pytest
from installer_backend.vault_bootstrap import (
    PLATFORM_KV_MOUNT,
    SECRET_PATH_DATABASE,
    SECRET_PATH_ENCRYPTION,
    SECRET_PATH_JWT,
    SECRET_PATH_LLM,
    SECRET_PATH_MINIO,
    FakeVaultClient,
    VaultBootstrapError,
    VaultBootstrapResult,
    VaultInitResult,
    bootstrap_vault,
    initial_policies,
)

pytestmark = pytest.mark.integration


# Sentinel secret values we assert never reach disk/logs.
_ROOT_TOKEN = "s.r00t-token-never-leaks"
_UNSEAL_KEYS = (
    "unseal-share-1-never-leaks",
    "unseal-share-2-never-leaks",
    "unseal-share-3-never-leaks",
    "unseal-share-4-never-leaks",
    "unseal-share-5-never-leaks",
)
_ALL_SECRETS = (_ROOT_TOKEN, *_UNSEAL_KEYS)


def _fresh_client() -> FakeVaultClient:
    """A fresh (uninitialised + sealed) fake vault with sentinel material."""

    return FakeVaultClient(
        scripted_unseal_keys=_UNSEAL_KEYS,
        scripted_root_token=_ROOT_TOKEN,
    )


# ---------------------------------------------------------------------------
# init — unseal keys + root token returned ONCE.
# ---------------------------------------------------------------------------
def test_init_returns_unseal_keys_and_root_token() -> None:
    client = _fresh_client()
    result = bootstrap_vault(client, key_shares=5, key_threshold=3)

    assert result.init is not None
    assert result.init.root_token == _ROOT_TOKEN
    assert result.init.unseal_keys == _UNSEAL_KEYS
    assert result.init.key_threshold == 3
    # This run initialised the vault (not a re-bootstrap).
    assert result.already_initialized is False


def test_init_happens_exactly_once_in_a_run() -> None:
    client = _fresh_client()
    bootstrap_vault(client)
    # operator init was called exactly once.
    assert client.init_calls == 1


# ---------------------------------------------------------------------------
# unseal — the threshold is applied; a sealed vault becomes unsealed.
# ---------------------------------------------------------------------------
def test_unseal_applies_the_threshold() -> None:
    client = _fresh_client()
    assert client.is_sealed() is True

    bootstrap_vault(client, key_shares=5, key_threshold=3)

    # The vault is unsealed after the bootstrap applies the threshold shares.
    assert client.is_sealed() is False


def test_unseal_raises_when_not_enough_keys_reach_threshold() -> None:
    # A vault whose accepted keys never reach the threshold stays sealed → error.
    client = FakeVaultClient(
        scripted_unseal_keys=("only-one-share",),
        scripted_root_token=_ROOT_TOKEN,
        key_shares=1,
    )
    with pytest.raises(VaultBootstrapError):
        # threshold 3 but only 1 share available.
        bootstrap_vault(client, key_shares=1, key_threshold=3)


def test_invalid_threshold_is_rejected() -> None:
    client = _fresh_client()
    with pytest.raises(VaultBootstrapError):
        bootstrap_vault(client, key_shares=5, key_threshold=6)


# ---------------------------------------------------------------------------
# KV v2 enabled at the platform mount.
# ---------------------------------------------------------------------------
def test_kv_v2_enabled_at_platform_mount() -> None:
    client = _fresh_client()
    result = bootstrap_vault(client)

    assert result.kv_mount == PLATFORM_KV_MOUNT
    assert result.kv_enabled is True
    # The KV engine is mounted at secret/ as version 2.
    assert client.mounts[f"{PLATFORM_KV_MOUNT}/"] == "kv"
    assert client.mount_kv_versions[f"{PLATFORM_KV_MOUNT}/"] == "2"


def test_kv_mount_is_idempotent_when_already_present() -> None:
    client = _fresh_client()
    # Pretend the mount already exists (e.g. partial prior run).
    client.mounts[f"{PLATFORM_KV_MOUNT}/"] = "kv"
    result = bootstrap_vault(client)
    # Nothing newly enabled, but no failure either.
    assert result.kv_enabled is False


def test_kv_mount_conflict_is_an_error() -> None:
    client = _fresh_client()
    # A non-KV engine squatting the platform mount must fail loudly.
    client.mounts[f"{PLATFORM_KV_MOUNT}/"] = "transit"
    with pytest.raises(VaultBootstrapError, match="KV"):
        bootstrap_vault(client)


# ---------------------------------------------------------------------------
# Policies — expected names + least-privilege capabilities + scoped paths.
# ---------------------------------------------------------------------------
def test_expected_policies_written() -> None:
    client = _fresh_client()
    result = bootstrap_vault(client)

    expected = {"api-server", "workers", "orchestrator", "notification-dispatcher"}
    assert set(result.policies_written) == expected
    assert set(client.policies) == expected


def test_policies_are_read_only_least_privilege() -> None:
    # Every grant in every initial policy is read-only — services consume
    # secrets, they never write them.
    for policy in initial_policies():
        for rule in policy.rules:
            assert rule.capabilities == ("read",), (policy.name, rule.path)


def test_api_server_policy_scopes_every_secret_domain_it_resolves() -> None:
    client = _fresh_client()
    bootstrap_vault(client)
    hcl = client.policies["api-server"]
    mount = PLATFORM_KV_MOUNT
    # api-server resolves DB, MinIO, JWT, encryption keys + LLM provider creds.
    for path in (
        SECRET_PATH_DATABASE,
        SECRET_PATH_MINIO,
        SECRET_PATH_JWT,
        SECRET_PATH_ENCRYPTION,
        SECRET_PATH_LLM,
    ):
        assert f'path "{mount}/data/{path}"' in hcl, path
    assert 'capabilities = ["read"]' in hcl


def test_orchestrator_policy_is_database_only() -> None:
    client = _fresh_client()
    bootstrap_vault(client)
    hcl = client.policies["orchestrator"]
    mount = PLATFORM_KV_MOUNT
    assert f'path "{mount}/data/{SECRET_PATH_DATABASE}"' in hcl
    # The orchestrator schedules; it must NOT read JWT / MinIO / encryption / LLM.
    for path in (SECRET_PATH_JWT, SECRET_PATH_MINIO, SECRET_PATH_ENCRYPTION, SECRET_PATH_LLM):
        assert f'path "{mount}/data/{path}"' not in hcl, path


def test_dispatcher_reads_encryption_key_not_minio() -> None:
    client = _fresh_client()
    bootstrap_vault(client)
    hcl = client.policies["notification-dispatcher"]
    mount = PLATFORM_KV_MOUNT
    # It reads the notification encryption key (read side of the write/read pair)
    # + the DB DSN, but not MinIO / JWT / LLM creds.
    assert f'path "{mount}/data/{SECRET_PATH_ENCRYPTION}"' in hcl
    assert f'path "{mount}/data/{SECRET_PATH_DATABASE}"' in hcl
    assert f'path "{mount}/data/{SECRET_PATH_MINIO}"' not in hcl
    assert f'path "{mount}/data/{SECRET_PATH_LLM}"' not in hcl


def test_policy_hcl_carries_no_secret() -> None:
    # Policy documents grant paths/capabilities only — never a secret value.
    client = _fresh_client()
    bootstrap_vault(client)
    for hcl in client.policies.values():
        for secret in _ALL_SECRETS:
            assert secret not in hcl


# ---------------------------------------------------------------------------
# Re-bootstrap detection — no double-init.
# ---------------------------------------------------------------------------
def test_rebootstrap_of_initialized_vault_does_not_reinit() -> None:
    client = _fresh_client()
    first = bootstrap_vault(client)
    assert first.already_initialized is False
    assert client.init_calls == 1

    # Re-run against the SAME (now initialised, unsealed) vault.
    second = bootstrap_vault(client)
    assert second.already_initialized is True
    # No second operator-init — the no-double-init guarantee.
    assert client.init_calls == 1
    # And no init material is handed out on a re-bootstrap (no recovery).
    assert second.init is None


def test_rebootstrap_reconciles_policies_idempotently() -> None:
    client = _fresh_client()
    bootstrap_vault(client)
    # Drop a policy to simulate drift, then re-bootstrap.
    del client.policies["workers"]
    second = bootstrap_vault(client)
    # The missing policy is rewritten on the re-bootstrap.
    assert "workers" in client.policies
    assert "workers" in second.policies_written


def test_rebootstrap_of_sealed_vault_needs_existing_unseal_keys() -> None:
    # Model an already-initialised vault that is sealed (e.g. after a restart).
    client = FakeVaultClient(scripted_root_token=_ROOT_TOKEN)
    client.preset_initialized(sealed=True, unseal_keys=_UNSEAL_KEYS)

    # Without the operator's stored keys we cannot unseal — and there is no
    # recovery of the originals → explicit error, not a silent failure.
    with pytest.raises(VaultBootstrapError, match="unseal"):
        bootstrap_vault(client)

    # With the existing keys the re-bootstrap unseals + reconciles (no re-init).
    result = bootstrap_vault(client, existing_unseal_keys=_UNSEAL_KEYS)
    assert result.already_initialized is True
    assert result.init is None
    assert client.is_sealed() is False
    assert client.init_calls == 0


# ---------------------------------------------------------------------------
# Secrets never logged / never persisted in plaintext.
# ---------------------------------------------------------------------------
def test_init_and_bootstrap_results_repr_are_redacted() -> None:
    client = _fresh_client()
    result = bootstrap_vault(client)
    assert result.init is not None

    # A stray log/traceback of either result must not leak the values.
    for rendered in (repr(result), str(result), repr(result.init), str(result.init)):
        assert _ROOT_TOKEN not in rendered
        for key in _UNSEAL_KEYS:
            assert key not in rendered
        assert "redacted" in rendered.lower()


def test_bootstrap_emits_no_secret_to_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client = _fresh_client()
    bootstrap_vault(client)

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    for secret in _ALL_SECRETS:
        assert secret not in log_text


def test_bootstrap_writes_nothing_to_disk(tmp_path, monkeypatch) -> None:
    """The bootstrap itself performs NO file I/O — assert the cwd stays clean."""

    monkeypatch.chdir(tmp_path)
    client = _fresh_client()
    bootstrap_vault(client)

    leaked = []
    for path in tmp_path.rglob("*"):
        if path.is_file():
            content = path.read_text(encoding="utf-8", errors="ignore")
            for secret in _ALL_SECRETS:
                if secret in content:
                    leaked.append((path, secret))
    assert leaked == []


# ---------------------------------------------------------------------------
# Result types / typing smoke.
# ---------------------------------------------------------------------------
def test_result_types() -> None:
    client = _fresh_client()
    result = bootstrap_vault(client)
    assert isinstance(result, VaultBootstrapResult)
    assert isinstance(result.init, VaultInitResult)
