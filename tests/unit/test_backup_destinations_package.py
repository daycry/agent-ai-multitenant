"""El troceo de `workers/backup_destinations.py` en paquete no puede perder nada.

Plan prod-16, ``task_prod16_12``: «`workers/backup_destinations.py` (1392):
extraer sub-módulos cohesivos (un módulo por tipo de destino)». Refactor puro: el
módulo era 1449 líneas con cuatro adaptadores completos + la fábrica.

## Qué es lo que de verdad se puede romper aquí, y no es un import

Este módulo NO se consume por su API pública nada más. Se consume **por su nombre
de módulo**, en dos formas que un troceo mal hecho rompe en silencio:

1. **`monkeypatch` sobre el atributo del módulo.** `routers/backup.py` y
   `workers/backup_task.py` hacen `from workers.backup_destinations import
   build_destination` DENTRO de la función, así que leen el atributo del módulo en
   cada llamada — y los tests (`test_dest_ui.py`, `test_backup_remote_upload.py`)
   se apoyan justo en eso para no tocar boto3 ni la red. Si un consumidor pasara a
   importar de `workers.backup_destinations.factory`, el parche sobre el paquete
   dejaría de alcanzarle: los tests seguirían VERDES **haciendo red de verdad**, o
   fallando por una razón que no tiene nada que ver. Por eso hay abajo una guarda
   por AST de que nadie importa de un submódulo.

2. **El nombre del logger.** `structlog.get_logger("workers.backup_destinations")`
   es lo que sale en Loki. Un logger por submódulo cambiaría las consultas del
   runbook de DR sin que ningún test lo notara.

Y una tercera, específica de este fichero: **`B2Destination` HEREDA de
`S3Destination`** (B2 habla S3; solo cambian endpoint, tamaño de parte y nombres
de secreto). Repartirlos entre `s3.py` y `b2.py` es exactamente el movimiento que
tienta a "desduplicar" — y romper la herencia cambiaría el comportamiento de
subida sin tocar una firma.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


#: Capturado del `backup_destinations.py` monolítico (1449 líneas) el 2026-08-12,
#: justo antes de partirlo. Es el `__all__` literal del módulo.
PUBLIC_API_BEFORE_THE_SPLIT: tuple[str, ...] = (
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
)

#: Nombres PRIVADOS que consumen desde fuera del módulo. No son API pública, pero
#: retirarlos del paquete pone rojo un test de otro carril, así que el troceo los
#: reexporta. Cada uno con quién lo usa:
#:   `_default_boto3_factory`      -> tests/unit/test_backup_destination_connect_timeouts
#:   `_default_paramiko_transport` -> idem
#:   `REMOTE_CONNECT_TIMEOUT_S`    -> idem (es público de facto, sin `__all__`)
#:   `_safe_error` / `_safe_command_error` / `_require` / `_sftp_attr_is_dir`
#:                                 -> los usan los propios adaptadores entre sí
PRIVATE_NAMES_STILL_REACHABLE: tuple[str, ...] = (
    "REMOTE_CONNECT_TIMEOUT_S",
    "_HOST_KEY_POLICIES",
    "_RCLONE_TEMP_CONF_NAME",
    "_S3_MAX_ATTEMPTS",
    "_SFTP_DEFAULT_PORT",
    "_default_boto3_factory",
    "_default_paramiko_transport",
    "_log",
    "_require",
    "_safe_command_error",
    "_safe_error",
    "_sftp_attr_is_dir",
)


def test_the_public_api_is_exactly_the_one_the_monolith_exported() -> None:
    import workers.backup_destinations as bd

    assert tuple(bd.__all__) == PUBLIC_API_BEFORE_THE_SPLIT
    missing = [name for name in PUBLIC_API_BEFORE_THE_SPLIT if not hasattr(bd, name)]
    assert not missing, f"`__all__` promete nombres que el paquete no expone: {missing}"


def test_the_private_names_other_suites_reach_for_are_still_on_the_package() -> None:
    import workers.backup_destinations as bd

    missing = [name for name in PRIVATE_NAMES_STILL_REACHABLE if not hasattr(bd, name)]
    assert not missing, (
        f"el troceo escondió nombres que se consumen desde fuera: {missing}. "
        "No son API pública, pero moverlos sin reexportarlos rompe suites de "
        "otros carriles, no este fichero."
    )


def test_build_destination_still_maps_every_type_to_its_adapter() -> None:
    from workers.backup_destinations import (
        B2Destination,
        DestinationError,
        RcloneDestination,
        S3Destination,
        SftpDestination,
        build_destination,
    )
    from workers.secrets import StaticSecretsProvider

    secrets = StaticSecretsProvider(values={})

    s3 = build_destination({"type": "s3", "name": "s", "bucket": "b"}, secrets=secrets)
    assert isinstance(s3, S3Destination) and not isinstance(s3, B2Destination)

    b2 = build_destination(
        {"type": "b2", "name": "x", "bucket": "b", "region": "us-west-002"}, secrets=secrets
    )
    assert isinstance(b2, B2Destination)
    assert b2.config.endpoint_url == "https://s3.us-west-002.backblazeb2.com"

    sftp = build_destination(
        {"type": "sftp", "name": "n", "host": "h", "username": "u"}, secrets=secrets
    )
    assert isinstance(sftp, SftpDestination)

    rclone = build_destination({"type": "rclone", "name": "r", "remote": "rem"}, secrets=secrets)
    assert isinstance(rclone, RcloneDestination)

    with pytest.raises(DestinationError):
        build_destination({"type": "nope", "name": "n"}, secrets=secrets)
    with pytest.raises(DestinationError):
        # Falta `bucket`: `_require` tiene que seguir siendo un DestinationError
        # tipado y no un KeyError pelado.
        build_destination({"type": "s3", "name": "s"}, secrets=secrets)


def test_b2_still_inherits_from_s3_instead_of_duplicating_it() -> None:
    """B2 HABLA S3: reutiliza upload/list/download/connectivity enteros.

    Partirlos en dos módulos es justo el momento en que alguien "desduplica" la
    herencia sin querer. Si esto se rompe, la subida a B2 deja de usar el camino
    probado del S3 y nadie se entera hasta un DR.
    """
    from workers.backup_destinations import (
        B2Destination,
        B2DestinationConfig,
        S3Destination,
        S3DestinationConfig,
    )

    assert issubclass(B2Destination, S3Destination)
    assert issubclass(B2DestinationConfig, S3DestinationConfig)


def test_every_adapter_still_satisfies_the_protocol() -> None:
    from workers.backup_destinations import BackupDestination, build_destination
    from workers.secrets import StaticSecretsProvider

    secrets = StaticSecretsProvider(values={})
    configs: list[dict[str, Any]] = [
        {"type": "s3", "name": "s", "bucket": "b"},
        {"type": "b2", "name": "x", "bucket": "b", "region": "us-west-002"},
        {"type": "sftp", "name": "n", "host": "h", "username": "u"},
        {"type": "rclone", "name": "r", "remote": "rem"},
    ]
    for config in configs:
        dest = build_destination(config, secrets=secrets)
        assert isinstance(dest, BackupDestination), config["type"]


def test_the_logger_name_did_not_move_to_a_per_module_name() -> None:
    """Es lo que sale en Loki y lo que consultan los runbooks de DR."""
    import workers.backup_destinations as bd

    # structlog guarda el nombre del logger en su contexto de binding.
    name = getattr(bd._log, "_logger_factory_args", None) or getattr(bd._log, "name", None)
    assert name in (
        ("workers.backup_destinations",),
        "workers.backup_destinations",
    ), f"el logger dejó de llamarse `workers.backup_destinations`: {name!r}"


def _modules_importing_a_submodule() -> list[str]:
    """Ficheros que importan de `workers.backup_destinations.<algo>` (por AST)."""
    offenders: list[str] = []
    roots = [_REPO_ROOT / "apps", _REPO_ROOT / "tests", _REPO_ROOT / "packages"]
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "backup_destinations" in path.parts:
                continue  # el propio paquete se importa a sí mismo, claro
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover
                continue
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    module = next(
                        (
                            a.name
                            for a in node.names
                            if a.name.startswith("workers.backup_destinations.")
                        ),
                        "",
                    )
                if module.startswith("workers.backup_destinations."):
                    offenders.append(f"{path.relative_to(_REPO_ROOT).as_posix()} -> {module}")
    return offenders


def test_nobody_outside_the_package_imports_a_submodule_directly() -> None:
    """El punto de parcheo tiene que seguir siendo UNO: el paquete.

    `routers/backup.py` y `workers/backup_task.py` importan `build_destination`
    dentro de la función y los tests parchean el atributo del PAQUETE. Un import
    desde `…backup_destinations.factory` esquivaría ese parche, y el síntoma sería
    un test que pasa haciendo red de verdad — no un rojo.
    """
    offenders = _modules_importing_a_submodule()
    assert not offenders, (
        "alguien importa un submódulo de `workers.backup_destinations` en vez del "
        f"paquete, esquivando el punto de parcheo de los tests: {offenders}"
    )


def test_the_split_actually_split_it() -> None:
    """Un `__init__.py` que reexportase el monolito pasaría todo lo de arriba."""
    from workers.backup_destinations import b2, base, factory, rclone, s3, sftp

    assert base.DestinationError is not None
    assert s3.S3Destination.__module__ == "workers.backup_destinations.s3"
    assert b2.B2Destination.__module__ == "workers.backup_destinations.b2"
    assert sftp.SftpDestination.__module__ == "workers.backup_destinations.sftp"
    assert rclone.RcloneDestination.__module__ == "workers.backup_destinations.rclone"
    assert factory.build_destination.__module__ == "workers.backup_destinations.factory"


def test_every_piece_stays_under_the_size_that_motivated_the_split() -> None:
    """Repartir 1449 líneas en dos piezas de 700 no es trocear (métrica del plan)."""
    import workers.backup_destinations as bd

    package_dir = Path(bd.__file__).parent
    sizes = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(package_dir.glob("*.py"))
    }
    too_big = {name: n for name, n in sizes.items() if n > 500}
    assert not too_big, f"piezas del troceo por encima de 500 líneas: {too_big}"
