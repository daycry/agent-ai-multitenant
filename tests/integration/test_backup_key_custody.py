"""Custodia offsite de la clave que descifra el bundle (prod-04 task_prod_04_07).

La circularidad (hallazgo gap1-1)
---------------------------------
1. El bundle se cifra con AES-256-GCM usando `WORKERS_BACKUP_ENCRYPTION_KEY`.
2. Esa variable vive en el entorno de **la misma máquina que se respalda**.
3. El backend de Vault (`vault_data`) viaja **DENTRO** del blob cifrado.

Consecuencia: ante pérdida total del host, el bundle es matemáticamente
irrecuperable. Y las unseal keys que sí están custodiadas **no descifran
AES-GCM** — solo abren un Vault que está dentro del blob que no se puede abrir.
Tres runbooks afirmaban que «Vault resuelve la clave»; era falso
(`EnvSecretsProvider` lee `os.environ`).

Lo que un control automático PUEDE hacer
----------------------------------------
No puede probar que el sobre sellado contiene la clave — eso solo lo prueba el
drill, recuperándola de la custodia y restaurando con ella. Lo que sí puede es
comprobar que la clave ACTIVA es la que alguien declaró haber depositado, y
negarse a producir un bundle cifrado cuando no coincide. Eso convierte una
rotación de clave sin actualizar la custodia — que dejaría meses de bundles que
nadie puede abrir — en un backup fallido esa misma noche.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workers.backup import (
    BackupConfig,
    BackupEngine,
    BackupError,
    CommandResult,
    run_full_backup,
)
from workers.backup_encryption import BackupEncryptor
from workers.config import Settings
from workers.secrets import StaticSecretsProvider

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 29, 3, 0, 0, tzinfo=UTC)
_VAULT_KEY = "backup_encryption_key"
_KEY = "clave-de-prueba-no-secreta"
_OTRA_KEY = "una-clave-distinta"


class Runner:
    """Fabrica los artefactos (aquí solo interesa el gate, no el tar real)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        argv = list(args)
        self.calls.append(argv)
        target = next((a[len("--file=") :] for a in argv if a.startswith("--file=")), None)
        assert target is not None
        if argv[0] == "pg_dump":
            Path(target).mkdir(parents=True, exist_ok=True)
            (Path(target) / "toc.dat").write_bytes(b"toc")
        else:
            Path(target).write_bytes(b"tar-bytes")
        return CommandResult(returncode=0)


def _encryptor(secret: str = _KEY) -> BackupEncryptor:
    return BackupEncryptor(
        provider=StaticSecretsProvider({_VAULT_KEY: secret}),
        vault_key_name=_VAULT_KEY,
    )


