"""Vault dynamic-secret credential rotation engine (Plan 15 task_15_17).

Plan 15 Fase C, *"Rotación automática de credenciales (Vault dynamic secrets)"*.
Credential rotation has two complementary halves and this module owns both, the
same way the Vault BOOTSTRAP (installer task_15_09) owns its sequence:

  1. **Short-TTL DYNAMIC DB credentials** — the Vault *database secrets engine*
     mints a throwaway Postgres role per lease (``configure_db_secrets_engine_role``
     + :func:`issue_dynamic_db_credential`). A service holds creds only for the
     role's TTL; Vault revokes the role on lease expiry, so a leaked credential
     self-expires instead of living forever.

  2. **Periodic ROTATION CYCLE** — :func:`rotate_credentials` rotates the STATIC
     secrets (MinIO access/secret key, JWT signing key, …) in place each cycle,
     issues a fresh dynamic DB lease, and **renews then revokes** the prior lease
     so the window where two valid credentials coexist is bounded. The cycle is
     driven on a CONFIGURABLE cadence by a Celery beat task
     (:mod:`workers.credential_rotation_task`).

The Vault client behind a seam (mocked in tests)
-------------------------------------------------
The worker links NO ``hvac`` dependency. Every Vault call is expressed through
the :class:`VaultRotationClient` Protocol — an ``hvac``-like surface (database
secrets-engine role config + generate-credentials + sys lease renew/revoke + a
KV v2 write for the static-secret rotation). Tests inject
:class:`FakeVaultRotationClient`, an in-memory fake that models the role config,
lease lifecycle and KV writes, so the WHOLE cycle is asserted with NO real Vault.
The real binding (an ``hvac.Client`` adapter) lands at install time and is
exercised only by the plan's Tests Humanos. This mirrors the seam discipline of
:mod:`installer_backend.vault_bootstrap` and :mod:`workers.backup_encryption`.

Audit + alerting (reusing Plan 10)
----------------------------------
Every cycle writes an immutable :class:`RotationAudit` entry (the WHAT/WHEN, the
secret *names* + lease ids — never the secret *values*). A rotation FAILURE does
NOT take the system down: the failure is caught, audited as failed, and an alert
is raised through the injected :class:`RotationNotifier` (the production binding
enqueues a ``credential_rotation_failed`` event onto the Plan 10 notification
dispatcher's priority lane). The current credentials keep working; an operator is
notified to investigate.

Secrets are never logged in plaintext
--------------------------------------
The minted username/password live only inside :class:`DynamicDbCredential`,
whose ``__repr__``/``__str__`` are redacted. The static rotation logs only the
secret *name*, never the new value. The structured log lines + the audit entry
carry names/lease-ids/counts — never a credential. This is the same redaction
discipline as :class:`installer_backend.vault_bootstrap.VaultInitResult`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

import structlog

_log = structlog.get_logger("workers.credential_rotation")


# ---------------------------------------------------------------------------
# Platform KV layout for the STATIC secrets the rotation cycle rotates. Mirrors
# the installer's PLATFORM_KV_MOUNT layout (installer_backend.vault_bootstrap)
# without importing it (workers must stay import-clean of the installer app).
# ---------------------------------------------------------------------------
#: KV v2 mount holding the platform's static secrets.
PLATFORM_KV_MOUNT = "secret"

#: Logical static-secret name → its KV v2 path under the mount. The rotation
#: cycle rotates the value AT this path; the path is config, the value is the
#: high-entropy material Vault generates + writes (never in code, never logged).
STATIC_SECRET_PATHS: dict[str, str] = {
    "minio": "platform/minio",
    "jwt": "platform/jwt",
}


class RotationStatus(StrEnum):
    """Outcome of a rotation cycle (or a single secret within it)."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class DbSecretsEngineRole:
    """The Vault database-secrets-engine role that mints dynamic Postgres creds.

    ``name`` is the role name a service reads its lease from
    (``<mount>/creds/<name>``). ``db_connection`` is the configured Vault
    database CONNECTION the role is bound to (the admin DSN Vault dials to
    create/drop the throwaway roles lives server-side, never here). ``ttl_s`` /
    ``max_ttl_s`` are the SHORT lease lifetimes — a minted credential self-expires
    so a leak is bounded. ``creation_statements`` are the SQL Vault runs to mint
    a role; they carry NO password (Vault injects ``{{password}}``). NOT a secret.
    """

    name: str
    db_connection: str
    ttl_s: int
    max_ttl_s: int
    creation_statements: tuple[str, ...]


