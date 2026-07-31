"""El binario `tar` REAL contra los argv que el motor construye (prod-04 task_prod_04_01).

Por qué existe este fichero
---------------------------
Hasta el 2026-07 la ÚNICA cobertura del motor de backup era `FakeRunner`
(`tests/integration/test_backup_full.py`): un doble que **fabrica** el artefacto
en vez de ejecutar el comando. Eso cubre la construcción del argv, no su
ejecución — y el hueco no fue teórico: los argv de tar de Plan 12 omitían el flag
de modo (`--create`), así que cualquier `tar` real devolvía rc=2
(«You must specify one of the '-Acdtrux' options») y el motor borraba el bundle
entero. El primer backup real (2026-07-03) reventó ahí, un mes después de que la
suite estuviera verde.

Este módulo cierra ese hueco: ejecuta el argv que produce el CÓDIGO DE PRODUCCIÓN
(`BackupEngine._tar_volume`, `_tar_bind_path`, `_encrypt_bundle`) con
`SubprocessRunner` — el mismo runner que corre en el worker — contra directorios
temporales. No necesita Docker, ni stack vivo, ni PostgreSQL: solo `tar` y
`gzip`. Y comprueba lo único que importa de verdad de un backup: que el archivo
producido **se puede volver a extraer con el contenido original**.

Complemento, no sustituto: `test_backup_real_runner.py` hace lo mismo con
`run_full_backup` de punta a punta (manifest + verificación + cifrado).
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workers.backup import (
    BackupConfig,
    BackupEngine,
    SubprocessRunner,
    _checksum_file,
)
from workers.backup_encryption import BackupEncryptor
from workers.secrets import StaticSecretsProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("tar") is None,
        reason="este smoke test ejecuta el binario tar REAL; sin tar no prueba nada",
    ),
]

_NOW = datetime(2026, 7, 29, 3, 0, 0, tzinfo=UTC)


def _config(tmp_path: Path, **overrides: object) -> BackupConfig:
    defaults: dict[str, object] = {
        "backup_root": tmp_path / "backups",
        "database_url": "postgresql://u:p@localhost:5432/db",
        "volumes": (),
        "volumes_mount_root": tmp_path / "volumes",
        "retention_days": 7,
    }
    defaults.update(overrides)
    return BackupConfig(**defaults)  # type: ignore[arg-type]


def _seed_tree(root: Path) -> dict[str, bytes]:
    """Un árbol pequeño pero no trivial: subdirectorios y bytes no-ASCII."""
    files = {
        "top.txt": b"contenido raiz\n",
        "sub/nested.bin": bytes(range(256)),
        "sub/deeper/utf8.txt": "acentos: áéíóú ñ\n".encode(),
    }
    for rel, payload in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return files


def _extract(archive: Path, into: Path, *, gzip: bool = True) -> None:
    into.mkdir(parents=True, exist_ok=True)
    args = ["tar", "--extract"]
    if gzip:
        args.append("--gzip")
    args += [f"--directory={into}", f"--file={archive}"]
    done = subprocess.run(args, capture_output=True, text=True, check=False)
    assert done.returncode == 0, f"tar --extract falló: {done.stderr or done.stdout}"


def _relative_files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# --------------------------------------------------------------------------- #
# _tar_volume — el argv que reventó en producción
# --------------------------------------------------------------------------- #


def test_tar_volume_produces_a_real_archive_that_extracts_to_the_original_tree(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path, volumes=("minio_data",))
    source = cfg.volumes_mount_root / "minio_data" / "_data"
    source.mkdir(parents=True)
    expected = _seed_tree(source)

    bundle_dir = cfg.backup_root / "bundle"
    bundle_dir.mkdir(parents=True)

    engine = BackupEngine(cfg, runner=SubprocessRunner(), now=_NOW)
    record = engine._tar_volume(bundle_dir, "minio_data")

    archive = bundle_dir / record.path
    assert archive.is_file()
    # Un .tar.gz vacío pesa ~30 bytes; el árbol sembrado pasa de 400.
    assert archive.stat().st_size > 200, "el tar salió sospechosamente vacío"
    assert record.size_bytes == archive.stat().st_size
    assert record.sha256 == _checksum_file(archive)
    assert record.kind == "volume_tar"
    assert record.source == "minio_data"

    restored = tmp_path / "restored"
    _extract(archive, restored)
    assert _relative_files(restored) == expected


def test_tar_volume_argv_still_carries_the_mode_flag(tmp_path: Path) -> None:
    """La guarda explícita del bug: sin `--create` GNU tar no sabe qué hacer.

    No es redundante con el test de arriba: aquí queda por escrito CUÁL es el
    flag cuya ausencia costó el backup del 2026-07-03, para que un revert cuente
    una historia legible en vez de un rc=2 opaco.
    """
    cfg = _config(tmp_path, volumes=("redis_data",))
    source = cfg.volumes_mount_root / "redis_data" / "_data"
    source.mkdir(parents=True)
    (source / "dump.rdb").write_bytes(b"REDIS0011fake")
    bundle_dir = cfg.backup_root / "bundle"
    bundle_dir.mkdir(parents=True)

    recorded: list[list[str]] = []

    class Recording(SubprocessRunner):
        def run(self, args, *, env=None, timeout=None):  # type: ignore[no-untyped-def]
            recorded.append(list(args))
            return super().run(args, env=env, timeout=timeout)

    BackupEngine(cfg, runner=Recording(), now=_NOW)._tar_volume(bundle_dir, "redis_data")

    assert len(recorded) == 1
    argv = recorded[0]
    assert argv[0] == "tar"
    assert "--create" in argv, f"el argv de tar perdió el flag de modo: {argv}"

    # Y la prueba de que el flag es NECESARIO: el mismo argv sin él falla de
    # verdad. Si algún día GNU tar dejase de exigirlo, este assert avisaría de
    # que la guarda de arriba pasó a ser decorativa.
    without_mode = [a for a in argv if a != "--create"]
    done = subprocess.run(without_mode, capture_output=True, text=True, check=False)
    assert (
        done.returncode != 0
    ), "tar aceptó un argv sin flag de modo: la guarda `--create` ya no prueba nada"


# --------------------------------------------------------------------------- #
# _tar_bind_path — los bare repos de los agentes, y la auto-inclusión
# --------------------------------------------------------------------------- #


def test_tar_bind_path_extracts_back_and_excludes_the_backup_root(tmp_path: Path) -> None:
    """El bind por defecto CONTIENE el backup_root: sin el exclude el bundle se
    auto-incluiría (crecimiento cuadrático y/o rc≠0 «file changed as we read it»).
    Con tar real esto es comprobable; con el doble no lo era."""
    bind = tmp_path / "agent-platform"
    backup_root = bind / "backups"
    backup_root.mkdir(parents=True)
    # Un bundle previo, gordo, que NO debe entrar en el tar.
    (backup_root / "20260101T030000Z").mkdir()
    (backup_root / "20260101T030000Z" / "old.tar.gz").write_bytes(b"x" * 4096)

    projects = bind / "projects"
    projects.mkdir()
    expected = _seed_tree(projects)

    cfg = _config(tmp_path, backup_root=backup_root, bind_paths=(str(bind),))
    bundle_dir = backup_root / "bundle"
    bundle_dir.mkdir()

    record = BackupEngine(cfg, runner=SubprocessRunner(), now=_NOW)._tar_bind_path(
        bundle_dir, str(bind)
    )
    archive = bundle_dir / record.path
    assert archive.is_file() and archive.stat().st_size > 200

    restored = tmp_path / "restored-bind"
    _extract(archive, restored)
    files = _relative_files(restored)
    assert {
        k.removeprefix("projects/"): v for k, v in files.items() if k.startswith("projects/")
    } == (expected)
    assert not [
        k for k in files if k.startswith("backups/")
    ], f"el tar del bind se auto-incluyó el backup_root: {sorted(files)}"


# --------------------------------------------------------------------------- #
# _encrypt_bundle — tar real + AES-256-GCM real, ida y vuelta
# --------------------------------------------------------------------------- #


def test_encrypt_bundle_collapses_real_artifacts_and_the_blob_decrypts(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        volumes=("minio_data",),
        encryption_enabled=True,
        encryption_vault_key="backup_encryption_key",
    )
    source = cfg.volumes_mount_root / "minio_data" / "_data"
    source.mkdir(parents=True)
    expected = _seed_tree(source)

    bundle_dir = cfg.backup_root / "bundle"
    bundle_dir.mkdir(parents=True)

    encryptor = BackupEncryptor(
        provider=StaticSecretsProvider({"backup_encryption_key": "una-clave-de-prueba"}),
        vault_key_name="backup_encryption_key",
    )
    engine = BackupEngine(cfg, runner=SubprocessRunner(), encryptor=encryptor, now=_NOW)

    volume_record = engine._tar_volume(bundle_dir, "minio_data")
    encrypted = engine._encrypt_bundle(bundle_dir, [volume_record])

    assert [a.kind for a in encrypted] == ["encrypted_bundle"]
    blob = bundle_dir / encrypted[0].path
    assert blob.is_file()
    assert encrypted[0].sha256 == _checksum_file(blob)
    # El plaintext ya no está en reposo.
    assert not (bundle_dir / volume_record.path).exists()
    assert not (bundle_dir / "bundle.tar").exists()

    # Ida y vuelta completa: descifrar → des-tar el bundle → des-tar el volumen.
    work = tmp_path / "decrypted"
    work.mkdir()
    plain_tar = work / "bundle.tar"
    encryptor.decrypt_file(blob, plain_tar)
    _extract(plain_tar, work, gzip=False)
    inner = work / volume_record.path
    assert inner.is_file(), f"el bundle descifrado no trae {volume_record.path}"
    restored = tmp_path / "restored-enc"
    _extract(inner, restored)
    assert _relative_files(restored) == expected
