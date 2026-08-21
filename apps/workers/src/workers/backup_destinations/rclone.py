"""Destino rclone genérico (task_12_08) — CUALQUIER backend de rclone.

rclone habla ~70 backends de almacenamiento (Google Drive, Dropbox, OneDrive,
Azure Blob, SFTP, S3, WebDAV…) por una sola CLI. Envolverlo como
:class:`BackupDestination` deja el catálogo de destinos abierto sin un adaptador
a medida por proveedor.

Es un SUBPROCESO, no un cliente Python, así que sale por el mismo
:class:`workers.backup.CommandRunner` inyectable que usa el motor de backup
(producción = ``SubprocessRunner`` con argv explícito, jamás ``shell=True``).

El blob de configuración (que lleva las credenciales ofuscadas) es un SECRETO: se
resuelve por el seam, se escribe en un ``rclone.conf`` temporal a 0600, se le pasa
a rclone con ``--config <ruta>`` —para que las credenciales estén en el FICHERO y
nunca en la línea de comandos, la tabla de procesos o los logs— y el temporal se
borra en un ``finally``.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workers.backup import CommandResult, CommandRunner, SubprocessRunner
from workers.backup_destinations.base import (
    REMOTE_CONNECT_TIMEOUT_S,
    ConnectivityResult,
    DestinationError,
    RemoteEntry,
    UploadResult,
    _log,
    _safe_command_error,
)
from workers.secrets import SecretsProvider

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

        ``--contimeout`` (task_prod13_02) va en TODAS las invocaciones, no solo
        en la sonda, porque acota únicamente la fase de CONEXIÓN: una copia de
        varios GB no se ve afectada, y una copia contra un host muerto deja de
        esperar el minuto que rclone trae por defecto. Las globales tienen que
        ir delante del subcomando.
        """
        runner = self.runner or SubprocessRunner()
        argv = [
            "rclone",
            f"--config={conf_path}",
            f"--contimeout={REMOTE_CONNECT_TIMEOUT_S}s",
            *args,
        ]
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