def _prod_settings(**overrides: object) -> Settings:
    """`Settings` fuera de dev. El guard de credenciales-dev de este propio
    Settings exige un DSN sin marcadores `changeme`/`dev-only`, así que hay que
    darle uno sintético: no se conecta a nada, solo pasa la validación."""
    base: dict[str, object] = {
        "environment": "prod",
        "database_url": "postgresql+asyncpg://service_user:sintetica@postgres:5432/agentic",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _config(tmp_path: Path, **overrides: object) -> BackupConfig:
    volumes_root = tmp_path / "volumes"
    (volumes_root / "minio_data" / "_data").mkdir(parents=True)
    base: dict[str, object] = {
        "backup_root": tmp_path / "backups",
        "database_url": "postgresql://u:p@db:5432/x",
        "volumes": ("minio_data",),
        "volumes_mount_root": volumes_root,
        "retention_days": 7,
        "encryption_enabled": True,
        "encryption_vault_key": _VAULT_KEY,
    }
    base.update(overrides)
    return BackupConfig(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# La huella
# --------------------------------------------------------------------------- #


def test_the_fingerprint_is_stable_key_specific_and_not_the_key() -> None:
    fp = _encryptor().key_fingerprint()
    assert fp == _encryptor().key_fingerprint(), "la huella tiene que ser determinista"
    assert fp != _encryptor(_OTRA_KEY).key_fingerprint(), "dos claves, dos huellas"
    assert len(fp) == 64 and all(c in "0123456789abcdef" for c in fp)
    # Ni el secreto ni la clave derivada aparecen en la huella.
    assert _KEY not in fp
    import hashlib

    assert (
        fp != hashlib.sha256(_KEY.encode()).hexdigest()
    ), "la huella es la clave derivada: publicarla en el manifest publicaría la clave"


# --------------------------------------------------------------------------- #
# Fail-closed
# --------------------------------------------------------------------------- #


def test_a_mismatched_custody_fingerprint_fails_the_backup(tmp_path: Path) -> None:
    """Alguien rotó la clave y no actualizó la custodia. Sin este gate, los
    bundles de esta noche los podría abrir nadie."""
    cfg = _config(tmp_path, key_custody_fingerprint=_encryptor(_OTRA_KEY).key_fingerprint())
    runner = Runner()
    engine = BackupEngine(cfg, runner=runner, encryptor=_encryptor(), now=_NOW)

    with pytest.raises(BackupError, match="no es la que está declarada en custodia"):
        engine.run_full_backup()

    # Y falla ANTES de gastar una hora en pg_dump + tar.
    assert runner.calls == [], f"el gate corrió demasiado tarde: {runner.calls}"
    assert not (cfg.backup_root / _NOW.strftime("%Y%m%dT%H%M%SZ")).exists()


def test_an_undeclared_key_fails_outside_dev(tmp_path: Path) -> None:
    cfg = _config(tmp_path, key_custody_fingerprint="", require_key_custody=True)
    engine = BackupEngine(cfg, runner=Runner(), encryptor=_encryptor(), now=_NOW)
    with pytest.raises(BackupError, match="NO está declarada en custodia offsite"):
        engine.run_full_backup()


def test_the_error_hands_the_operator_the_fingerprint_to_register(tmp_path: Path) -> None:
    """Un mensaje accionable: si no trae la huella, el operador no sabe qué
    escribir en la variable y el gate se convierte en un muro."""
    cfg = _config(tmp_path, key_custody_fingerprint="", require_key_custody=True)
    engine = BackupEngine(cfg, runner=Runner(), encryptor=_encryptor(), now=_NOW)
    with pytest.raises(BackupError) as exc_info:
        engine.run_full_backup()
    message = str(exc_info.value)
    assert _encryptor().key_fingerprint() in message
    assert "WORKERS_BACKUP_KEY_CUSTODY_FINGERPRINT" in message
    # Y deja claro que las unseal keys NO sirven (los runbooks decían lo contrario).
    assert "unseal keys no descifran" in message


def test_an_undeclared_key_only_warns_in_dev(tmp_path: Path) -> None:
    """En dev el backup sigue: exigir custodia en un portátil no protege nada y
    haría que un desarrollador desactivase el cifrado, que es peor."""
    cfg = _config(tmp_path, key_custody_fingerprint="", require_key_custody=False)
    result = BackupEngine(cfg, runner=Runner(), encryptor=_encryptor(), now=_NOW).run_full_backup()
    assert result.artifacts


# --------------------------------------------------------------------------- #
# El manifest
# --------------------------------------------------------------------------- #


def test_a_matching_fingerprint_lands_in_the_manifest(tmp_path: Path) -> None:
    """Quien restaure puede comprobar ANTES de intentarlo que la clave que sacó
    de la custodia es la correcta, en vez de descubrirlo por un InvalidTag."""
    fingerprint = _encryptor().key_fingerprint()
    cfg = _config(tmp_path, key_custody_fingerprint=fingerprint)
    result = BackupEngine(cfg, runner=Runner(), encryptor=_encryptor(), now=_NOW).run_full_backup()

    manifest = json.loads((result.bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["encrypted"] is True
    assert manifest["key_fingerprint"] == fingerprint
    # Nunca la clave.
    assert _KEY not in json.dumps(manifest)


def test_a_plaintext_bundle_has_no_fingerprint(tmp_path: Path) -> None:
    cfg = _config(tmp_path, encryption_enabled=False, require_key_custody=True)
    result = BackupEngine(cfg, runner=Runner(), now=_NOW).run_full_backup()
    manifest = json.loads((result.bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["encrypted"] is False
    assert manifest["key_fingerprint"] is None


# --------------------------------------------------------------------------- #
# El cableado desde Settings
# --------------------------------------------------------------------------- #


def test_the_custody_requirement_follows_the_environment(tmp_path: Path) -> None:
    dev = BackupConfig.from_settings(Settings(environment="dev"))
    assert dev.require_key_custody is False
    for env in ("staging", "prod"):
        cfg = BackupConfig.from_settings(_prod_settings(environment=env))
        assert cfg.require_key_custody is True, env


def test_the_fingerprint_is_normalised_from_settings() -> None:
    """Un fingerprint copiado de un log con espacios o en mayúsculas tiene que
    seguir coincidiendo; si no, el gate falla por un motivo cosmético a las 3 AM."""
    fingerprint = _encryptor().key_fingerprint()
    cfg = BackupConfig.from_settings(
        Settings(backup_key_custody_fingerprint=f"  {fingerprint.upper()}  ")
    )
    assert cfg.key_custody_fingerprint == fingerprint


def test_the_entrypoint_enforces_custody_too(tmp_path: Path) -> None:
    """`run_full_backup()` construye su propio encryptor desde el entorno; el
    gate tiene que aplicar también por ese camino, que es el del beat diario."""
    volumes_root = tmp_path / "volumes"
    (volumes_root / "minio_data" / "_data").mkdir(parents=True)
    settings = _prod_settings(
        backup_root=str(tmp_path / "backups"),
        backup_database_url="postgresql://u:p@db:5432/x",
        backup_volumes=["minio_data"],
        backup_volumes_mount_root=str(volumes_root),
        backup_bind_paths=[],
        backup_projects_root="",
        backup_encryption_enabled=True,
        backup_encryption_vault_key=_VAULT_KEY,
        backup_key_custody_fingerprint=_encryptor(_OTRA_KEY).key_fingerprint(),
    )
    with pytest.raises(BackupError, match="declarada en custodia"):
        run_full_backup(settings=settings, runner=Runner(), encryptor=_encryptor(), now=_NOW)