@dataclass(frozen=True)
class DynamicDbCredential:
    """A short-TTL dynamic DB credential minted by the database secrets engine.

    ``username`` / ``password`` are the throwaway Postgres role's credentials —
    HIGH-VALUE material, so ``__repr__``/``__str__`` are redacted to keep them
    out of any log line or traceback frame. ``lease_id`` / ``lease_duration_s``
    identify the lease the rotation cycle renews/revokes; those ARE safe to log
    (they name the lease, they are not the secret).
    """

    username: str
    password: str
    lease_id: str
    lease_duration_s: int

    def __repr__(self) -> str:  # pragma: no cover - trivial, security-load-bearing
        return f"DynamicDbCredential(lease_id={self.lease_id!r}, <username/password redacted>)"

    __str__ = __repr__


@dataclass(frozen=True)
class StaticSecretRotation:
    """Record of one static secret rotated in a cycle (names + lease-ids only).

    ``name`` is the logical secret name (``minio``/``jwt``); ``path`` is its KV
    v2 path. ``version`` is the new KV version Vault assigned (KV v2 keeps the
    prior version for rollback). The rotated VALUE is never recorded here.
    """

    name: str
    path: str
    version: int


@dataclass(frozen=True)
class RotationAudit:
    """The immutable audit entry a rotation cycle writes (no secret values).

    Carries the WHAT/WHEN: the cycle status, the static secrets rotated (by
    name + new KV version), the new lease id, the renewed/revoked prior lease
    id, and — on failure — a non-leaky error string. This is the audit surface
    the runbook (task_15_23) + the failure alert reference. ``__repr__`` is the
    dataclass default (it carries no secret), so it is safe to log.
    """

    rotated_at: datetime
    status: RotationStatus
    static_secrets: tuple[StaticSecretRotation, ...]
    new_lease_id: str | None
    renewed_lease_id: str | None
    revoked_lease_id: str | None
    error: str | None = None

    def as_log_fields(self) -> dict[str, object]:
        """JSON-safe, secret-free fields for the structured log / event payload."""
        return {
            "rotated_at": self.rotated_at.isoformat(),
            "status": str(self.status),
            "static_secrets": [s.name for s in self.static_secrets],
            "static_secret_versions": {s.name: s.version for s in self.static_secrets},
            "new_lease_id": self.new_lease_id,
            "renewed_lease_id": self.renewed_lease_id,
            "revoked_lease_id": self.revoked_lease_id,
            "error": self.error,
        }


@dataclass(frozen=True)
class RotationOutcome:
    """Result of :func:`rotate_credentials` — the audit entry + whether it alerted.

    ``audit`` is always set (a cycle always produces an audit entry, success or
    failure). ``credential`` carries the freshly-minted dynamic credential on
    success (redacted repr), ``None`` on failure. ``alerted`` is True when a
    failure raised an alert through the notifier.
    """

    audit: RotationAudit
    credential: DynamicDbCredential | None
    alerted: bool

    @property
    def ok(self) -> bool:
        return self.audit.status is RotationStatus.SUCCEEDED


class CredentialRotationError(Exception):
    """Raised by the Vault seam when a rotation step cannot complete.

    The message is surfaced to the operator (audit + alert), so it MUST NOT
    carry any secret — no password, no Vault token, no minted credential.
    """


