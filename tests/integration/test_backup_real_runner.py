"""`run_full_backup` de punta a punta con el runner REAL (prod-04 task_prod_04_02).

Qué es real aquí y qué no
-------------------------
`tests/integration/test_backup_full.py` inyecta un `FakeRunner` que **fabrica**
los artefactos: prueba la construcción del argv, nunca su ejecución. Ese hueco
dejó pasar durante un mes un argv de tar sin flag de modo. Aquí el runner es
`SubprocessRunner` — el mismo del worker — así que corren de verdad:

* `tar --create --gzip` de cada volumen y de cada bind path,
* `tar --create` que colapsa el bundle antes de cifrar,
* `tar --list --gzip` de la verificación estructural,
* los SHA-256 del manifest (`hashlib` sobre los bytes reales del disco),
* el cifrado y descifrado AES-256-GCM del bundle.

El ÚNICO seam doblado son los dos binarios de PostgreSQL (`pg_dump` /
`pg_restore`), que no se pueden ejecutar sin un servidor y no están en el PATH de
la máquina de desarrollo. El plan pedía un stub ejecutable en el PATH; en Windows
eso no es posible (`CreateProcess` solo resuelve `.exe` para un argv sin
extensión, así que un `.bat`/script con shebang llamado `pg_dump` no se
encontraría), de modo que el stub es un runner que **delega en el subprocess real
todo lo que no sea pg_dump/pg_restore**. Para que ese doble no pueda degenerar en
un fake completo, cada test asserta cuántos comandos se ejecutaron DE VERDAD
(`real_calls`): si alguien reintrodujera un fake total, ese contador caería a 0.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workers.backup import (
    CommandResult,
    SubprocessRunner,
    run_full_backup,
)
from workers.backup_encryption import BackupEncryptor
from workers.backup_verification import (
    CHECK_CHECKSUM,
    CHECK_DECRYPT,
    CHECK_TAR_LIST,
    verify_bundle,
)
from workers.config import Settings
from workers.secrets import StaticSecretsProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("tar") is None,
        reason="el backup real necesita el binario tar; sin él el test no probaría nada",
    ),
]

_NOW = datetime(2026, 7, 29, 3, 0, 0, tzinfo=UTC)
_BACKUP_ID = "20260729T030000Z"
_VAULT_KEY = "backup_encryption_key"
_KEY_VALUE = "clave-de-prueba-no-secreta"

# TOC sintético que imita la salida de `pg_restore --list`: cabecera de
# comentarios `;` + al menos una entrada real (lo que exige _toc_has_entries).
_FAKE_TOC = (
    ";\n"
    "; Archive created at 2026-07-29 03:00:00 UTC\n"
    ";     dbname: agentic_platform\n"
    ";\n"
    "215; 1259 16404 TABLE public tenants migrations_user\n"
    "216; 1259 16410 TABLE public projects migrations_user\n"
)


class PgStubRunner(SubprocessRunner):
    """`SubprocessRunner` real salvo para `pg_dump` / `pg_restore`.

    `pg_dump --format=directory --file=<dir>` escribe un dump sintético (dos
    ficheros con bytes deterministas); `pg_restore --list` devuelve un TOC
    sintético. TODO lo demás (tar, gzip) va al binario de verdad.
    """

    def __init__(self) -> None:
        self.real_calls: list[list[str]] = []
        self.stubbed_calls: list[list[str]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        argv = list(args)
        if argv[0] == "pg_dump":
            self.stubbed_calls.append(argv)
            out = _arg_value(argv, "--file=")
            assert out is not None, f"pg_dump sin --file=: {argv}"
            target = Path(out)
            target.mkdir(parents=True, exist_ok=True)
            (target / "toc.dat").write_bytes(b"PGDMP-synthetic-toc")
            (target / "3434.dat.gz").write_bytes(bytes(range(256)) * 4)
            return CommandResult(returncode=0)
        if argv[0] == "pg_restore":
            self.stubbed_calls.append(argv)
            return CommandResult(returncode=0, stdout=_FAKE_TOC)
        self.real_calls.append(argv)
        return super().run(argv, env=env, timeout=timeout)


def _arg_value(argv: list[str], prefix: str) -> str | None:
    for arg in argv:
        if arg.startswith(prefix):
            return arg[len(prefix) :]
    return None


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    volumes_root = tmp_path / "volumes"
    for volume in ("minio_data", "redis_data", "vault_data"):
        data = volumes_root / volume / "_data"
        data.mkdir(parents=True)
        (data / f"{volume}.payload").write_bytes(f"contenido de {volume}\n".encode() * 32)
        (data / "sub").mkdir()
        (data / "sub" / "nested.bin").write_bytes(bytes(range(256)))

    bind = tmp_path / "agent-platform"
    (bind / "projects" / "t1" / "p1" / "repos").mkdir(parents=True)
    (bind / "projects" / "t1" / "p1" / "repos" / "HEAD").write_bytes(b"ref: refs/heads/main\n")

    base: dict[str, object] = {
        "backup_root": str(bind / "backups"),
        "backup_database_url": "postgresql://migrations_user:secreto@postgres:5432/agentic_platform",
        "backup_volumes": ["minio_data", "redis_data", "vault_data"],
        "backup_volumes_mount_root": str(volumes_root),
        "backup_bind_paths": [str(bind)],
        "backup_retention_days": 7,
        # ADR 0149: sin esto el motor le pediría a `docker compose` que parase el
        # stack de esta MÁQUINA de desarrollo mientras corre la suite. El sujeto
        # de este fichero es tar/gzip/AES de verdad, no el quiesce (que tiene su
        # propio fichero, `tests/unit/test_backup_quiesce.py`).
        "backup_quiesce_services": [],
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _extract(archive: Path, into: Path, *, gzip: bool = True) -> None:
    into.mkdir(parents=True, exist_ok=True)
    args = ["tar", "--extract"]
    if gzip:
        args.append("--gzip")
    args += [f"--directory={into}", f"--file={archive}"]
    done = subprocess.run(args, capture_output=True, text=True, check=False)
    assert done.returncode == 0, f"tar --extract falló: {done.stderr or done.stdout}"


# --------------------------------------------------------------------------- #
# Bundle en claro: tar real + checksums reales + verificación estructural real
# --------------------------------------------------------------------------- #


def test_plaintext_bundle_is_produced_verified_and_extractable(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    runner = PgStubRunner()

    result = run_full_backup(settings=settings, runner=runner, now=_NOW)

    assert result.backup_id == _BACKUP_ID
    # 3 volúmenes + 1 bind = 4 tar REALES ejecutados.
    assert len(runner.real_calls) == 4, f"tar no se ejecutó de verdad: {runner.real_calls}"
    assert all(c[0] == "tar" for c in runner.real_calls)
    assert [c[0] for c in runner.stubbed_calls] == ["pg_dump"]

    kinds = sorted(a.kind for a in result.artifacts)
    assert kinds == ["bind_tar", "pg_dump", "volume_tar", "volume_tar", "volume_tar"]

    # Cada tar.gz existe, no está vacío, y su sha256 del manifest cuadra con el
    # disco (los bytes son reales, no fabricados por un doble).
    manifest = json.loads((result.bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["encrypted"] is False
    assert manifest["total_size_bytes"] > 0
    # La contraseña del DSN no viaja al manifest.
    assert "secreto" not in json.dumps(manifest)

    for art in result.artifacts:
        target = result.bundle_dir / art.path
        assert target.exists(), art.path
        if art.kind != "pg_dump":
            assert target.stat().st_size > 200, f"{art.path} salió vacío"

    # Verificación con el MISMO runner (tar --list real, pg_restore --list stub).
    report = verify_bundle(result.bundle_dir, runner=runner)
    assert report.valid, [c.to_dict() for c in report.failures]
    ran = {c.check for c in report.checks}
    assert CHECK_TAR_LIST in ran and CHECK_CHECKSUM in ran

    # Y lo que de verdad importa: el tar de un volumen se re-extrae idéntico.
    minio = next(a for a in result.artifacts if a.source == "minio_data")
    restored = tmp_path / "restored"
    _extract(result.bundle_dir / minio.path, restored)
    assert (restored / "minio_data.payload").read_bytes() == b"contenido de minio_data\n" * 32
    assert (restored / "sub" / "nested.bin").read_bytes() == bytes(range(256))


def test_a_corrupted_archive_makes_the_real_verification_fail(tmp_path: Path) -> None:
    """La verificación no es decorativa: con bytes reales, corromper el .tar.gz
    la pone en rojo por checksum Y por `tar --list`."""
    settings = _settings(tmp_path)
    runner = PgStubRunner()
    result = run_full_backup(settings=settings, runner=runner, now=_NOW)

    victim = result.bundle_dir / next(a.path for a in result.artifacts if a.kind == "volume_tar")
    victim.write_bytes(b"esto ya no es un gzip valido")

    report = verify_bundle(result.bundle_dir, runner=runner)
    assert not report.valid
    failed = {c.check for c in report.failures}
    assert CHECK_CHECKSUM in failed
    assert CHECK_TAR_LIST in failed, "tar --list real no detectó el gzip roto"


# --------------------------------------------------------------------------- #
# Bundle cifrado: AES-256-GCM real, y el blob se descifra y contiene todo
# --------------------------------------------------------------------------- #


def test_encrypted_bundle_decrypts_back_to_every_artifact(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        backup_encryption_enabled=True,
        backup_encryption_vault_key=_VAULT_KEY,
    )
    runner = PgStubRunner()
    encryptor = BackupEncryptor(
        provider=StaticSecretsProvider({_VAULT_KEY: _KEY_VALUE}),
        vault_key_name=_VAULT_KEY,
    )

    result = run_full_backup(settings=settings, runner=runner, encryptor=encryptor, now=_NOW)

    # 4 tar de captura + 1 tar de colapso del bundle = 5 comandos reales.
    assert len(runner.real_calls) == 5, runner.real_calls
    assert [a.kind for a in result.artifacts] == ["encrypted_bundle"]

    blob = result.bundle_dir / "bundle.tar.enc"
    assert blob.is_file() and blob.stat().st_size > 500
    # Nada en claro sobrevive en reposo.
    leftovers = sorted(p.name for p in result.bundle_dir.iterdir())
    assert leftovers == ["bundle.tar.enc", "manifest.json"], leftovers

    report = verify_bundle(result.bundle_dir, runner=runner, encryptor=encryptor)
    assert report.valid, [c.to_dict() for c in report.failures]
    assert CHECK_DECRYPT in {c.check for c in report.checks}

    # Ida y vuelta: descifrar el blob y comprobar que trae los 5 artefactos.
    work = tmp_path / "decrypted"
    work.mkdir()
    plain = work / "bundle.tar"
    encryptor.decrypt_file(blob, plain)
    _extract(plain, work, gzip=False)
    inside = sorted(p.name for p in work.iterdir() if p.name != "bundle.tar")
    assert "postgres" in inside
    assert "minio_data.tar.gz" in inside
    assert len([n for n in inside if n.endswith(".tar.gz")]) == 4, inside
    assert (work / "postgres" / "toc.dat").read_bytes() == b"PGDMP-synthetic-toc"

    # Y un volumen extraído del bundle descifrado es byte-a-byte el original.
    restored = tmp_path / "restored-enc"
    _extract(work / "minio_data.tar.gz", restored)
    assert (restored / "sub" / "nested.bin").read_bytes() == bytes(range(256))


def test_a_tampered_encrypted_blob_fails_authentication(tmp_path: Path) -> None:
    """GCM autentica: un bit cambiado en el blob NO se descifra en silencio."""
    settings = _settings(
        tmp_path,
        backup_encryption_enabled=True,
        backup_encryption_vault_key=_VAULT_KEY,
    )
    encryptor = BackupEncryptor(
        provider=StaticSecretsProvider({_VAULT_KEY: _KEY_VALUE}),
        vault_key_name=_VAULT_KEY,
    )
    result = run_full_backup(
        settings=settings, runner=PgStubRunner(), encryptor=encryptor, now=_NOW
    )

    blob = result.bundle_dir / "bundle.tar.enc"
    raw = bytearray(blob.read_bytes())
    raw[-1] ^= 0x01
    blob.write_bytes(bytes(raw))

    report = verify_bundle(result.bundle_dir, runner=PgStubRunner(), encryptor=encryptor)
    assert not report.valid
    assert CHECK_DECRYPT in {c.check for c in report.failures}
