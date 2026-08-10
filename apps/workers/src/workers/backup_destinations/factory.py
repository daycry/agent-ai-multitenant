"""El registro: de un dict de config NO-secreto a un adaptador vivo (task_12_09).

El panel de admin guarda una lista de configs de destino (cada una un dict
``{"type", "name", <ajustes NO secretos>}``) en `platform_settings`, y ofrece un
botón de «probar conectividad». Tanto el flujo de backup como ese botón necesitan
convertir uno de esos dicts en un :class:`BackupDestination` vivo: esta fábrica es
el único seam registrado-por-tipo que lo hace.

Las credenciales NUNCA están en el dict; se resuelven en tiempo de ejecución por
el :class:`SecretsProvider` inyectado, igual que ya hacen los adaptadores. Un dict
de config no lleva secretos, así que construir un destino a partir de él no puede
filtrar ninguno.
"""

from __future__ import annotations

from typing import Any

from workers.backup import CommandRunner
from workers.backup_destinations.b2 import (
    B2Destination,
    B2DestinationConfig,
    b2_endpoint_url,
)
from workers.backup_destinations.base import BackupDestination, DestinationError
from workers.backup_destinations.rclone import RcloneDestination, RcloneDestinationConfig
from workers.backup_destinations.s3 import S3Destination, S3DestinationConfig
from workers.backup_destinations.sftp import (
    _SFTP_DEFAULT_PORT,
    SftpDestination,
    SftpDestinationConfig,
)
from workers.secrets import SecretsProvider

# ---------------------------------------------------------------------------
# Registry — map a destination `type` + NON-secret config to an adapter (task_12_09).
# ---------------------------------------------------------------------------
#
# The admin UI (task_12_09) stores a list of destination configs (each a
# {"type", "name", <type-specific NON-secret knobs>} dict) in platform_settings,
# and offers a "test connectivity" button. Both the backup flow and that button
# need to turn one such config dict into a live :class:`BackupDestination`. This
# factory is the single registered-by-type seam that does so — credentials are
# NEVER in the config dict; they are resolved through the injected
# :class:`SecretsProvider` exactly as the adapters already do. A config dict NEVER
# carries a secret, so building a destination from it cannot leak one.

# The destination types the platform supports (Plan 12: "S3, B2, SFTP/NAS, rclone").
DESTINATION_TYPES = ("s3", "b2", "sftp", "rclone")


def _require(config: dict[str, Any], key: str, *, dest_type: str) -> str:
    """Pull a required NON-secret string field from a destination config dict.

    Raises a typed :class:`DestinationError` (never a bare KeyError) when the
    operator's config is missing a field the adapter needs — the message names
    the field + type, never any secret (the config dict has none).
    """
    value = config.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise DestinationError(f"{dest_type} destination config is missing required field {key!r}")
    return str(value)


def build_destination(
    config: dict[str, Any],
    *,
    secrets: SecretsProvider,
    runner: CommandRunner | None = None,
) -> BackupDestination:
    """Build a :class:`BackupDestination` from a NON-secret config dict.

    ``config`` is one entry of the operator's ``backup_destinations`` list: a
    ``{"type": "s3"|"b2"|"sftp"|"rclone", "name": ..., <type-specific knobs>}``
    dict that carries ONLY non-secret tunables (bucket, endpoint, host, path,
    remote). Credentials are NOT in it — they are resolved at run time through
    ``secrets`` (the workers' Vault/env secret seam), keyed by each adapter's
    well-known field names. ``runner`` is forwarded to the rclone adapter (the
    CommandRunner seam); the S3/B2/SFTP adapters do not use it.

    Registered by ``type`` (:data:`DESTINATION_TYPES`); an unknown type is a
    typed :class:`DestinationError`. Building a destination performs NO network /
    credential resolution — that happens lazily on the first operation — so this
    is cheap + side-effect-free, suitable for the admin "test connectivity"
    endpoint to call before probing.
    """
    dest_type = str(config.get("type", "")).strip().lower()
    name = str(config.get("name") or dest_type)

    if dest_type == "s3":
        return S3Destination(
            config=S3DestinationConfig(
                bucket=_require(config, "bucket", dest_type=dest_type),
                prefix=str(config.get("prefix", "")),
                endpoint_url=str(config.get("endpoint_url") or "") or None,
                region=str(config.get("region") or "") or None,
                name=name,
            ),
            secrets=secrets,
        )
    if dest_type == "b2":
        region = _require(config, "region", dest_type=dest_type)
        return B2Destination(
            config=B2DestinationConfig(
                bucket=_require(config, "bucket", dest_type=dest_type),
                prefix=str(config.get("prefix", "")),
                endpoint_url=b2_endpoint_url(region),
                region=region,
                name=name,
            ),
            secrets=secrets,
        )
    if dest_type == "sftp":
        return SftpDestination(
            config=SftpDestinationConfig(
                host=_require(config, "host", dest_type=dest_type),
                remote_path=str(config.get("remote_path", "")),
                username=_require(config, "username", dest_type=dest_type),
                port=int(config.get("port", _SFTP_DEFAULT_PORT)),
                host_key_policy=str(config.get("host_key_policy", "reject")),
                known_hosts_path=str(config.get("known_hosts_path", "")),
                name=name,
            ),
            secrets=secrets,
        )
    if dest_type == "rclone":
        return RcloneDestination(
            config=RcloneDestinationConfig(
                remote=_require(config, "remote", dest_type=dest_type),
                path=str(config.get("path", "")),
                name=name,
            ),
            secrets=secrets,
            runner=runner,
        )
    raise DestinationError(
        f"unknown backup destination type {dest_type!r}; " f"must be one of {DESTINATION_TYPES}"
    )