@runtime_checkable
class VaultRotationClient(Protocol):
    """An ``hvac``-like surface for credential rotation — the injectable seam.

    Models only what rotation needs: configuring + reading the database
    secrets-engine role, generating dynamic creds, renewing/revoking a lease,
    and rotating a static KV v2 secret in place. The real binding is an
    ``hvac.Client`` adapter (Tests Humanos); tests inject
    :class:`FakeVaultRotationClient`. Method names/shapes mirror ``hvac`` so the
    adapter is a thin pass-through.
    """

    def configure_db_role(self, role: DbSecretsEngineRole, *, mount: str) -> None:
        """Create/update the database secrets-engine role (idempotent)."""
        ...

    def read_db_role(self, name: str, *, mount: str) -> DbSecretsEngineRole | None:
        """Return the configured role, or None if it does not exist."""
        ...

    def generate_db_credentials(self, role_name: str, *, mount: str) -> DynamicDbCredential:
        """Mint a fresh short-TTL dynamic DB credential from the role."""
        ...

    def renew_lease(self, lease_id: str, *, increment_s: int) -> int:
        """Renew a lease; return the new lease duration (seconds)."""
        ...

    def revoke_lease(self, lease_id: str) -> None:
        """Revoke a lease immediately (Vault drops the throwaway role)."""
        ...

    def rotate_static_secret(self, *, path: str, mount: str) -> int:
        """Rotate a static KV v2 secret in place; return the new version."""
        ...


@runtime_checkable
class RotationNotifier(Protocol):
    """Raises an operator alert when a rotation cycle fails (Plan 10 reuse).

    The production binding (:class:`CeleryRotationNotifier`) enqueues a
    ``credential_rotation_failed`` domain event onto the notification
    dispatcher's priority lane; the dispatcher fans it out to the System
    Admins' channels. Tests inject a fake that records the call. The alert
    payload carries NO secret — only the audit's secret-free log fields.
    """

    def alert_failure(self, audit: RotationAudit) -> None:
        """Raise a credential-rotation-failure alert for *audit*."""
        ...


# ---------------------------------------------------------------------------
# DB secrets-engine role configuration (the dynamic-secret half).
# ---------------------------------------------------------------------------
#: Default SQL the Vault database engine runs to mint a throwaway login role.
#: Vault substitutes ``{{name}}``/``{{password}}``/``{{expiration}}`` — the
#: password is generated server-side and NEVER appears in this template. The role
#: gets exactly the platform app group's privileges (least privilege; mirrors how
#: the app's RLS-bound ``app_user`` is granted), so a dynamic role is no broader
#: than the static one it replaces.
DEFAULT_DB_CREATION_STATEMENTS: tuple[str, ...] = (
    "CREATE ROLE \"{{name}}\" WITH LOGIN PASSWORD '{{password}}' "
    "VALID UNTIL '{{expiration}}' IN ROLE app_user;",
)


def build_db_role(
    *,
    name: str,
    db_connection: str,
    ttl_s: int,
    max_ttl_s: int,
    creation_statements: Sequence[str] | None = None,
) -> DbSecretsEngineRole:
    """Build the database-secrets-engine role spec (validated, no secret).

    ``ttl_s`` must be a positive, SHORT lifetime not exceeding ``max_ttl_s`` —
    the whole point is a self-expiring credential, so we reject a non-positive
    or inverted TTL rather than silently mint a long-lived one.
    """
    if ttl_s < 1:
        raise CredentialRotationError(f"db role ttl must be >= 1s (got {ttl_s})")
    if max_ttl_s < ttl_s:
        raise CredentialRotationError(f"db role max_ttl ({max_ttl_s}s) must be >= ttl ({ttl_s}s)")
    statements = (
        tuple(creation_statements) if creation_statements else DEFAULT_DB_CREATION_STATEMENTS
    )
    return DbSecretsEngineRole(
        name=name,
        db_connection=db_connection,
        ttl_s=ttl_s,
        max_ttl_s=max_ttl_s,
        creation_statements=statements,
    )


