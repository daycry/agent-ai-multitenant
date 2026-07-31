"""The REAL Vault + MinIO bindings for credential rotation (prod-05, gap2-1/gap2-2).

Until this module existed, ``credential_rotation_task._build_vault_client``
returned :class:`workers.credential_rotation.FakeVaultRotationClient`
**unconditionally** — an in-memory dictionary. The scheduled job therefore wrote
an audit row with ``status=SUCCEEDED`` and ``ok: true`` every week without
touching Vault, without touching MinIO and without rotating a single credential.
Worse, the canonical runbook pointed EMERGENCY REVOCATION at that job (gap2-1,
critical): the documented response to a leaked credential was a no-op that
reported success.

This module supplies the two real bindings and — with the resolver in
:mod:`workers.credential_rotation_task` — makes ``SUCCEEDED`` unreachable without
them.

Two adapters
------------
:class:`HvacVaultRotationClient`
    The ``hvac`` implementation of the ``VaultRotationClient`` Protocol. Lazily
    imported, exception-zoo translated to :class:`CredentialRotationError`,
    address + token from settings and NEVER logged — the same discipline as
    :class:`api_server.llm_providers.vault.HvacLLMProviderVaultStore`.

:class:`MinioServiceAccountRotator`
    The piece gap2-2 was really about. Writing a new value into
    ``secret/platform/minio`` rotates NOTHING: MinIO keeps accepting the old
    credential and every service keeps using the old one from its env. Real
    rotation means minting a credential IN MINIO. We use **service accounts**
    (child credentials of the root user) so the rotation never has to change the
    root credential itself.

ADD-THEN-REMOVE, in that order, always
--------------------------------------
The ordering is the whole safety property, and getting it backwards takes the
platform's object storage down:

  1. mint the NEW MinIO service account (both credentials now valid);
  2. write it to KV v2, recording the PREVIOUS access key and marking the entry
     ``pending_apply``;
  3. — propagation happens out of band: the services are restarted with the new
     value (ADR 0144 chooses env + coordinated restart over runtime Vault
     reads) —
  4. only THEN :func:`revoke_previous_minio_credential` deletes the old service
     account and clears ``pending_apply``.

Between 2 and 4 both credentials work, so there is no cut-over window. Step 4 is
a SEPARATE call precisely so it cannot accidentally run inside step 1's
transaction of thought: revoking before propagating is the failure mode the
runbook warns about.

If MinIO's admin API is unreachable, the rotation FAILS LOUDLY before KV is
written. A KV entry that names a credential MinIO never issued is worse than no
rotation at all: every service would restart onto a credential that does not
authenticate.
"""

from __future__ import annotations

import secrets
import string
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import structlog

from workers.credential_rotation import (
    PLATFORM_KV_MOUNT,
    STATIC_SECRET_PATHS,
    CredentialRotationError,
    DbSecretsEngineRole,
    DynamicDbCredential,
)

_log = structlog.get_logger("workers.credential_rotation_hvac")

#: Entropy of a generated static secret (JWT signing key and friends), in bytes
#: before urlsafe-base64. 48 bytes ≈ 64 chars, comfortably above the api-server's
#: 32-char HMAC floor with room for the operator to keep the old key too.
_STATIC_SECRET_BYTES = 48

#: MinIO service-account access keys are user-visible identifiers, not secrets.
#: Uppercase alphanumeric mirrors the AWS-style keys MinIO generates itself.
_MINIO_ACCESS_KEY_LEN = 20
_MINIO_SECRET_KEY_LEN = 40
_MINIO_ACCESS_ALPHABET = string.ascii_uppercase + string.digits

#: KV field names. ``pending_apply`` is the marker task_prod05_06 clears once the
#: rotated value has actually reached the services; ``previous_access_key`` is
#: what step 4 needs in order to revoke the credential it replaced.
KV_FIELD_VALUE = "value"
KV_FIELD_ACCESS_KEY = "access_key"
KV_FIELD_SECRET_KEY = "secret_key"
KV_FIELD_PREVIOUS_ACCESS_KEY = "previous_access_key"
KV_FIELD_PENDING_APPLY = "pending_apply"
KV_FIELD_ROTATED_AT = "rotated_at"

#: The logical static-secret name whose rotation must also touch a live service.
MINIO_SECRET_NAME = "minio"


