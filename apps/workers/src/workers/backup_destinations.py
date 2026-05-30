"""Remote backup destinations (Plan 12 Phase B — task_12_05 onward).

Phase A (:mod:`workers.backup`) writes a *timestamped bundle directory* to local
disk and prunes by a 7-day retention window. Phase B adds the OTHER half of the
plan's storage policy — *"Retención local 7 días + destinos remotos opcionales
(S3, B2, SFTP/NAS, rclone genérico)"*: after a successful, verified backup the
(optionally encrypted) bundle is uploaded to every configured + enabled remote
destination.

One common interface, per-backend adapters
-------------------------------------------
Every backend (S3, Backblaze B2, SFTP/NAS, rclone) speaks the same
:class:`BackupDestination` Protocol:

    upload(bundle_path)              -> UploadResult
    list_remote()                    -> tuple[RemoteEntry, ...]
    download(name, dest)             -> Path
    test_connectivity()              -> ConnectivityResult

so the backup flow (and the admin "test connectivity" button, task_12_09) talk to
a destination without knowing which backend it is. A concrete adapter per backend
is registered by ``type`` (``"s3"``, ``"b2"``, ``"sftp"``, ``"rclone"``); this
module ships the S3 adapter (task_12_05) and the registry. Later tasks add the
rest behind the SAME Protocol.

The mock-not-fake reality
--------------------------
No real S3/B2/SFTP/rclone endpoint is reachable in tests, so each adapter hides
its backend client behind an injectable seam and tests inject a mock. The
S3 adapter takes a ``client_factory`` (defaults to a real boto3 ``client("s3")``);
tests pass a factory returning a mocked boto3 client and assert the adapter issues
the correct calls (right bucket/key, ``upload_file`` for multipart, ``list_objects_v2``
on the prefix, ``head_bucket`` for connectivity) and that an S3 error maps to a
typed :class:`DestinationError`.

Credentials are secrets
------------------------
Destination credentials (S3 access key + secret, SFTP password/key) are resolved
through the workers' secret seam (:class:`workers.secrets.SecretsProvider` —
``fetch(keys) -> {name: value}``, the same Protocol the backup-encryption key and
the agent-runtime credential injector use). They are NEVER read from plaintext
config, NEVER written to the manifest, and NEVER logged — we log the secret *key
names*, never their values, mirroring :mod:`workers.backup_encryption`.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog

from workers.backup import CommandResult, CommandRunner, SubprocessRunner
from workers.secrets import SecretsProvider

if TYPE_CHECKING:  # pragma: no cover — typing only, never imported at runtime
    import paramiko
    from mypy_boto3_s3.client import S3Client

_log = structlog.get_logger("workers.backup_destinations")


class DestinationError(RuntimeError):
    """Raised when a remote destination operation fails.

    Every adapter funnels its backend's native error (a boto3
    ``botocore.exceptions.ClientError``, a paramiko ``SSHException``, an rclone
    non-zero exit, …) into this one type so the backup flow gets a single,
    non-leaky error class. The underlying cause is chained (``from exc``) but the
    message never echoes credential material.
    """


@dataclass(frozen=True)
class UploadResult:
    """Outcome of uploading one bundle to one destination."""

    destination: str  # the destination's logical name
    remote_uri: str  # where it landed, e.g. "s3://bucket/prefix/20260530T030000Z.tar.enc"
    size_bytes: int


@dataclass(frozen=True)
class RemoteEntry:
    """One backup object already present at the destination."""

    name: str  # the object key / file name (bundle id-ish)
    size_bytes: int


@dataclass(frozen=True)
class ConnectivityResult:
    """Result of a cheap reachability/auth probe against a destination.

    ``ok`` is the headline; ``detail`` is a short, non-leaky human string for the
    admin UI (task_12_09). On failure ``detail`` carries the mapped error class /
    message, NEVER the credential.
    """

    ok: bool
    detail: str = ""


@runtime_checkable
class BackupDestination(Protocol):
    """The one interface every remote backup backend implements.

    ``name`` is a stable logical identifier for logs + the manifest. The four
    methods are the whole contract the backup flow + admin UI rely on; a new
    backend is a new class implementing exactly this, registered by type.
    """

    @property
    def name(self) -> str: ...

    def upload(self, bundle_path: Path) -> UploadResult: ...

    def list_remote(self) -> tuple[RemoteEntry, ...]: ...

    def download(self, name: str, dest: Path) -> Path: ...

    def test_connectivity(self) -> ConnectivityResult: ...


# ---------------------------------------------------------------------------
# S3 destination (task_12_05) — works with ANY S3-compatible provider.
# ---------------------------------------------------------------------------

# The well-known secret field names the S3 adapter resolves through the secret
# seam. NEVER plaintext config: the values come from Vault/env, keyed by these
# names. Mirrors workers.backup_encryption.VAULT_BACKUP_KEY_FIELD.
S3_ACCESS_KEY_FIELD = "backup_s3_access_key_id"
S3_SECRET_KEY_FIELD = "backup_s3_secret_access_key"


@dataclass(frozen=True)
class S3DestinationConfig:
    """Operator-tunable, NON-secret config for an S3 destination.

    These are the knobs that belong in platform_settings/config (bucket,
    prefix/path, endpoint, region). The access key + secret are NOT here — they
    are secrets resolved through the secret seam at upload time. ``endpoint_url``
    is the lever that makes ANY S3-compatible provider work (MinIO, Backblaze B2,
    Wasabi, Cloudflare R2, …): leave it empty for AWS, set it to the provider's
    endpoint otherwise.
    """

    bucket: str
    # Key prefix (a "folder") under which bundles are stored. Normalised to no
    # leading slash + a single trailing slash so key joining is unambiguous.
    prefix: str = ""
    endpoint_url: str | None = None
    region: str | None = None
    # Logical name for logs/manifest. Defaults to "s3" but the operator can name
    # it (e.g. "offsite-wasabi") when several S3 destinations coexist.
    name: str = "s3"
    # The secret-seam field names the access key + secret are resolved from.
    # Operator can repoint them when several S3 destinations have distinct creds.
    access_key_field: str = S3_ACCESS_KEY_FIELD
    secret_key_field: str = S3_SECRET_KEY_FIELD

    @classmethod
    def from_settings(cls, settings: Any) -> S3DestinationConfig:
        """Build the S3 destination config from the workers :class:`Settings`.

        Reads only the NON-secret tunables (bucket, prefix, endpoint, region);
        the access key + secret stay in the secret seam. ``endpoint_url`` /
        ``region`` are normalised to ``None`` when blank so boto3 falls back to
        its AWS defaults.
        """
        return cls(
            bucket=str(settings.backup_s3_bucket),
            prefix=str(settings.backup_s3_prefix),
            endpoint_url=str(settings.backup_s3_endpoint_url) or None,
            region=str(settings.backup_s3_region) or None,
        )

    def normalized_prefix(self) -> str:
        """Prefix with no leading slash and exactly one trailing slash (or '')."""
        p = self.prefix.strip().strip("/")
        return f"{p}/" if p else ""

    def key_for(self, name: str) -> str:
        """The full object key for a bundle file name under the prefix."""
        return self.normalized_prefix() + name

    def uri_for(self, key: str) -> str:
        """A human ``s3://bucket/key`` URI for logs/results."""
        return f"s3://{self.bucket}/{key}"