def configure_db_secrets_engine_role(
    client: VaultRotationClient,
    role: DbSecretsEngineRole,
    *,
    mount: str = "database",
) -> DbSecretsEngineRole:
    """Configure the Postgres database secrets-engine role (idempotent).

    Services then read ``<mount>/creds/<role>`` to get a short-TTL dynamic
    credential. Re-running converges the role config (TTLs, creation SQL) — it
    never mints a credential here. Returns the role that is now configured (read
    back through the seam, so the caller asserts the post-state).
    """
    client.configure_db_role(role, mount=mount)
    configured = client.read_db_role(role.name, mount=mount)
    if configured is None:
        raise CredentialRotationError(
            f"database role {role.name!r} was not configured at mount {mount!r}"
        )
    _log.info(
        "credential_rotation.db_role_configured",
        role=configured.name,
        mount=mount,
        connection=configured.db_connection,
        ttl_s=configured.ttl_s,
        max_ttl_s=configured.max_ttl_s,
    )
    return configured


def issue_dynamic_db_credential(
    client: VaultRotationClient,
    role_name: str,
    *,
    mount: str = "database",
) -> DynamicDbCredential:
    """Mint a fresh short-TTL dynamic DB credential from the configured role.

    The minted username/password live only inside the returned
    :class:`DynamicDbCredential` (redacted repr); they are NEVER logged. Only
    the lease id + duration are logged (they name the lease, not the secret).
    """
    cred = client.generate_db_credentials(role_name, mount=mount)
    _log.info(
        "credential_rotation.db_credential_issued",
        role=role_name,
        mount=mount,
        lease_id=cred.lease_id,
        lease_duration_s=cred.lease_duration_s,
    )
    return cred


# ---------------------------------------------------------------------------
# The rotation cycle (the periodic-job half).
# ---------------------------------------------------------------------------
def rotate_credentials(
    client: VaultRotationClient,
    *,
    db_role_name: str,
    db_mount: str = "database",
    static_secret_names: Sequence[str] = ("minio", "jwt"),
    kv_mount: str = PLATFORM_KV_MOUNT,
    renew_increment_s: int = 3600,
    previous_lease_id: str | None = None,
    notifier: RotationNotifier | None = None,
    now: datetime | None = None,
) -> RotationOutcome:
    """Run ONE credential-rotation cycle. Best-effort: a failure never raises.

    Steps, in order:

      1. **Rotate the static secrets** (MinIO/JWT/…) in place — Vault generates a
         new high-entropy value at each KV v2 path and bumps the version (the
         prior version is retained for rollback). Only the secret NAME + new
         version are recorded; the value is never read or logged.
      2. **Issue a fresh dynamic DB credential** from the configured role.
      3. **Renew then revoke the PRIOR lease** (``previous_lease_id``) so the
         old credential is briefly renewed (services mid-request keep working),
         then revoked — bounding the window where two valid credentials coexist.

    On ANY failure the cycle does NOT raise (the platform stays up on its
    current credentials): the error is caught, an audit entry with
    ``status=FAILED`` is produced, and — if a ``notifier`` is wired — an alert
    is raised (Plan 10) so an operator investigates. Returns the
    :class:`RotationOutcome` either way; the caller (the beat task) logs it.

    Secrets are never logged: the structured logs + the audit entry carry secret
    *names*, lease *ids* and counts — never a credential value.
    """
    rotated_at = now or datetime.now(UTC)
    rotations: list[StaticSecretRotation] = []
    new_lease_id: str | None = None
    renewed_lease_id: str | None = None
    revoked_lease_id: str | None = None
    credential: DynamicDbCredential | None = None

    try:
        # 1) Rotate the static secrets in place.
        for name in static_secret_names:
            path = STATIC_SECRET_PATHS.get(name, f"platform/{name}")
            version = client.rotate_static_secret(path=path, mount=kv_mount)
            rotations.append(StaticSecretRotation(name=name, path=path, version=version))
            # NAME + new version only — never the rotated value.
            _log.info(
                "credential_rotation.static_rotated",
                secret=name,
                path=path,
                version=version,
            )

        # 2) Issue a fresh dynamic DB credential.
        credential = issue_dynamic_db_credential(client, db_role_name, mount=db_mount)
        new_lease_id = credential.lease_id

        # 3) Renew then revoke the prior lease (bounded overlap).
        if previous_lease_id:
            client.renew_lease(previous_lease_id, increment_s=renew_increment_s)
            renewed_lease_id = previous_lease_id
            client.revoke_lease(previous_lease_id)
            revoked_lease_id = previous_lease_id
            _log.info(
                "credential_rotation.prior_lease_cycled",
                renewed_lease_id=renewed_lease_id,
                revoked_lease_id=revoked_lease_id,
            )
    except Exception as exc:
        # A failure must NOT take the system down — audit it, alert, return.
        audit = RotationAudit(
            rotated_at=rotated_at,
            status=RotationStatus.FAILED,
            static_secrets=tuple(rotations),
            new_lease_id=new_lease_id,
            renewed_lease_id=renewed_lease_id,
            revoked_lease_id=revoked_lease_id,
            error=str(exc),
        )
        _log.warning("credential_rotation.failed", **audit.as_log_fields())
        alerted = False
        if notifier is not None:
            try:
                notifier.alert_failure(audit)
                alerted = True
            except Exception as alert_exc:  # pragma: no cover — defensive
                # An alerting failure must not mask the rotation failure.
                _log.warning("credential_rotation.alert_failed", error=str(alert_exc))
        return RotationOutcome(audit=audit, credential=None, alerted=alerted)

    audit = RotationAudit(
        rotated_at=rotated_at,
        status=RotationStatus.SUCCEEDED,
        static_secrets=tuple(rotations),
        new_lease_id=new_lease_id,
        renewed_lease_id=renewed_lease_id,
        revoked_lease_id=revoked_lease_id,
    )
    _log.info("credential_rotation.succeeded", **audit.as_log_fields())
    return RotationOutcome(audit=audit, credential=credential, alerted=False)