# ---------------------------------------------------------------------------
# MinIO admin seam
# ---------------------------------------------------------------------------
@runtime_checkable
class MinioCredentialRotator(Protocol):
    """Mint / revoke a MinIO credential. Injectable so tests need no MinIO."""

    def mint(self) -> tuple[str, str]:
        """Create a new credential IN MinIO; return ``(access_key, secret_key)``."""
        ...

    def revoke(self, access_key: str) -> None:
        """Delete a previously minted credential. Idempotent."""
        ...


class MinioServiceAccountRotator:
    """Mints MinIO **service accounts** through the admin API (``MinioAdmin``).

    Service accounts rather than a new root user: they inherit the parent user's
    policy, can be created and deleted freely, and rotating one never risks
    locking the platform out of its own object storage the way a botched root
    credential change would.

    ``minio`` is imported lazily so a worker that never rotates pays nothing, and
    so a missing/incompatible client surfaces as a clear
    :class:`CredentialRotationError` rather than an ImportError at module load.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        root_user: str,
        root_password: str,
        secure: bool = False,
    ) -> None:
        # `endpoint` is host:port — MinioAdmin takes no scheme. Accept a URL for
        # operator convenience and strip it, because "http://minio:9000" in the
        # env var is the mistake everyone makes exactly once.
        self._endpoint = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
        self._secure = secure or endpoint.startswith("https://")
        self._root_user = root_user
        self._root_password = root_password

    def _admin(self) -> Any:
        try:
            from minio import MinioAdmin

            # `minio.credentials.providers` is the module that DEFINES it; the
            # package re-export is not declared in the type stubs.
            from minio.credentials.providers import StaticProvider
        except ImportError as exc:  # pragma: no cover - the dep is declared
            raise CredentialRotationError(
                "the `minio` client is not installed, so the MinIO credential "
                "cannot be rotated in the service"
            ) from exc
        return MinioAdmin(
            endpoint=self._endpoint,
            credentials=StaticProvider(self._root_user, self._root_password),
            secure=self._secure,
        )

    def mint(self) -> tuple[str, str]:
        access_key = "".join(
            secrets.choice(_MINIO_ACCESS_ALPHABET) for _ in range(_MINIO_ACCESS_KEY_LEN)
        )
        secret_key = secrets.token_urlsafe(_MINIO_SECRET_KEY_LEN)[:_MINIO_SECRET_KEY_LEN]
        try:
            self._admin().add_service_account(
                access_key=access_key,
                secret_key=secret_key,
                name="agentic-platform-rotation",
                description=f"rotated {datetime.now(UTC).isoformat()}",
            )
        except Exception as exc:  # the admin client raises a zoo of types
            # NEVER echo the generated secret in the error.
            raise CredentialRotationError(
                f"MinIO admin API refused to create a service account at "
                f"{self._endpoint!r}: {type(exc).__name__}"
            ) from exc
        # The ACCESS key is an identifier, safe to log; the secret never is.
        _log.info("credential_rotation.minio.service_account_created", access_key=access_key)
        return access_key, secret_key

    def revoke(self, access_key: str) -> None:
        try:
            self._admin().delete_service_account(access_key)
        except Exception as exc:
            raise CredentialRotationError(
                f"MinIO admin API refused to delete service account "
                f"{access_key!r}: {type(exc).__name__}"
            ) from exc
        _log.info("credential_rotation.minio.service_account_revoked", access_key=access_key)


# ---------------------------------------------------------------------------
# The hvac client
# ---------------------------------------------------------------------------
class HvacVaultRotationClient:
    """``VaultRotationClient`` backed by a real ``hvac.Client``.

    The client is injected already constructed and typed ``Any`` so this module
    never imports ``hvac`` (matching ``HvacLLMProviderVaultStore``). Every hvac
    call is wrapped: the library raises a zoo of exception types and the rotation
    engine's contract is that a Vault failure becomes a
    :class:`CredentialRotationError`, audited and alerted — never an unhandled
    traceback in a beat worker.
    """

    def __init__(
        self,
        client: Any,
        *,
        minio_rotator: MinioCredentialRotator | None = None,
    ) -> None:
        self._client = client
        self._minio = minio_rotator

    # -- database secrets engine -------------------------------------------
    def configure_db_role(self, role: DbSecretsEngineRole, *, mount: str) -> None:
        try:
            self._client.secrets.database.create_role(
                name=role.name,
                db_name=role.db_connection,
                creation_statements=list(role.creation_statements),
                default_ttl=role.ttl_s,
                max_ttl=role.max_ttl_s,
                mount_point=mount,
            )
        except Exception as exc:
            raise CredentialRotationError(
                f"Vault refused to configure database role {role.name!r} at mount "
                f"{mount!r}: {type(exc).__name__}"
            ) from exc

    def read_db_role(self, name: str, *, mount: str) -> DbSecretsEngineRole | None:
        try:
            resp = self._client.secrets.database.read_role(name=name, mount_point=mount)
        except Exception as exc:
            if _is_invalid_path(exc):
                return None
            raise CredentialRotationError(
                f"Vault read of database role {name!r} failed: {type(exc).__name__}"
            ) from exc
        data = (resp or {}).get("data") or {}
        return DbSecretsEngineRole(
            name=name,
            db_connection=str(data.get("db_name", "")),
            ttl_s=int(data.get("default_ttl", 0)),
            max_ttl_s=int(data.get("max_ttl", 0)),
            creation_statements=tuple(data.get("creation_statements", ())),
        )

    def generate_db_credentials(self, role_name: str, *, mount: str) -> DynamicDbCredential:
        try:
            resp = self._client.secrets.database.generate_credentials(
                name=role_name, mount_point=mount
            )
        except Exception as exc:
            raise CredentialRotationError(
                f"Vault refused to mint a dynamic credential for role "
                f"{role_name!r}: {type(exc).__name__}"
            ) from exc
        data = (resp or {}).get("data") or {}
        return DynamicDbCredential(
            username=str(data.get("username", "")),
            password=str(data.get("password", "")),
            lease_id=str((resp or {}).get("lease_id", "")),
            lease_duration_s=int((resp or {}).get("lease_duration", 0)),
        )

    def renew_lease(self, lease_id: str, *, increment_s: int) -> int:
        try:
            resp = self._client.sys.renew_lease(lease_id=lease_id, increment=increment_s)
        except Exception as exc:
            raise CredentialRotationError(
                f"Vault refused to renew lease {lease_id!r}: {type(exc).__name__}"
            ) from exc
        return int((resp or {}).get("lease_duration", increment_s))

    def revoke_lease(self, lease_id: str) -> None:
        try:
            self._client.sys.revoke_lease(lease_id=lease_id)
        except Exception as exc:
            raise CredentialRotationError(
                f"Vault refused to revoke lease {lease_id!r}: {type(exc).__name__}"
            ) from exc

    # -- static KV v2 secrets ----------------------------------------------
    def rotate_static_secret(self, *, path: str, mount: str) -> int:
        """Rotate the static secret at ``path``; return the new KV v2 version.

        For the MinIO secret this MINTS THE CREDENTIAL IN MINIO FIRST and only
        then writes KV (add-then-remove, step 1→2). For every other static secret
        it generates fresh high-entropy material and writes it.

        The entry is written with ``pending_apply=true``: the value exists in
        Vault but no service is using it yet. The propagation step
        (``scripts``/task_prod05_06) clears the marker; until it does, the
        auditing surface can tell "rotated" from "rotated AND in effect", which
        is the distinction gap2-2 was about.
        """
        if path == STATIC_SECRET_PATHS.get(MINIO_SECRET_NAME) or path.endswith("/minio"):
            return self._rotate_minio(path=path, mount=mount)
        return self._rotate_opaque(path=path, mount=mount)

    def _rotate_opaque(self, *, path: str, mount: str) -> int:
        secret = {
            KV_FIELD_VALUE: secrets.token_urlsafe(_STATIC_SECRET_BYTES),
            KV_FIELD_PENDING_APPLY: "true",
            KV_FIELD_ROTATED_AT: datetime.now(UTC).isoformat(),
        }
        return self._write_kv(path=path, mount=mount, secret=secret)

    def _rotate_minio(self, *, path: str, mount: str) -> int:
        if self._minio is None:
            # Refusing is the point: writing KV without touching MinIO is exactly
            # the no-op that made the previous implementation report success.
            raise CredentialRotationError(
                "the MinIO credential cannot be rotated: no MinIO admin client is "
                "configured. Writing a new value to KV alone leaves MinIO on the "
                "OLD credential and every service on a value that will not "
                "authenticate after the restart (gap2-2)."
            )
        previous = self._read_kv(path=path, mount=mount)
        # MINT FIRST. If MinIO is unreachable this raises and KV is untouched.
        access_key, secret_key = self._minio.mint()
        secret = {
            KV_FIELD_ACCESS_KEY: access_key,
            KV_FIELD_SECRET_KEY: secret_key,
            KV_FIELD_PENDING_APPLY: "true",
            KV_FIELD_ROTATED_AT: datetime.now(UTC).isoformat(),
        }
        # Remember which credential this one replaces, so step 4 knows what to
        # revoke. Without it the old service account lives forever — a rotation
        # that adds credentials and never removes any is not a rotation.
        old_access_key = previous.get(KV_FIELD_ACCESS_KEY)
        if old_access_key:
            secret[KV_FIELD_PREVIOUS_ACCESS_KEY] = old_access_key
        return self._write_kv(path=path, mount=mount, secret=secret)

    # -- KV helpers ---------------------------------------------------------
    def _write_kv(self, *, path: str, mount: str, secret: dict[str, str]) -> int:
        try:
            resp = self._client.secrets.kv.v2.create_or_update_secret(
                mount_point=mount, path=path, secret=secret
            )
        except Exception as exc:
            raise CredentialRotationError(
                f"Vault KV write failed for {path!r}: {type(exc).__name__}"
            ) from exc
        version = ((resp or {}).get("data") or {}).get("version")
        if version is None:
            raise CredentialRotationError(
                f"Vault KV write for {path!r} returned no version — cannot audit "
                "the rotation, so it is treated as failed."
            )
        return int(version)

    def _read_kv(self, *, path: str, mount: str) -> dict[str, str]:
        try:
            resp = self._client.secrets.kv.v2.read_secret_version(mount_point=mount, path=path)
        except Exception as exc:
            if _is_invalid_path(exc):
                return {}
            raise CredentialRotationError(
                f"Vault KV read failed for {path!r}: {type(exc).__name__}"
            ) from exc
        data = ((resp or {}).get("data") or {}).get("data") or {}
        return {str(k): str(v) for k, v in data.items()}


def revoke_previous_minio_credential(
    client: HvacVaultRotationClient,
    rotator: MinioCredentialRotator,
    *,
    path: str = "platform/minio",
    mount: str = PLATFORM_KV_MOUNT,
) -> str | None:
    """Step 4 of add-then-remove: revoke the credential the rotation replaced.

    Call this ONLY after the new value has reached every service (the restart of
    task_prod05_06 / ADR 0144). Running it too early takes object storage down
    for the whole platform, which is why it is a separate function with this
    docstring rather than a tail-end of the rotation cycle.

    Returns the revoked access key, or ``None`` when there was nothing to revoke
    (first rotation ever, or already revoked — the call is idempotent).
    """
    entry = client._read_kv(path=path, mount=mount)  # - same module's seam
    previous = entry.get(KV_FIELD_PREVIOUS_ACCESS_KEY)
    if not previous:
        _log.info("credential_rotation.minio.nothing_to_revoke", path=path)
        return None
    rotator.revoke(previous)
    # Clear the marker AND the pointer in one write: the entry now describes a
    # credential that is live and alone.
    cleaned = {k: v for k, v in entry.items() if k != KV_FIELD_PREVIOUS_ACCESS_KEY}
    cleaned[KV_FIELD_PENDING_APPLY] = "false"
    client._write_kv(path=path, mount=mount, secret=cleaned)
    return previous


def _is_invalid_path(exc: Exception) -> bool:
    """hvac's "path not found", detected by class name so we never import hvac."""
    return type(exc).__name__ == "InvalidPath" or "InvalidPath" in {
        cls.__name__ for cls in type(exc).__mro__
    }


__all__ = [
    "KV_FIELD_ACCESS_KEY",
    "KV_FIELD_PENDING_APPLY",
    "KV_FIELD_PREVIOUS_ACCESS_KEY",
    "KV_FIELD_SECRET_KEY",
    "KV_FIELD_VALUE",
    "MINIO_SECRET_NAME",
    "HvacVaultRotationClient",
    "MinioCredentialRotator",
    "MinioServiceAccountRotator",
    "revoke_previous_minio_credential",
]