# A factory that builds a boto3-style S3 client. Production uses the real
# ``boto3.client("s3", ...)``; tests inject a factory returning a mock. Kept as a
# plain callable type so neither boto3 nor its stubs are needed at runtime here.
if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    S3ClientFactory = Callable[..., "S3Client"]


@dataclass
class S3Destination:
    """Upload a backup bundle to S3 (or any S3-compatible provider) via boto3.

    The boto3 client is built lazily through ``client_factory`` so:

      * no network/credential resolution happens at construction time;
      * tests inject a factory returning a mocked client (no boto3 import, no AWS).

    Credentials are resolved through the :class:`workers.secrets.SecretsProvider`
    seam at client-build time and handed to boto3 as ``aws_access_key_id`` /
    ``aws_secret_access_key`` — never read from the (non-secret) config, never
    logged. ``endpoint_url`` is forwarded so a non-AWS provider works unchanged.
    """

    config: S3DestinationConfig
    secrets: SecretsProvider
    # Defaults to None → the real boto3 factory is wired lazily in _build_client.
    client_factory: Any | None = None
    _client: Any | None = field(default=None, init=False, repr=False)

    @property
    def name(self) -> str:
        return self.config.name

    # -- client wiring ------------------------------------------------------

    def _resolve_credentials(self) -> tuple[str, str]:
        """Resolve (access_key, secret_key) through the secret seam.

        The raw values live only in this local scope + inside the boto3 client;
        they are NEVER logged or put in a result/manifest. We log only the field
        NAMES, mirroring the encryption layer.
        """
        cfg = self.config
        try:
            fetched = self.secrets.fetch([cfg.access_key_field, cfg.secret_key_field])
        except KeyError as exc:
            # StaticSecretsProvider raises on an absent key; normalise to our type.
            raise DestinationError(
                f"secret provider is missing S3 credentials "
                f"({cfg.access_key_field!r} / {cfg.secret_key_field!r})"
            ) from exc
        access_key = fetched.get(cfg.access_key_field)
        secret_key = fetched.get(cfg.secret_key_field)
        if not access_key or not secret_key:
            raise DestinationError(
                f"secret provider returned no value for S3 credentials "
                f"({cfg.access_key_field!r} / {cfg.secret_key_field!r})"
            )
        _log.debug(
            "backup.dest.s3.credentials_resolved",
            destination=cfg.name,
            access_key_field=cfg.access_key_field,
            secret_key_field=cfg.secret_key_field,
        )
        return access_key, secret_key

    def _build_client(self) -> Any:
        """Build (once) and cache the boto3 S3 client with resolved creds."""
        if self._client is None:
            access_key, secret_key = self._resolve_credentials()
            factory = self.client_factory or _default_boto3_factory
            self._client = factory(
                service_name="s3",
                endpoint_url=self.config.endpoint_url,
                region_name=self.config.region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
        return self._client

    # -- overridable hooks ---------------------------------------------------

    def _upload_kwargs(self) -> dict[str, Any]:
        """Extra keyword args forwarded to boto3's managed ``upload_file``.

        Empty for vanilla S3 — the SDK defaults (5 MiB multipart threshold /
        part size) are fine for AWS and most S3-compatible providers. A subclass
        (e.g. :class:`B2Destination`) overrides this to inject a
        ``Config=TransferConfig(...)`` that tunes the multipart part sizing to a
        provider's quirks. Kept as the single seam so the rest of ``upload`` is
        backend-agnostic.
        """
        return {}

    # -- BackupDestination ---------------------------------------------------

    def upload(self, bundle_path: Path) -> UploadResult:
        """Upload ``bundle_path`` (a file) to ``s3://bucket/prefix/<name>``.

        Uses ``upload_file`` — boto3's managed transfer that AUTOMATICALLY does a
        multipart upload for large files (Plan 12: "multipart for large files")
        and a single PUT for small ones, so one call covers both. Maps any boto3
        client error to :class:`DestinationError`.
        """
        bundle_path = Path(bundle_path)
        if not bundle_path.is_file():
            # Phase A's encrypted bundle is a single .enc file; the plaintext
            # bundle is a directory. The remote-upload path expects a single
            # artifact file (the caller tars a plaintext bundle first).
            raise DestinationError(f"S3 upload expects a single bundle file, not {bundle_path!s}")
        client = self._build_client()
        key = self.config.key_for(bundle_path.name)
        try:
            client.upload_file(str(bundle_path), self.config.bucket, key, **self._upload_kwargs())
        except Exception as exc:  # botocore.exceptions.ClientError / BotoCoreError
            raise DestinationError(
                f"S3 upload to {self.config.uri_for(key)} failed: {_safe_error(exc)}"
            ) from exc
        size = bundle_path.stat().st_size
        uri = self.config.uri_for(key)
        _log.info("backup.dest.s3.uploaded", destination=self.config.name, uri=uri, size_bytes=size)
        return UploadResult(destination=self.config.name, remote_uri=uri, size_bytes=size)

    def list_remote(self) -> tuple[RemoteEntry, ...]:
        """List the bundles already stored under the configured prefix.

        Paginates ``list_objects_v2`` so a destination with more than one page of
        objects still returns every entry. The prefix is stripped from each key
        so the returned ``name`` is the bare bundle file name.
        """
        client = self._build_client()
        prefix = self.config.normalized_prefix()
        entries: list[RemoteEntry] = []
        try:
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.config.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    full_key = obj["Key"]
                    if full_key.endswith("/"):
                        continue  # a "folder" placeholder, not a bundle
                    name = full_key[len(prefix) :] if prefix else full_key
                    entries.append(RemoteEntry(name=name, size_bytes=int(obj.get("Size", 0))))
        except Exception as exc:
            raise DestinationError(
                f"S3 list of {self.config.uri_for(prefix)} failed: {_safe_error(exc)}"
            ) from exc
        return tuple(entries)

    def download(self, name: str, dest: Path) -> Path:
        """Fetch a single remote bundle by ``name`` to local ``dest``.

        ``name`` is the bare file name (as returned by :meth:`list_remote`); the
        prefix is re-applied to build the key. ``dest`` may be a directory (the
        file is written under it as ``name``) or a target file path.
        """
        dest = Path(dest)
        target = dest / name if dest.is_dir() else dest
        target.parent.mkdir(parents=True, exist_ok=True)
        client = self._build_client()
        key = self.config.key_for(name)
        try:
            client.download_file(self.config.bucket, key, str(target))
        except Exception as exc:
            raise DestinationError(
                f"S3 download of {self.config.uri_for(key)} failed: {_safe_error(exc)}"
            ) from exc
        _log.info(
            "backup.dest.s3.downloaded",
            destination=self.config.name,
            uri=self.config.uri_for(key),
            dest=str(target),
        )
        return target

    def test_connectivity(self) -> ConnectivityResult:
        """Cheap reachability + auth probe: ``head_bucket`` on the configured bucket.

        ``head_bucket`` is the canonical zero-payload S3 auth/existence check — it
        proves the credentials are valid AND the bucket is reachable without
        listing or transferring anything. Returns a typed result (never raises) so
        the admin UI can render OK/FAIL; the detail is non-leaky.
        """
        try:
            client = self._build_client()
            client.head_bucket(Bucket=self.config.bucket)
        except DestinationError as exc:
            # Credential resolution failed before we even reached S3.
            return ConnectivityResult(ok=False, detail=str(exc))
        except Exception as exc:
            return ConnectivityResult(
                ok=False,
                detail=f"head_bucket on {self.config.bucket!r} failed: {_safe_error(exc)}",
            )
        return ConnectivityResult(ok=True, detail=f"bucket {self.config.bucket!r} reachable")


def _default_boto3_factory(**kwargs: Any) -> Any:
    """Build a real boto3 S3 client. Imported lazily so boto3 is only required
    in production / when no test factory is injected."""
    import boto3

    return boto3.client(**kwargs)


# ---------------------------------------------------------------------------
# Backblaze B2 destination (task_12_06) — S3-compatible, with quirks.
# ---------------------------------------------------------------------------
#
# B2's S3-compatible API IS S3, so we reuse the whole S3 adapter rather than
# duplicate upload/list/download/connectivity. What differs are three quirks the
# B2 layer handles:
#
#   1. ENDPOINT — B2 exposes its S3 API at ``s3.<region>.backblazeb2.com`` (e.g.
#      ``us-west-002`` → ``https://s3.us-west-002.backblazeb2.com``). The operator
#      configures only the REGION; the endpoint is derived. region_name is also
#      set so boto3's SigV4 signing uses the matching region.
#   2. MULTIPART PART SIZE — B2's S3-compatible multipart requires every part
#      except the last to be at least 5 MB, and large objects upload far more
#      reliably with bigger parts. boto3's default 8 MiB chunk is acceptable but
#      we pin an explicit, B2-friendly part size + threshold via a TransferConfig
#      so the behaviour is deterministic and not at the mercy of an SDK default.
#   3. AUTH — B2's application keyId / applicationKey map straight onto S3's
#      access_key_id / secret_access_key, but they are DISTINCT secrets, so the
#      B2 destination resolves them from their own secret-seam field names.

# B2's S3-compatible multipart minimum part size is 5 MB; we use 100 MB parts
# (well above the floor) so very large encrypted bundles upload in sane chunks.
# boto3 switches to multipart once a file exceeds the threshold; below it a
# single PUT is used. Both are part of the deterministic B2 tuning.
B2_MULTIPART_THRESHOLD_BYTES = 100 * 1024 * 1024
B2_MULTIPART_CHUNKSIZE_BYTES = 100 * 1024 * 1024

# The secret-seam field names the B2 adapter resolves. DISTINCT from the S3 ones
# (a deployment may use both): B2 application keyId + applicationKey.
B2_KEY_ID_FIELD = "backup_b2_key_id"
B2_APPLICATION_KEY_FIELD = "backup_b2_application_key"


def b2_endpoint_url(region: str) -> str:
    """Derive B2's S3-compatible endpoint URL from a region.

    ``us-west-002`` → ``https://s3.us-west-002.backblazeb2.com``. This is the
    documented B2 S3 endpoint form; the region also drives SigV4 signing.
    """
    r = region.strip().strip("/")
    if not r:
        raise DestinationError("B2 destination requires a region (e.g. 'us-west-002')")
    return f"https://s3.{r}.backblazeb2.com"


@dataclass(frozen=True)
class B2DestinationConfig(S3DestinationConfig):
    """NON-secret config for a Backblaze B2 destination.

    Reuses :class:`S3DestinationConfig` (B2 IS S3) but the operator configures a
    REGION, not an endpoint — the S3-compatible endpoint is derived from it. The
    application keyId / key are NOT here (they are secrets); the field names
    default to the B2-specific secret-seam keys.
    """

    name: str = "b2"
    access_key_field: str = B2_KEY_ID_FIELD
    secret_key_field: str = B2_APPLICATION_KEY_FIELD

    @classmethod
    def from_b2_settings(cls, settings: Any) -> B2DestinationConfig:
        """Build the B2 destination config from the workers :class:`Settings`.

        Reads only the NON-secret tunables (bucket, prefix, region); the
        application key id + key stay in the secret seam. The endpoint is derived
        from the region (B2 quirk #1) and ``region`` is preserved so SigV4 signing
        matches.
        """
        region = str(settings.backup_b2_region)
        return cls(
            bucket=str(settings.backup_b2_bucket),
            prefix=str(settings.backup_b2_prefix),
            endpoint_url=b2_endpoint_url(region),
            region=region,
        )

    def uri_for(self, key: str) -> str:
        """A human ``b2://bucket/key`` URI for logs/results (distinct from s3://)."""
        return f"b2://{self.bucket}/{key}"


@dataclass
class B2Destination(S3Destination):
    """Upload a backup bundle to Backblaze B2 via its S3-compatible API.

    Subclasses :class:`S3Destination` — the upload/list/download/connectivity
    behaviour is identical because B2 speaks S3. Only the quirks are layered on:

      * the endpoint is the B2 S3 endpoint derived from the region (handled by
        :class:`B2DestinationConfig`, forwarded to boto3 unchanged);
      * multipart part sizing is pinned to B2-friendly values via a
        ``TransferConfig`` (overridden :meth:`_upload_kwargs`);
      * credentials are the B2 application keyId / key, resolved from the
        B2-specific secret-seam field names (again via the config).

    Credentials are resolved through the secret seam at client-build time exactly
    as the S3 adapter does — never plaintext, never logged.
    """

    config: B2DestinationConfig

    def _upload_kwargs(self) -> dict[str, Any]:
        """Pin B2-friendly multipart sizing (quirk #2) via a boto3 TransferConfig.

        Imported lazily so boto3 is only required in production / when uploading
        for real; tests inject a mock client and assert the ``Config`` it received
        carries the B2 part sizing.
        """
        from boto3.s3.transfer import TransferConfig

        return {
            "Config": TransferConfig(
                multipart_threshold=B2_MULTIPART_THRESHOLD_BYTES,
                multipart_chunksize=B2_MULTIPART_CHUNKSIZE_BYTES,
            )
        }


# ---------------------------------------------------------------------------
# SFTP / NAS destination (task_12_07) — paramiko over SSH.
# ---------------------------------------------------------------------------
#
# A NAS or any SSH-reachable host is a first-class remote destination (Plan 12:
# "destinos remotos opcionales (S3, B2, SFTP/NAS, rclone)"). It speaks the SAME
# BackupDestination Protocol as the S3/B2 adapters — upload / list_remote /
# download / test_connectivity — so the backup flow + the admin "test
# connectivity" button treat it identically.
#
# Backend client behind a seam
# -----------------------------
# paramiko opens a real TCP+SSH session, which is unreachable in tests, so the
# transport is built lazily through an injectable ``transport_factory``.
# Production wires the real paramiko ``SSHClient``→``SFTPClient`` (see
# :func:`_default_paramiko_transport`); tests inject a factory returning a mock
# SFTP client and assert the adapter issues the right calls (``put`` to
# host:path, ``listdir_attr`` on the dir, ``get`` for download, ``stat`` for the
# connectivity probe) and that an auth/transport error maps to DestinationError.
#
# Auth + host-key handling
# -------------------------
# Auth is password OR a private key, BOTH resolved through the secret seam (never
# plaintext config, never logged — we log only the field NAMES). The host-key
# policy is configurable: ``"reject"`` (default, safest — the host must be in a
# known_hosts file), ``"auto_add"`` (trust-on-first-use), or ``"warn"``. We never
# silently disable host-key checking beyond what the operator opts into.

# Secret-seam field names the SFTP adapter resolves. Password and private key are
# alternatives — whichever the operator provisioned. NEVER plaintext config.
SFTP_PASSWORD_FIELD = "backup_sftp_password"
SFTP_PRIVATE_KEY_FIELD = "backup_sftp_private_key"
SFTP_PRIVATE_KEY_PASSPHRASE_FIELD = "backup_sftp_private_key_passphrase"

# Default SSH port.
_SFTP_DEFAULT_PORT = 22

# Host-key policy names the operator may configure (NON-secret tunable).
_HOST_KEY_POLICIES = ("reject", "auto_add", "warn")


@dataclass(frozen=True)
class SftpDestinationConfig:
    """Operator-tunable, NON-secret config for an SFTP/NAS destination.

    The knobs that belong in platform_settings/config: host, port, remote path,
    username, and host-key policy. The password / private key are NOT here — they
    are secrets resolved through the secret seam at connect time.

    ``host_key_policy`` controls how an unknown server host key is handled:

      * ``"reject"`` (default) — the host MUST already be in the known-hosts file;
        an unknown key aborts the connection. Safest.
      * ``"auto_add"`` — trust-on-first-use: an unknown key is accepted + added.
      * ``"warn"`` — accept but log a warning.
    """

    host: str
    remote_path: str
    username: str
    port: int = _SFTP_DEFAULT_PORT
    host_key_policy: str = "reject"
    # Optional path to a known_hosts file loaded before connecting (used by the
    # "reject"/"warn" policies). Empty = paramiko's system host keys only.
    known_hosts_path: str = ""
    # Logical name for logs/manifest. Defaults to "sftp"; operator can name it
    # (e.g. "nas-offsite") when several SFTP destinations coexist.
    name: str = "sftp"
    # The secret-seam field names the password / private key / passphrase are
    # resolved from. Operator can repoint them per destination.
    password_field: str = SFTP_PASSWORD_FIELD
    private_key_field: str = SFTP_PRIVATE_KEY_FIELD
    private_key_passphrase_field: str = SFTP_PRIVATE_KEY_PASSPHRASE_FIELD

    def __post_init__(self) -> None:
        if self.host_key_policy not in _HOST_KEY_POLICIES:
            raise DestinationError(
                f"invalid SFTP host_key_policy {self.host_key_policy!r}; "
                f"must be one of {_HOST_KEY_POLICIES}"
            )

    @classmethod
    def from_settings(cls, settings: Any) -> SftpDestinationConfig:
        """Build the SFTP destination config from the workers :class:`Settings`.

        Reads only the NON-secret tunables (host, port, remote path, username,
        host-key policy); the password / private key stay in the secret seam.
        """
        return cls(
            host=str(settings.backup_sftp_host),
            remote_path=str(settings.backup_sftp_path),
            username=str(settings.backup_sftp_username),
            port=int(settings.backup_sftp_port),
            host_key_policy=str(settings.backup_sftp_host_key_policy),
            known_hosts_path=str(settings.backup_sftp_known_hosts_path),
        )

    def normalized_path(self) -> str:
        """Remote directory with no trailing slash (or '' for the SFTP cwd)."""
        return self.remote_path.rstrip("/")

    def remote_file(self, name: str) -> str:
        """The full remote path for a bundle file name under the remote dir.

        POSIX join (SFTP paths are always ``/``-separated regardless of the
        worker host OS — never use os.path.join here on Windows).
        """
        base = self.normalized_path()
        return f"{base}/{name}" if base else name

    def uri_for(self, remote_file: str) -> str:
        """A human ``sftp://host:port/path`` URI for logs/results (no creds)."""
        return f"sftp://{self.host}:{self.port}/{remote_file.lstrip('/')}"


# A factory that opens an SFTP session and returns a (closeable) SFTP client.
# Production uses the real paramiko transport; tests inject a factory returning a
# mock. Kept as a plain callable type so paramiko is not needed at runtime here.
if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable as _Callable

    SftpTransportFactory = _Callable[..., "paramiko.SFTPClient"]


@dataclass
class SftpDestination:
    """Upload a backup bundle to an SFTP/NAS host via paramiko.

    The SFTP session is opened lazily through ``transport_factory`` so:

      * no network/credential resolution happens at construction time;
      * tests inject a factory returning a mocked SFTP client (no paramiko
        import, no real SSH).

    Credentials (password OR private key) are resolved through the
    :class:`workers.secrets.SecretsProvider` seam at connect time and handed to
    the factory — never read from the (non-secret) config, never logged.
    """

    config: SftpDestinationConfig
    secrets: SecretsProvider
    # Defaults to None → the real paramiko factory is wired lazily in _connect.
    transport_factory: Any | None = None
    _client: Any | None = field(default=None, init=False, repr=False)

    @property
    def name(self) -> str:
        return self.config.name

    # -- credential + session wiring ----------------------------------------

    def _resolve_credentials(self) -> dict[str, str]:
        """Resolve the SFTP auth material through the secret seam.

        Returns a dict with whichever of ``password`` / ``private_key`` /
        ``private_key_passphrase`` the secret provider supplies. AT LEAST one of
        password / private_key must be present, else a typed
        :class:`DestinationError` (no usable auth). The raw values live only in
        this local scope + inside the paramiko session; they are NEVER logged or
        put in a result/manifest. We log only the field NAMES.
        """
        cfg = self.config
        wanted = [cfg.password_field, cfg.private_key_field, cfg.private_key_passphrase_field]
        try:
            fetched = self.secrets.fetch(wanted)
        except KeyError:
            # StaticSecretsProvider raises on an absent key; the optional fields
            # may legitimately be missing, so fall back to per-key best-effort.
            fetched = {}
            for key in wanted:
                try:
                    fetched.update(self.secrets.fetch([key]))
                except KeyError:
                    continue

        creds: dict[str, str] = {}
        password = fetched.get(cfg.password_field)
        private_key = fetched.get(cfg.private_key_field)
        passphrase = fetched.get(cfg.private_key_passphrase_field)
        if password:
            creds["password"] = password
        if private_key:
            creds["private_key"] = private_key
        if passphrase:
            creds["private_key_passphrase"] = passphrase

        if "password" not in creds and "private_key" not in creds:
            raise DestinationError(
                f"secret provider returned no SFTP auth material "
                f"(need a password {cfg.password_field!r} or a private key "
                f"{cfg.private_key_field!r})"
            )
        _log.debug(
            "backup.dest.sftp.credentials_resolved",
            destination=cfg.name,
            auth="private_key" if "private_key" in creds else "password",
            password_field=cfg.password_field,
            private_key_field=cfg.private_key_field,
        )
        return creds

    def _connect(self) -> Any:
        """Open (once) and cache the SFTP client with resolved credentials."""
        if self._client is None:
            creds = self._resolve_credentials()
            factory = self.transport_factory or _default_paramiko_transport
            self._client = factory(
                host=self.config.host,
                port=self.config.port,
                username=self.config.username,
                host_key_policy=self.config.host_key_policy,
                known_hosts_path=self.config.known_hosts_path or None,
                **creds,
            )
        return self._client

    # -- BackupDestination ---------------------------------------------------

    def upload(self, bundle_path: Path) -> UploadResult:
        """Upload ``bundle_path`` (a file) to ``host:remote_path/<name>`` via SFTP.

        Uses paramiko's ``put`` (SFTP write). Maps any paramiko / transport error
        to :class:`DestinationError`.
        """
        bundle_path = Path(bundle_path)
        if not bundle_path.is_file():
            # Mirrors the S3 adapter: the remote-upload path expects a single
            # artifact file (the caller tars a plaintext bundle first).
            raise DestinationError(f"SFTP upload expects a single bundle file, not {bundle_path!s}")
        client = self._connect()
        remote = self.config.remote_file(bundle_path.name)
        try:
            client.put(str(bundle_path), remote)
        except Exception as exc:  # paramiko SSHException / SFTPError / OSError
            raise DestinationError(
                f"SFTP upload to {self.config.uri_for(remote)} failed: {_safe_error(exc)}"
            ) from exc
        size = bundle_path.stat().st_size
        uri = self.config.uri_for(remote)
        _log.info(
            "backup.dest.sftp.uploaded", destination=self.config.name, uri=uri, size_bytes=size
        )
        return UploadResult(destination=self.config.name, remote_uri=uri, size_bytes=size)

    def list_remote(self) -> tuple[RemoteEntry, ...]:
        """List the bundle files already present in the remote directory.

        Uses ``listdir_attr`` so each entry carries its size. Directory entries
        (a nested folder) are skipped — only regular files are bundles.
        """
        client = self._connect()
        path = self.config.normalized_path() or "."
        entries: list[RemoteEntry] = []
        try:
            for attr in client.listdir_attr(path):
                if _sftp_attr_is_dir(attr):
                    continue
                entries.append(
                    RemoteEntry(
                        name=attr.filename, size_bytes=int(getattr(attr, "st_size", 0) or 0)
                    )
                )
        except Exception as exc:
            raise DestinationError(
                f"SFTP list of {self.config.uri_for(path)} failed: {_safe_error(exc)}"
            ) from exc
        return tuple(entries)

    def download(self, name: str, dest: Path) -> Path:
        """Fetch a single remote bundle by ``name`` to local ``dest`` via SFTP.

        ``name`` is the bare file name (as returned by :meth:`list_remote`); the
        remote dir is re-applied to build the path. ``dest`` may be a directory
        (the file is written under it as ``name``) or a target file path.
        """
        dest = Path(dest)
        target = dest / name if dest.is_dir() else dest
        target.parent.mkdir(parents=True, exist_ok=True)
        client = self._connect()
        remote = self.config.remote_file(name)
        try:
            client.get(remote, str(target))
        except Exception as exc:
            raise DestinationError(
                f"SFTP download of {self.config.uri_for(remote)} failed: {_safe_error(exc)}"
            ) from exc
        _log.info(
            "backup.dest.sftp.downloaded",
            destination=self.config.name,
            uri=self.config.uri_for(remote),
            dest=str(target),
        )
        return target

    def test_connectivity(self) -> ConnectivityResult:
        """Cheap reachability + auth probe: open a session + ``stat`` the path.

        Opening the SFTP session proves the host is reachable AND the credentials
        authenticate; ``stat`` on the remote directory proves it exists + is
        accessible. Returns a typed result (never raises) so the admin UI can
        render OK/FAIL; the detail is non-leaky.
        """
        path = self.config.normalized_path() or "."
        try:
            client = self._connect()
            client.stat(path)
        except DestinationError as exc:
            # Credential resolution / connect failed before/while reaching the host.
            return ConnectivityResult(ok=False, detail=str(exc))
        except Exception as exc:
            return ConnectivityResult(
                ok=False,
                detail=f"stat of {path!r} on {self.config.host!r} failed: {_safe_error(exc)}",
            )
        return ConnectivityResult(
            ok=True, detail=f"{self.config.host!r} reachable, path {path!r} present"
        )


def _sftp_attr_is_dir(attr: Any) -> bool:
    """True if a paramiko ``SFTPAttributes`` entry is a directory.

    paramiko exposes the POSIX mode in ``st_mode``; directory bit is ``S_ISDIR``.
    Defensive against a mock/attr missing st_mode (treated as a file).
    """
    import stat as _stat

    mode = getattr(attr, "st_mode", None)
    if mode is None:
        return False
    return _stat.S_ISDIR(mode)


def _default_paramiko_transport(
    *,
    host: str,
    port: int,
    username: str,
    host_key_policy: str,
    known_hosts_path: str | None,
    password: str | None = None,
    private_key: str | None = None,
    private_key_passphrase: str | None = None,
) -> Any:
    """Open a real paramiko SFTP session and return the SFTPClient.

    Imported lazily so paramiko is only required in production / when no test
    factory is injected. Auth is password OR a private key (the key material is
    parsed from its PEM string — never written to disk). The host-key policy is
    applied per the operator's config.
    """
    import io

    import paramiko

    client = paramiko.SSHClient()
    if known_hosts_path:
        client.load_host_keys(known_hosts_path)
    else:
        client.load_system_host_keys()
    policy: paramiko.MissingHostKeyPolicy
    if host_key_policy == "auto_add":
        policy = paramiko.AutoAddPolicy()
    elif host_key_policy == "warn":
        policy = paramiko.WarningPolicy()
    else:  # "reject" (default) — safest
        policy = paramiko.RejectPolicy()
    client.set_missing_host_key_policy(policy)

    pkey: paramiko.PKey | None = None
    if private_key:
        pkey = paramiko.RSAKey.from_private_key(
            io.StringIO(private_key), password=private_key_passphrase or None
        )
    client.connect(
        hostname=host,
        port=port,
        username=username,
        password=password or None,
        pkey=pkey,
        look_for_keys=False,
        allow_agent=False,
    )
    sftp = client.open_sftp()
    return sftp


# ---------------------------------------------------------------------------
# Generic rclone destination (task_12_08) — ANY rclone backend.
# ---------------------------------------------------------------------------
#
# rclone (https://rclone.org) speaks ~70 storage backends (Google Drive,
# Dropbox, OneDrive, Azure Blob, SFTP, S3, WebDAV, …) through one CLI. Wrapping
# it as a BackupDestination makes the platform's remote-destination catalogue
# open-ended without a bespoke adapter per provider (Plan 12: "rclone genérico
# (cualquier backend)"). It speaks the SAME BackupDestination Protocol as the
# S3/B2/SFTP adapters so the backup flow + the admin "test connectivity" button
# treat it identically.
#
# Backend behind the CommandRunner seam
# -------------------------------------
# rclone is a subprocess, not a Python client, so the adapter shells out through
# the SAME injectable :class:`workers.backup.CommandRunner` the backup engine
# uses (production = SubprocessRunner with explicit argv, never shell=True; tests
# inject a fake that records argv + fabricates artifacts). Tests assert each op
# builds the correct rclone argv against the temp config and that a non-zero exit
# maps to a typed :class:`DestinationError`.
#
# Config blob (creds) is a secret, written to a temp file — never argv/log
# ---------------------------------------------------------------------------
# An rclone remote is configured by a chunk of ``rclone.conf`` (an INI section
# whose body carries OBSCURED tokens / keys / passwords). That whole blob is a
# SECRET: it is resolved through the secret seam, written to a private temp
# ``rclone.conf`` chmod 0600, passed to rclone via ``--config <path>`` (so the
# creds are in the FILE, never on the command line / in the process table / in
# logs), and the temp file is removed in a finally after the op. We log only the
# remote name + the secret FIELD name, never the blob.

# The secret-seam field name the rclone config blob is resolved from. NEVER
# plaintext config: the value (an `rclone.conf` section body with obscured creds)
# comes from Vault/env, keyed by this name. Mirrors the S3/SFTP field constants.
RCLONE_CONFIG_FIELD = "backup_rclone_config"

# rclone's own marker for "config file is fully supplied, do not prompt / read
# the user's default config". Belt-and-braces alongside the explicit --config.
_RCLONE_TEMP_CONF_NAME = "rclone.conf"


@dataclass(frozen=True)
class RcloneDestinationConfig:
    """Operator-tunable, NON-secret config for a generic rclone destination.

    The knobs that belong in platform_settings/config: the rclone REMOTE NAME
    (the ``[name]`` section in the config blob) and the PATH under it. The
    config blob itself — which carries the obscured credentials — is NOT here; it
    is a secret resolved through the secret seam at run time and written to a temp
    ``rclone.conf``.
    """

    # The rclone remote name — must match the ``[section]`` header inside the
    # config blob (e.g. "gdrive", "b2-offsite"). Combined with ``path`` into
    # rclone's ``remote:path`` syntax.
    remote: str
    # Path under the remote where bundles live (a "folder"). Normalised to no
    # leading/trailing slash so ``remote:path`` joining is unambiguous.
    path: str = ""
    # Logical name for logs/manifest. Defaults to "rclone"; operator can name it
    # (e.g. "gdrive-offsite") when several rclone destinations coexist.
    name: str = "rclone"
    # The secret-seam field name the config blob is resolved from. Operator can
    # repoint it per destination.
    config_field: str = RCLONE_CONFIG_FIELD

    def __post_init__(self) -> None:
        if not self.remote.strip():
            raise DestinationError("rclone destination requires a remote name")

    @classmethod
    def from_settings(cls, settings: Any) -> RcloneDestinationConfig:
        """Build the rclone destination config from the workers :class:`Settings`.

        Reads only the NON-secret tunables (remote name, path); the config blob
        (obscured creds) stays in the secret seam.
        """
        return cls(
            remote=str(settings.backup_rclone_remote),
            path=str(settings.backup_rclone_path),
        )

    def normalized_path(self) -> str:
        """Path with no leading/trailing slash (or '' for the remote root)."""
        return self.path.strip().strip("/")

    def remote_root(self) -> str:
        """rclone ``remote:path`` for the bundle directory (the upload target)."""
        p = self.normalized_path()
        return f"{self.remote}:{p}" if p else f"{self.remote}:"

    def remote_file(self, name: str) -> str:
        """rclone ``remote:path/name`` for a single bundle file."""
        p = self.normalized_path()
        joined = f"{p}/{name}" if p else name
        return f"{self.remote}:{joined}"

    def uri_for(self, target: str) -> str:
        """A human ``rclone://remote:path`` URI for logs/results (no creds)."""
        return f"rclone://{target}"


@dataclass
class RcloneDestination:
    """Upload a backup bundle to ANY rclone backend by shelling out to rclone.

    Every operation runs ``rclone <verb> ... --config <temp rclone.conf>`` through
    the injectable :class:`workers.backup.CommandRunner` seam:

      * upload            -> ``rclone copy <bundle> <remote>:<path>``
      * list_remote       -> ``rclone lsjson <remote>:<path>``
      * download          -> ``rclone copy <remote>:<path>/<name> <dest>``
      * test_connectivity -> ``rclone lsd <remote>:<path>``

    The credentials live in the config blob, resolved through the
    :class:`workers.secrets.SecretsProvider` seam and written to a private temp
    ``rclone.conf`` (chmod 0600) for the duration of the op — never on the command
    line, never logged, always cleaned up in a finally. A non-zero rclone exit
    maps to a typed :class:`DestinationError`.
    """

    config: RcloneDestinationConfig
    secrets: SecretsProvider
    # Defaults to None → the real SubprocessRunner is wired lazily.
    runner: CommandRunner | None = None
    # Wall-clock cap for one rclone invocation. Generous: a multi-GB copy must
    # not be killed, but a hung transfer is a problem.
    timeout_s: int = 3600

    @property
    def name(self) -> str:
        return self.config.name

    # -- credential + config wiring -----------------------------------------

    def _resolve_config_blob(self) -> str:
        """Resolve the rclone config blob (obscured creds) through the secret seam.

        The raw blob lives only in this local scope + the temp file; it is NEVER
        logged or put in a result/manifest. We log only the remote name + the
        field NAME, mirroring the S3/SFTP adapters.
        """
        cfg = self.config
        try:
            fetched = self.secrets.fetch([cfg.config_field])
        except KeyError as exc:
            raise DestinationError(
                f"secret provider is missing the rclone config blob ({cfg.config_field!r})"
            ) from exc
        blob = fetched.get(cfg.config_field)
        if not blob:
            raise DestinationError(
                f"secret provider returned no rclone config blob ({cfg.config_field!r})"
            )
        _log.debug(
            "backup.dest.rclone.config_resolved",
            destination=cfg.name,
            remote=cfg.remote,
            config_field=cfg.config_field,
        )
        return blob

    @contextlib.contextmanager
    def _temp_config(self) -> Iterator[Path]:
        """Write the resolved config blob to a private temp ``rclone.conf`` (0600).

        Yields the path for the duration of one rclone op; the file (and its temp
        dir) are removed in the finally so the obscured creds never persist on
        disk. The file is created 0600 (owner-only) BEFORE the blob is written so
        the secret is never world-readable, even briefly.
        """
        tmp_dir = Path(tempfile.mkdtemp(prefix="rclone-conf-"))
        conf_path = tmp_dir / _RCLONE_TEMP_CONF_NAME
        try:
            # Open with O_CREAT|O_EXCL at 0600 so the secret is owner-only from
            # creation — never a window where it is world-readable.
            fd = os.open(
                conf_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(self._resolve_config_blob())
            yield conf_path
        finally:
            with contextlib.suppress(OSError):
                conf_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _rclone(self, conf_path: Path, *args: str) -> CommandResult:
        """Run one rclone invocation with the temp config + global flags.

        ``--config <path>`` points rclone at the temp file (creds in the FILE,
        never argv); ``--quiet`` keeps stdout to the structured output we parse.
        The credential blob is in the FILE, so the argv we build + log is safe.
        """
        runner = self.runner or SubprocessRunner()
        argv = ["rclone", f"--config={conf_path}", *args]
        return runner.run(argv, timeout=self.timeout_s)

    # -- BackupDestination ---------------------------------------------------

    def upload(self, bundle_path: Path) -> UploadResult:
        """Upload ``bundle_path`` (a file) to ``remote:path`` via ``rclone copy``.

        ``rclone copy <src-file> <remote:dir>`` copies the file into the remote
        directory keeping its name (rclone's documented file→dir semantics). Maps
        a non-zero rclone exit to :class:`DestinationError`.
        """
        bundle_path = Path(bundle_path)
        if not bundle_path.is_file():
            # Mirrors the S3/SFTP adapters: the remote-upload path expects a
            # single artifact file (the caller tars a plaintext bundle first).
            raise DestinationError(
                f"rclone upload expects a single bundle file, not {bundle_path!s}"
            )
        target = self.config.remote_root()
        with self._temp_config() as conf_path:
            result = self._rclone(conf_path, "copy", str(bundle_path), target)
        if result.returncode != 0:
            raise DestinationError(
                f"rclone copy to {self.config.uri_for(target)} failed "
                f"(rc={result.returncode}): {_safe_command_error(result)}"
            )
        size = bundle_path.stat().st_size
        landed = self.config.remote_file(bundle_path.name)
        uri = self.config.uri_for(landed)
        _log.info(
            "backup.dest.rclone.uploaded",
            destination=self.config.name,
            remote=self.config.remote,
            uri=uri,
            size_bytes=size,
        )
        return UploadResult(destination=self.config.name, remote_uri=uri, size_bytes=size)

    def list_remote(self) -> tuple[RemoteEntry, ...]:
        """List the bundle files already present under ``remote:path``.

        Uses ``rclone lsjson`` — a stable, parseable JSON array of objects with
        ``Name``/``Size``/``IsDir``. Directory entries are skipped; only regular
        files are bundles.
        """
        target = self.config.remote_root()
        with self._temp_config() as conf_path:
            result = self._rclone(conf_path, "lsjson", target)
        if result.returncode != 0:
            raise DestinationError(
                f"rclone lsjson of {self.config.uri_for(target)} failed "
                f"(rc={result.returncode}): {_safe_command_error(result)}"
            )
        try:
            items = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise DestinationError(
                f"rclone lsjson of {self.config.uri_for(target)} returned unparseable output"
            ) from exc
        entries: list[RemoteEntry] = []
        for item in items:
            if item.get("IsDir"):
                continue
            entries.append(
                RemoteEntry(
                    name=str(item.get("Name", "")), size_bytes=int(item.get("Size", 0) or 0)
                )
            )
        return tuple(entries)

    def download(self, name: str, dest: Path) -> Path:
        """Fetch a single remote bundle by ``name`` to local ``dest`` via ``rclone copy``.

        ``name`` is the bare file name (as returned by :meth:`list_remote`); the
        remote path is re-applied. ``rclone copy <remote:file> <local-dir>`` copies
        the file into ``dest`` keeping its name. ``dest`` may be a directory (the
        file lands under it as ``name``) or a target file path (its parent dir is
        used as rclone's destination directory).
        """
        dest = Path(dest)
        if dest.is_dir():
            dest_dir = dest
            target = dest / name
        else:
            dest_dir = dest.parent
            target = dest
        dest_dir.mkdir(parents=True, exist_ok=True)
        source = self.config.remote_file(name)
        with self._temp_config() as conf_path:
            result = self._rclone(conf_path, "copy", source, str(dest_dir))
        if result.returncode != 0:
            raise DestinationError(
                f"rclone copy of {self.config.uri_for(source)} failed "
                f"(rc={result.returncode}): {_safe_command_error(result)}"
            )
        _log.info(
            "backup.dest.rclone.downloaded",
            destination=self.config.name,
            remote=self.config.remote,
            uri=self.config.uri_for(source),
            dest=str(target),
        )
        return target

    def test_connectivity(self) -> ConnectivityResult:
        """Cheap reachability + auth probe: ``rclone lsd`` on ``remote:path``.

        ``lsd`` (list directories) proves the config authenticates AND the remote
        is reachable without transferring a bundle. Returns a typed result (never
        raises) so the admin UI can render OK/FAIL; the detail is non-leaky.
        """
        target = self.config.remote_root()
        try:
            with self._temp_config() as conf_path:
                result = self._rclone(conf_path, "lsd", target)
        except DestinationError as exc:
            # Config-blob resolution failed before we even reached rclone.
            return ConnectivityResult(ok=False, detail=str(exc))
        if result.returncode != 0:
            return ConnectivityResult(
                ok=False,
                detail=(
                    f"rclone lsd on {self.config.remote!r} failed "
                    f"(rc={result.returncode}): {_safe_command_error(result)}"
                ),
            )
        return ConnectivityResult(ok=True, detail=f"remote {self.config.remote!r} reachable")


def _safe_command_error(result: CommandResult) -> str:
    """A short, non-leaky description of a failed rclone invocation.

    rclone writes its error to stderr (a transport/auth/path message), never the
    config-file contents (those are read from the file we never echo), so the
    trimmed stderr is safe to surface to the admin UI + logs. Falls back to stdout
    then a generic message.
    """
    msg = (result.stderr or "").strip() or (result.stdout or "").strip()
    return msg or "no output"


def _safe_error(exc: Exception) -> str:
    """A short, non-leaky description of a backend error.

    Uses the exception's class name + str(). boto3/botocore errors carry the
    operation + HTTP status, never the credential, so this is safe to surface to
    the admin UI and logs. paramiko's SSHException / SFTPError carry only the
    transport-level message, never the password/key.
    """
    return f"{type(exc).__name__}: {exc}"


__all__ = [
    "B2_APPLICATION_KEY_FIELD",
    "B2_KEY_ID_FIELD",
    "B2_MULTIPART_CHUNKSIZE_BYTES",
    "B2_MULTIPART_THRESHOLD_BYTES",
    "RCLONE_CONFIG_FIELD",
    "S3_ACCESS_KEY_FIELD",
    "S3_SECRET_KEY_FIELD",
    "SFTP_PASSWORD_FIELD",
    "SFTP_PRIVATE_KEY_FIELD",
    "SFTP_PRIVATE_KEY_PASSPHRASE_FIELD",
    "B2Destination",
    "B2DestinationConfig",
    "BackupDestination",
    "ConnectivityResult",
    "DestinationError",
    "RcloneDestination",
    "RcloneDestinationConfig",
    "RemoteEntry",
    "S3Destination",
    "S3DestinationConfig",
    "SftpDestination",
    "SftpDestinationConfig",
    "UploadResult",
    "b2_endpoint_url",
]