# ---------------------------------------------------------------------------
# Production notifier — enqueues a Plan 10 event (no import of the dispatcher).
# ---------------------------------------------------------------------------
#: The domain event a rotation failure raises. Mapped to a priority-lane
#: notification in the dispatcher's EVENT_REGISTRY (Plan 10 task_10_04).
ROTATION_FAILED_EVENT = "credential_rotation_failed"


@dataclass(frozen=True)
class CeleryRotationNotifier:
    """Default :class:`RotationNotifier` — enqueues a Plan 10 event by name.

    Mirrors :func:`api_server.celery_client.enqueue_event_dispatch`: the worker
    only PRODUCES the ``notification_dispatcher.dispatch_event`` task by name
    onto the priority lane — it never imports the dispatcher package (clean app
    boundary). The event is platform-scoped (``tenant_id=None``: a System-Admin
    ops signal). The ``context`` carries the audit's secret-free log fields —
    never a credential value.
    """

    broker_url: str
    dispatch_task: str = "notification_dispatcher.dispatch_event"
    priority_queue: str = "notifications.priority"

    def alert_failure(self, audit: RotationAudit) -> None:
        from celery import Celery

        event = {
            "event_type": ROTATION_FAILED_EVENT,
            "tenant_id": None,  # platform-scoped ops alert
            "context": audit.as_log_fields(),
        }
        Celery(broker=self.broker_url).send_task(
            self.dispatch_task,
            args=[event],
            queue=self.priority_queue,
        )


