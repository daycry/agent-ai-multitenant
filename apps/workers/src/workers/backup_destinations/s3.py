"""Destino S3 (task_12_05) — sirve para CUALQUIER proveedor S3-compatible.

El adaptador esconde su cliente boto3 detrás de un ``client_factory`` inyectable:
en producción construye un ``boto3.client("s3")`` real; los tests inyectan una
factoría que devuelve un mock y afirman sobre las llamadas emitidas (bucket/clave
correctos, ``upload_file`` para multiparte, ``list_objects_v2`` sobre el prefijo,
``head_bucket`` para la sonda) y sobre que un error de S3 se mapea a
:class:`DestinationError`.

Las credenciales se resuelven por el seam de secretos (Vault/env) con los nombres
de campo de abajo: NUNCA de config en claro, NUNCA al manifiesto, NUNCA al log —
se registran los NOMBRES de los campos, jamás sus valores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from workers.backup_destinations.base import (
    REMOTE_CONNECT_TIMEOUT_S,
    ConnectivityResult,
    DestinationError,
    RemoteEntry,
    UploadResult,
    _log,
    _safe_error,
)
from workers.secrets import SecretsProvider

if TYPE_CHECKING:  # pragma: no cover — typing only, never imported at runtime
    from mypy_boto3_s3.client import S3Client

# Intentos TOTALES de botocore por operación. Su default (modo `legacy`, 5) hay
# que multiplicarlo por el plazo de arriba para saber lo que ocupa una sonda
# contra un host muerto: 50 s por sonda convierten el plazo en decorativo.
_S3_MAX_ATTEMPTS = 2


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
    in production / when no test factory is injected.

    Le inyecta un ``botocore.config.Config`` con plazo de conexión corto y
    reintentos acotados (task_prod13_02): sin él, boto3 va con
    ``connect_timeout=60`` y hasta 5 intentos, o sea ~5 minutos de hilo ocupado
    por cada sonda contra un endpoint muerto. Un ``config`` explícito del
    llamante GANA — el nuestro es un default, no una imposición.
    """
    import boto3
    from botocore.config import Config as BotocoreConfig

    if "config" not in kwargs:
        kwargs["config"] = BotocoreConfig(
            connect_timeout=REMOTE_CONNECT_TIMEOUT_S,
            retries={"max_attempts": _S3_MAX_ATTEMPTS, "mode": "standard"},
        )
    return boto3.client(**kwargs)
