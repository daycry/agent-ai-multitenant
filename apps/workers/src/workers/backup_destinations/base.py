"""El contrato común de un destino remoto de backup: tipos, errores y plazos.

Lo que TODOS los adaptadores comparten y ninguno posee: el Protocol
:class:`BackupDestination`, los tres dataclasses de resultado, el error tipado al
que cada backend funela el suyo, el plazo de conexión y los dos formateadores de
error no-filtrantes.

**No hay ningún adaptador aquí.** Si un import de este módulo apunta a boto3, a
paramiko o a rclone, está en el fichero equivocado.

El logger vive aquí y se llama ``workers.backup_destinations`` —el nombre del
paquete, no el del submódulo— a propósito: es lo que sale en Loki y lo que
consultan los runbooks de DR. Lo fija ``tests/unit/test_backup_destinations_package.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import structlog

from workers.backup import CommandResult

_log = structlog.get_logger("workers.backup_destinations")

# Plazo de CONEXIÓN de los tres backends remotos (prod-13 task_prod13_02,
# hallazgo api-3). Es el único plazo que se puede poner AQUÍ y el único que
# arregla el problema de verdad.
#
# Por qué hace falta aunque el api-server ya envuelva estas llamadas en
# `to_thread` con su propio plazo (`routers/backup.py::REMOTE_PROBE_TIMEOUT_S`):
# aquel acota la RESPUESTA, no el HILO. Python no puede matar un hilo, así que
# una sonda contra una IP que DROPea paquetes —firewall que descarta en vez de
# mandar RST— seguía ocupando un hilo del executor por defecto (`min(32, cpu+4)`)
# hasta que el SO se rindiera, que son minutos. Con bastantes destinos
# inalcanzables el executor se agota y `to_thread` deja de ser una salida: se
# hace cola, y el bloqueo que se quería evitar vuelve por la puerta de atrás.
#
# Diez segundos porque un TCP connect que no abre en diez no va a abrir. Lo que
# NO se acorta es el plazo de LECTURA: una subida multiparte de varios GB por un
# enlace lento hace lecturas legítimamente largas y un `read_timeout` corto la
# mataría a mitad. El connect no tiene ese problema.
REMOTE_CONNECT_TIMEOUT_S = 10


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
