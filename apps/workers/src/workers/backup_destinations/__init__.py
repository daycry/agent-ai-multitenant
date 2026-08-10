"""Destinos remotos de backup (Plan 12 Fase B — task_12_05 en adelante).

La Fase A (:mod:`workers.backup`) escribe un *bundle con marca de tiempo* en disco
local y poda con una ventana de retención de 7 días. La Fase B añade la OTRA mitad
de la política de almacenamiento del plan —*«Retención local 7 días + destinos
remotos opcionales (S3, B2, SFTP/NAS, rclone genérico)»*—: tras un backup
correcto y verificado, el bundle (cifrado o no) se sube a cada destino remoto
configurado y habilitado.

Una interfaz común, un adaptador por backend
--------------------------------------------
Cada backend (S3, Backblaze B2, SFTP/NAS, rclone) habla el mismo Protocol
:class:`BackupDestination`::

    upload(bundle_path)              -> UploadResult
    list_remote()                    -> tuple[RemoteEntry, ...]
    download(name, dest)             -> Path
    test_connectivity()              -> ConnectivityResult

de modo que el flujo de backup (y el botón de «probar conectividad» del panel,
task_12_09) hablan con un destino sin saber de qué backend es. Cada adaptador se
registra por ``type`` (``"s3"``, ``"b2"``, ``"sftp"``, ``"rclone"``).

La realidad del mock-no-fake
-----------------------------
En tests no hay ningún endpoint S3/B2/SFTP/rclone real alcanzable, así que cada
adaptador esconde su cliente detrás de un seam inyectable y los tests meten un
mock: el adaptador emite las llamadas correctas y un error del backend se mapea a
un :class:`DestinationError` tipado.

Las credenciales son secretos
------------------------------
Las credenciales de destino (clave S3, contraseña/clave SFTP, blob de rclone) se
resuelven por el seam de secretos de los workers
(:class:`workers.secrets.SecretsProvider`). NUNCA se leen de config en claro,
NUNCA se escriben en el manifiesto y NUNCA se registran: se loguean los NOMBRES
de los campos, jamás sus valores.

## Por qué esto es un paquete (plan prod-16, `task_prod16_12`)

Era un solo `backup_destinations.py` de **1449 líneas** con cuatro adaptadores
completos y la fábrica. Repartido:

  * :mod:`.base`    — el Protocol, los tipos de resultado, el error, el plazo de
    conexión y los formateadores no-filtrantes. **Sin adaptadores.**
  * :mod:`.s3`      — el adaptador S3 (cualquier proveedor S3-compatible).
  * :mod:`.b2`      — Backblaze B2, que HEREDA del S3 (B2 habla S3).
  * :mod:`.sftp`    — SFTP/NAS por paramiko.
  * :mod:`.rclone`  — rclone genérico por subproceso.
  * :mod:`.factory` — `build_destination`, el registro por tipo.

**Este `__init__` no es decorativo: es el punto de parcheo.** `routers/backup.py`
y `workers/backup_task.py` hacen `from workers.backup_destinations import
build_destination` DENTRO de la función, y los tests parchean el atributo de ESTE
módulo para no tocar la red. Importar desde un submódulo (`…backup_destinations.factory`)
esquivaría ese parche, y el síntoma no sería un rojo sino un test que pasa
haciendo red de verdad. Hay una guarda por AST en
``tests/unit/test_backup_destinations_package.py`` que lo impide.
"""

from __future__ import annotations

from workers.backup_destinations import b2, base, factory, rclone, s3, sftp
from workers.backup_destinations.b2 import (
    B2_APPLICATION_KEY_FIELD,
    B2_KEY_ID_FIELD,
    B2_MULTIPART_CHUNKSIZE_BYTES,
    B2_MULTIPART_THRESHOLD_BYTES,
    B2Destination,
    B2DestinationConfig,
    b2_endpoint_url,
)
from workers.backup_destinations.base import (
    REMOTE_CONNECT_TIMEOUT_S,
    BackupDestination,
    ConnectivityResult,
    DestinationError,
    RemoteEntry,
    UploadResult,
    _log,
    _safe_command_error,
    _safe_error,
)
from workers.backup_destinations.factory import DESTINATION_TYPES, _require, build_destination
from workers.backup_destinations.rclone import (
    _RCLONE_TEMP_CONF_NAME,
    RCLONE_CONFIG_FIELD,
    RcloneDestination,
    RcloneDestinationConfig,
)
from workers.backup_destinations.s3 import (
    _S3_MAX_ATTEMPTS,
    S3_ACCESS_KEY_FIELD,
    S3_SECRET_KEY_FIELD,
    S3Destination,
    S3DestinationConfig,
    _default_boto3_factory,
)
from workers.backup_destinations.sftp import (
    _HOST_KEY_POLICIES,
    _SFTP_DEFAULT_PORT,
    SFTP_PASSWORD_FIELD,
    SFTP_PRIVATE_KEY_FIELD,
    SFTP_PRIVATE_KEY_PASSPHRASE_FIELD,
    SftpDestination,
    SftpDestinationConfig,
    _default_paramiko_transport,
    _sftp_attr_is_dir,
)

__all__ = [
    "B2_APPLICATION_KEY_FIELD",
    "B2_KEY_ID_FIELD",
    "B2_MULTIPART_CHUNKSIZE_BYTES",
    "B2_MULTIPART_THRESHOLD_BYTES",
    "DESTINATION_TYPES",
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
    "build_destination",
]

# Reexportados a propósito PERO fuera de `__all__`: no son API pública, y a la vez
# retirarlos rompería suites de otros carriles que los importan por este nombre
# (los plazos de conexión de prod-13) o los parchean. La lista, con quién usa cada
# uno, está en `tests/unit/test_backup_destinations_package.py`.
_REEXPORTED_PRIVATES = (
    REMOTE_CONNECT_TIMEOUT_S,
    _HOST_KEY_POLICIES,
    _RCLONE_TEMP_CONF_NAME,
    _S3_MAX_ATTEMPTS,
    _SFTP_DEFAULT_PORT,
    _default_boto3_factory,
    _default_paramiko_transport,
    _log,
    _require,
    _safe_command_error,
    _safe_error,
    _sftp_attr_is_dir,
    b2,
    base,
    factory,
    rclone,
    s3,
    sftp,
)
