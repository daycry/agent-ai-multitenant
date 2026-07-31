"""The rotation job stops lying (prod-05 task_prod05_05/07, gap2-1 + gap2-2).

Before this task ``_build_vault_client`` returned an in-memory
``FakeVaultRotationClient`` **unconditionally**, so the weekly beat job audited
``status=SUCCEEDED, ok=true`` while rotating nothing — and the canonical runbook
routed EMERGENCY REVOCATION at that job. A leaked credential's documented remedy
was a no-op that reported success. That is the single worst kind of bug: not a
missing feature, a feature that CLAIMS to have run.

The assertions are therefore mostly about what must NOT happen:

* the production resolver must never hand back the fake — asserted twice, once
  behaviourally and once as a static guard over the module's source, because the
  behavioural test alone would pass again the day someone reintroduces the
  fallback under a different name;
* no Vault must produce ``SKIPPED`` **with an alert**, never ``SUCCEEDED``, and
  never a silent log line;
* rotating ``minio`` must touch MinIO BEFORE writing KV, and must refuse
  outright if it cannot (a KV entry naming a credential MinIO never issued sends
  every service into a restart onto a credential that does not authenticate);
* the old credential is revoked only in a SEPARATE, later step — revoking before
  the new value has propagated takes object storage down platform-wide.

No hvac and no MinIO are needed: both are behind seams, and the doubles here
record their call ORDER, which is the property that matters.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from workers.config import Settings
from workers.credential_rotation import (
    CredentialRotationError,
    DbSecretsEngineRole,
    RotationAudit,
    RotationStatus,
    VaultRotationClient,
)
from workers.credential_rotation_hvac import (
    KV_FIELD_ACCESS_KEY,
    KV_FIELD_PENDING_APPLY,
    KV_FIELD_PREVIOUS_ACCESS_KEY,
    KV_FIELD_SECRET_KEY,
    KV_FIELD_VALUE,
    HvacVaultRotationClient,
    revoke_previous_minio_credential,
)
from workers.credential_rotation_task import _build_vault_client, _rotate_credentials

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Doubles: an hvac-shaped KV v2 and a MinIO admin that records its calls
# ---------------------------------------------------------------------------
class _FakeKvV2:
    def __init__(self, store: dict[str, dict[str, str]], journal: list[str]) -> None:
        self._store = store
        self._journal = journal
        self._versions: dict[str, int] = {}

    def create_or_update_secret(
        self, *, mount_point: str, path: str, secret: dict[str, str]
    ) -> dict[str, Any]:
        self._journal.append(f"kv_write:{path}")
        self._store[path] = dict(secret)
        self._versions[path] = self._versions.get(path, 0) + 1
        return {"data": {"version": self._versions[path]}}

    def read_secret_version(self, *, mount_point: str, path: str) -> dict[str, Any]:
        if path not in self._store:
            raise InvalidPath(path)
        return {"data": {"data": dict(self._store[path])}}


class InvalidPath(Exception):  # noqa: N818 - the name IS the contract
    """Stands in for ``hvac.exceptions.InvalidPath``.

    Named EXACTLY like hvac's class on purpose: the adapter detects "absent KV
    path" by class name (so it never imports hvac), and a double named anything
    else would not exercise that detection at all.
    """


class _FakeHvacClient:
    def __init__(self, journal: list[str] | None = None) -> None:
        self.journal: list[str] = journal if journal is not None else []
        self.kv_store: dict[str, dict[str, str]] = {}
        kv_v2 = _FakeKvV2(self.kv_store, self.journal)
        self.secrets = type("Secrets", (), {"kv": type("Kv", (), {"v2": kv_v2})()})()


class _RecordingMinio:
    """A MinIO admin double that records mint/revoke, in order."""

    def __init__(self, journal: list[str], *, fail_on_mint: bool = False) -> None:
        self.journal = journal
        self.fail_on_mint = fail_on_mint
        self.minted: list[str] = []
        self.revoked: list[str] = []
        self._n = 0

    def mint(self) -> tuple[str, str]:
        if self.fail_on_mint:
            raise CredentialRotationError("MinIO admin API unreachable")
        self._n += 1
        access = f"ROTATEDACCESSKEY{self._n:04d}"
        self.journal.append(f"minio_mint:{access}")
        self.minted.append(access)
        return access, f"rotated-secret-{self._n}"

    def revoke(self, access_key: str) -> None:
        self.journal.append(f"minio_revoke:{access_key}")
        self.revoked.append(access_key)


def _client(
    *, minio: _RecordingMinio | None = None, journal: list[str] | None = None
) -> tuple[HvacVaultRotationClient, _FakeHvacClient]:
    hvac_client = _FakeHvacClient(journal)
    return HvacVaultRotationClient(hvac_client, minio_rotator=minio), hvac_client


# ---------------------------------------------------------------------------
# 1. The resolver never returns the fake
# ---------------------------------------------------------------------------
def test_without_vault_the_resolver_returns_nothing_at_all() -> None:
    """Not a fake, not a degraded client: NOTHING. The caller must be forced to
    decide what "no Vault" means instead of being handed something that answers."""
    assert _build_vault_client(Settings(vault_url=None, vault_token=None)) is None
    assert _build_vault_client(Settings(vault_url="http://vault:8200", vault_token=None)) is None
    assert _build_vault_client(Settings(vault_url=None, vault_token="t")) is None


def test_with_vault_the_resolver_returns_a_real_hvac_client() -> None:
    client = _build_vault_client(Settings(vault_url="http://vault:8200", vault_token="t"))
    assert isinstance(client, HvacVaultRotationClient)
    # ...and it satisfies the Protocol the engine calls through, so a missing
    # method surfaces here rather than mid-rotation in production.
    assert isinstance(client, VaultRotationClient)


def test_the_production_module_does_not_even_import_the_fake() -> None:
    """Two guards, because either alone rots.

    The behavioural one (the name is not bound in the module) cannot be fooled by
    prose but would pass if the fallback came back under a different name. The
    textual one names the class, so it keeps failing until the fake is genuinely
    unreachable from production code — while still ALLOWING the docstrings to
    mention it, since explaining why it was removed is the point of those
    docstrings.
    """
    import workers.credential_rotation_task as task_module

    assert not hasattr(task_module, "FakeVaultRotationClient")

    source = (_REPO_ROOT / "apps/workers/src/workers/credential_rotation_task.py").read_text(
        encoding="utf-8"
    )
    assert "_build_vault_client" in source, "the guard is scanning the wrong file"
    code_use = re.compile(r"import[^#\n]*\bFakeVaultRotationClient\b|FakeVaultRotationClient\s*\(")
    offenders = [line.strip() for line in source.splitlines() if code_use.search(line)]
    assert not offenders, (
        "the production rotation task imports or constructs the in-memory fake "
        "again:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# 2. No Vault => SKIPPED + alert, never SUCCEEDED
# ---------------------------------------------------------------------------
class _RecordingNotifier:
    def __init__(self) -> None:
        self.alerts: list[RotationAudit] = []

    def alert_failure(self, audit: RotationAudit) -> None:
        self.alerts.append(audit)


def test_a_cycle_without_vault_is_skipped_alerted_and_not_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The three things an operator or a dashboard reads must all refuse to look
    like success: `status`, `ok`, and an alert actually being raised."""
    notifier = _RecordingNotifier()
    monkeypatch.setattr(
        "api_server.db.platform_settings.get_cred_rotation_enabled",
        _always_enabled,
        raising=False,
    )
    summary = asyncio.run(
        _rotate_credentials(_settings_without_db(), client=None, notifier=notifier)
    )

    assert summary["status"] == str(RotationStatus.SKIPPED)
    assert summary["ok"] is False
    assert summary["alerted"] is True
    assert summary["static_secrets"] == []
    assert len(notifier.alerts) == 1
    assert notifier.alerts[0].status is RotationStatus.SKIPPED
    assert "Vault" in (notifier.alerts[0].error or "")


