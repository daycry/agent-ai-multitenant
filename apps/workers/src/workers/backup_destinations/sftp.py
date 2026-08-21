"""Destino SFTP / NAS (task_12_07) — paramiko sobre SSH.

Un NAS o cualquier host alcanzable por SSH es un destino remoto de primera clase
y habla el MISMO Protocol :class:`BackupDestination` que S3/B2/rclone, así que el
flujo de backup y el botón de «probar conectividad» lo tratan igual.

paramiko abre una sesión TCP+SSH real, inalcanzable en tests, así que el
transporte se construye perezosamente por un ``transport_factory`` inyectable.

Auth por contraseña O por clave privada, las dos resueltas por el seam de
secretos (nunca config en claro, nunca al log — solo los NOMBRES de campo). La
política de host key es configurable y nunca se desactiva la comprobación más
allá de lo que el operador elija explícitamente.
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
    import paramiko

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
    # Los TRES plazos, y hacen falta los tres (task_prod13_02): `timeout` acota
    # el TCP connect (sin él paramiko hereda el del SO, que contra un firewall
    # que DROPea son minutos), `banner_timeout` el host que abre el socket y no
    # se identifica, y `auth_timeout` el que acepta la conexión y no contesta al
    # auth. Los dos últimos ocurren con el connect YA resuelto, así que `timeout`
    # a solas dejaría el hilo colgado igual — solo que un poco más tarde.
    client.connect(
        hostname=host,
        port=port,
        username=username,
        password=password or None,
        pkey=pkey,
        look_for_keys=False,
        allow_agent=False,
        timeout=REMOTE_CONNECT_TIMEOUT_S,
        banner_timeout=REMOTE_CONNECT_TIMEOUT_S,
        auth_timeout=REMOTE_CONNECT_TIMEOUT_S,
    )
    sftp = client.open_sftp()
    return sftp