# ---------------------------------------------------------------------------
# In-memory fake Vault client — the test default. Models the role config, the
# lease lifecycle and KV writes with NO real Vault. The real hvac adapter lands
# at install time and is exercised only by the plan's Tests Humanos.
# ---------------------------------------------------------------------------
@dataclass
class FakeVaultRotationClient:
    """A deterministic in-memory :class:`VaultRotationClient`.

    Records the configured role, the issued/renewed/revoked leases and the KV
    static-secret versions so a test asserts the whole cycle. Each
    :meth:`generate_db_credentials` mints obviously-fake (scripted) creds with a
    monotonically increasing lease id; each :meth:`rotate_static_secret` bumps
    the path's version. ``fail_on`` makes a chosen step raise
    :class:`CredentialRotationError` so the failure path is exercised.
    """

    #: Configured roles keyed by ``<mount>/<name>``.
    roles: dict[str, DbSecretsEngineRole] = field(default_factory=dict)
    #: KV v2 static-secret versions keyed by ``<mount>/<path>``.
    static_versions: dict[str, int] = field(default_factory=dict)
    #: Lease lifecycle records.
    issued_leases: list[str] = field(default_factory=list)
    renewed_leases: list[str] = field(default_factory=list)
    revoked_leases: list[str] = field(default_factory=list)
    rotated_paths: list[str] = field(default_factory=list)
    #: Monotonic counter backing the scripted lease ids.
    _lease_seq: int = 0
    #: When set, the named step raises CredentialRotationError. One of:
    #: "rotate_static" | "generate" | "renew" | "revoke".
    fail_on: str | None = None
    #: Default TTL the scripted minted credential reports.
    default_lease_duration_s: int = 3600

    def _key(self, mount: str, name: str) -> str:
        return f"{mount}/{name}"

    # -- VaultRotationClient surface ----------------------------------------
    def configure_db_role(self, role: DbSecretsEngineRole, *, mount: str) -> None:
        self.roles[self._key(mount, role.name)] = role

    def read_db_role(self, name: str, *, mount: str) -> DbSecretsEngineRole | None:
        return self.roles.get(self._key(mount, name))

    def generate_db_credentials(self, role_name: str, *, mount: str) -> DynamicDbCredential:
        if self.fail_on == "generate":
            raise CredentialRotationError("vault: failed to generate dynamic credentials")
        if self._key(mount, role_name) not in self.roles:
            raise CredentialRotationError(f"vault: role {role_name!r} not configured")
        self._lease_seq += 1
        lease_id = f"{mount}/creds/{role_name}/lease-{self._lease_seq}"
        self.issued_leases.append(lease_id)
        return DynamicDbCredential(
            username=f"v-token-{role_name}-{self._lease_seq}",
            password=f"fake-dynamic-password-{self._lease_seq}",
            lease_id=lease_id,
            lease_duration_s=self.default_lease_duration_s,
        )

    def renew_lease(self, lease_id: str, *, increment_s: int) -> int:
        if self.fail_on == "renew":
            raise CredentialRotationError("vault: failed to renew lease")
        self.renewed_leases.append(lease_id)
        return increment_s

    def revoke_lease(self, lease_id: str) -> None:
        if self.fail_on == "revoke":
            raise CredentialRotationError("vault: failed to revoke lease")
        self.revoked_leases.append(lease_id)

    def rotate_static_secret(self, *, path: str, mount: str) -> int:
        if self.fail_on == "rotate_static":
            raise CredentialRotationError("vault: failed to rotate static secret")
        key = self._key(mount, path)
        version = self.static_versions.get(key, 0) + 1
        self.static_versions[key] = version
        self.rotated_paths.append(path)
        return version


@dataclass
class RecordingRotationNotifier:
    """A test :class:`RotationNotifier` that records the alerts it received."""

    alerts: list[RotationAudit] = field(default_factory=list)

    def alert_failure(self, audit: RotationAudit) -> None:
        self.alerts.append(audit)


__all__ = [
    "DEFAULT_DB_CREATION_STATEMENTS",
    "PLATFORM_KV_MOUNT",
    "ROTATION_FAILED_EVENT",
    "STATIC_SECRET_PATHS",
    "CeleryRotationNotifier",
    "CredentialRotationError",
    "DbSecretsEngineRole",
    "DynamicDbCredential",
    "FakeVaultRotationClient",
    "RecordingRotationNotifier",
    "RotationAudit",
    "RotationNotifier",
    "RotationOutcome",
    "RotationStatus",
    "StaticSecretRotation",
    "VaultRotationClient",
    "build_db_role",
    "configure_db_secrets_engine_role",
    "issue_dynamic_db_credential",
    "rotate_credentials",
]