def test_a_skipped_cycle_never_reports_succeeded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression the plan asks for by name: a run with no real client must
    not be able to produce SUCCEEDED under any reading of the summary."""
    summary = asyncio.run(_rotate_credentials(_settings_without_db(), client=None, notifier=None))
    assert summary["status"] != str(RotationStatus.SUCCEEDED)
    assert summary.get("ok") is not True


def _always_enabled(_session: object) -> bool:  # pragma: no cover - replaced below
    return True


def _settings_without_db() -> Settings:
    """Settings whose DB URL points nowhere.

    Deliberate: the enable-flag read then fails, the task defaults to
    ``enabled=True`` (a missed rotation is the worse outcome) and we reach the
    Vault check — which is the branch under test. It also proves the SKIPPED path
    does not depend on a live database.
    """
    return Settings(
        database_url="postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none",
        vault_url=None,
        vault_token=None,
    )


# ---------------------------------------------------------------------------
# 3. MinIO is rotated in the SERVICE, not only in KV
# ---------------------------------------------------------------------------
def test_rotating_minio_mints_in_the_service_before_writing_kv() -> None:
    """Order is the safety property. KV-then-mint would leave a window where the
    KV entry names a credential that does not exist yet."""
    journal: list[str] = []
    minio = _RecordingMinio(journal)
    client, hvac_client = _client(minio=minio, journal=journal)

    version = client.rotate_static_secret(path="platform/minio", mount="secret")

    assert version == 1
    assert journal == [f"minio_mint:{minio.minted[0]}", "kv_write:platform/minio"], journal
    entry = hvac_client.kv_store["platform/minio"]
    assert entry[KV_FIELD_ACCESS_KEY] == minio.minted[0]
    assert entry[KV_FIELD_SECRET_KEY]
    assert entry[KV_FIELD_PENDING_APPLY] == "true"


def test_rotating_minio_without_an_admin_client_refuses_instead_of_writing_kv() -> None:
    """THE gap2-2 regression. Writing KV alone leaves MinIO on the old credential
    and points every service at one that will not authenticate after its restart:
    a rotation that reports success and breaks object storage."""
    client, hvac_client = _client(minio=None)
    with pytest.raises(CredentialRotationError, match="MinIO"):
        client.rotate_static_secret(path="platform/minio", mount="secret")
    assert hvac_client.kv_store == {}, "KV was written despite MinIO not being rotated"


def test_an_unreachable_minio_leaves_kv_untouched() -> None:
    client, hvac_client = _client(minio=_RecordingMinio([], fail_on_mint=True))
    with pytest.raises(CredentialRotationError):
        client.rotate_static_secret(path="platform/minio", mount="secret")
    assert hvac_client.kv_store == {}


def test_the_second_minio_rotation_records_the_credential_it_replaces() -> None:
    """Without the pointer, the previous service account lives forever and the
    "remove" half of add-then-remove can never happen."""
    journal: list[str] = []
    minio = _RecordingMinio(journal)
    client, hvac_client = _client(minio=minio, journal=journal)

    client.rotate_static_secret(path="platform/minio", mount="secret")
    first = hvac_client.kv_store["platform/minio"][KV_FIELD_ACCESS_KEY]
    client.rotate_static_secret(path="platform/minio", mount="secret")

    entry = hvac_client.kv_store["platform/minio"]
    assert entry[KV_FIELD_PREVIOUS_ACCESS_KEY] == first
    assert entry[KV_FIELD_ACCESS_KEY] != first
    # Nothing has been revoked yet: the new value has not propagated.
    assert minio.revoked == []


def test_the_previous_credential_is_revoked_only_by_the_explicit_later_step() -> None:
    """Add-then-REMOVE. Revoking inside the rotation would cut every service off
    the instant the cycle ran, before any of them had the new value."""
    journal: list[str] = []
    minio = _RecordingMinio(journal)
    client, hvac_client = _client(minio=minio, journal=journal)

    client.rotate_static_secret(path="platform/minio", mount="secret")
    first = hvac_client.kv_store["platform/minio"][KV_FIELD_ACCESS_KEY]
    client.rotate_static_secret(path="platform/minio", mount="secret")
    assert minio.revoked == []

    revoked = revoke_previous_minio_credential(client, minio, path="platform/minio")

    assert revoked == first
    assert minio.revoked == [first]
    entry = hvac_client.kv_store["platform/minio"]
    assert KV_FIELD_PREVIOUS_ACCESS_KEY not in entry
    assert entry[KV_FIELD_PENDING_APPLY] == "false"


def test_revoking_twice_is_a_no_op() -> None:
    """The propagation script may retry; a second revoke must not error out or
    delete the credential currently in use."""
    minio = _RecordingMinio([])
    client, _ = _client(minio=minio)
    client.rotate_static_secret(path="platform/minio", mount="secret")
    client.rotate_static_secret(path="platform/minio", mount="secret")

    assert revoke_previous_minio_credential(client, minio, path="platform/minio") is not None
    assert revoke_previous_minio_credential(client, minio, path="platform/minio") is None
    assert len(minio.revoked) == 1


# ---------------------------------------------------------------------------
# 4. Opaque static secrets (jwt & co.)
# ---------------------------------------------------------------------------
def test_an_opaque_static_secret_gets_fresh_high_entropy_material() -> None:
    client, hvac_client = _client()
    v1 = client.rotate_static_secret(path="platform/jwt", mount="secret")
    first = hvac_client.kv_store["platform/jwt"][KV_FIELD_VALUE]
    v2 = client.rotate_static_secret(path="platform/jwt", mount="secret")
    second = hvac_client.kv_store["platform/jwt"][KV_FIELD_VALUE]

    assert (v1, v2) == (1, 2), "the KV version must advance so the rotation is auditable"
    assert first != second
    # Above the api-server's 32-char HMAC floor, with room to spare.
    assert len(second) >= 32
    assert hvac_client.kv_store["platform/jwt"][KV_FIELD_PENDING_APPLY] == "true"


def test_a_vault_write_that_returns_no_version_is_treated_as_a_failure() -> None:
    """The version IS the audit evidence. A write we cannot prove happened must
    not be recorded as a rotation."""

    class _VersionlessKv:
        def create_or_update_secret(self, **_: Any) -> dict[str, Any]:
            return {"data": {}}

        def read_secret_version(self, **_: Any) -> dict[str, Any]:
            raise InvalidPath("x")

    broken = type(
        "C", (), {"secrets": type("S", (), {"kv": type("K", (), {"v2": _VersionlessKv()})()})()}
    )()
    with pytest.raises(CredentialRotationError, match="no version"):
        HvacVaultRotationClient(broken).rotate_static_secret(path="platform/jwt", mount="secret")


def test_a_vault_transport_error_becomes_a_credential_rotation_error() -> None:
    """The engine's contract: every Vault failure is audited + alerted, never an
    unhandled traceback in a beat worker."""

    class _ExplodingKv:
        def create_or_update_secret(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("connection refused")

        def read_secret_version(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("connection refused")

    exploding = type(
        "C", (), {"secrets": type("S", (), {"kv": type("K", (), {"v2": _ExplodingKv()})()})()}
    )()
    with pytest.raises(CredentialRotationError):
        HvacVaultRotationClient(exploding).rotate_static_secret(path="platform/jwt", mount="secret")


def test_a_missing_db_role_reads_as_none_not_as_an_error() -> None:
    """``configure_db_secrets_engine_role`` reads the role back to assert the
    post-state; "absent" must be distinguishable from "Vault is down"."""

    class _Db:
        def read_role(self, *, name: str, mount_point: str) -> dict[str, Any]:
            raise InvalidPath(name)

    client = HvacVaultRotationClient(
        type("C", (), {"secrets": type("S", (), {"database": _Db()})()})()
    )
    assert client.read_db_role("platform-app", mount="database") is None


def test_a_configured_db_role_round_trips_through_the_adapter() -> None:
    class _Db:
        def __init__(self) -> None:
            self.created: dict[str, Any] = {}

        def create_role(self, **kwargs: Any) -> None:
            self.created = kwargs

        def read_role(self, *, name: str, mount_point: str) -> dict[str, Any]:
            return {
                "data": {
                    "db_name": self.created["db_name"],
                    "default_ttl": self.created["default_ttl"],
                    "max_ttl": self.created["max_ttl"],
                    "creation_statements": self.created["creation_statements"],
                }
            }

    db = _Db()
    client = HvacVaultRotationClient(
        type("C", (), {"secrets": type("S", (), {"database": db})()})()
    )
    role = DbSecretsEngineRole(
        name="platform-app",
        db_connection="platform-postgres",
        ttl_s=3600,
        max_ttl_s=86400,
        creation_statements=("CREATE ROLE x;",),
    )
    client.configure_db_role(role, mount="database")
    read_back = client.read_db_role("platform-app", mount="database")

    assert read_back == role
