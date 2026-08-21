"""La salvaguarda de backups del ADR 0146: los secretos de columna NO viajan.

El ADR 0146 se firmó el 2026-08-01 con la **opción B**: los secretos que un
TENANT configura para integrarse con terceros (client secrets de OIDC, claves
privadas de SAML, credenciales de canal de notificación, secretos de firma de
webhooks entrantes) se quedan cifrados con Fernet en columnas de Postgres, en
vez de migrar a Vault — porque el ADR 0145 decidió desellado manual y migrarlos
dejaría el login SSO caído tras cada reinicio del host.

Pero se firmó **con** una condición que el propio ADR llama no opcional:

    Hoy un dump de Postgres lleva el ciphertext, así que quien tenga el backup
    **y** la variable de entorno tiene los secretos — y el backup viaja a MinIO y
    a destinos externos. Esas columnas se excluyen del bundle, o se cifran con
    una clave distinta, de forma que un dump robado no baste.
    «Sin (1) esta decisión sería peor que el statu quo, porque habría bendecido
    el riesgo sin quitarlo.»

Esto comprueba la vía elegida —**excluirlos**— y su frontera.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workers.backup import BackupConfig, BackupEngine, CommandResult
from workers.backup_secrets import (
    COLUMN_SECRET_COLUMNS,
    COLUMN_SECRET_TABLES,
    exclude_table_data_args,
)
from workers.config import Settings

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 1, 3, 0, 0, tzinfo=UTC)


@dataclass
class FakeRunner:
    calls: list[list[str]] = field(default_factory=list)

    def run(
        self,
        args: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        argv = list(args)
        self.calls.append(argv)
        if argv[0] == "pg_dump":
            out = _arg_value(argv, "--file=")
            assert out is not None
            Path(out).mkdir(parents=True, exist_ok=True)
            (Path(out) / "toc.dat").write_bytes(b"fake-toc")
        elif argv[0] == "tar":
            archive = _arg_value(argv, "--file=")
            assert archive is not None
            Path(archive).write_bytes(b"fake-tar")
        return CommandResult(returncode=0)


def _arg_value(argv: list[str], prefix: str) -> str | None:
    for token in argv:
        if token.startswith(prefix):
            return token[len(prefix) :]
    return None


def _config(tmp_path: Path, **overrides: object) -> BackupConfig:
    kwargs: dict[str, object] = {
        "backup_root": tmp_path / "backups",
        "database_url": "postgresql://migrations_user:s3cr3t@postgres:5432/agentic_platform",
        "volumes": (),
        "volumes_mount_root": tmp_path / "volumes",
        "retention_days": 7,
        "column_secret_tables": COLUMN_SECRET_TABLES,
    }
    kwargs.update(overrides)
    return BackupConfig(**kwargs)  # type: ignore[arg-type]


def _pg_dump_argv(runner: FakeRunner) -> list[str]:
    for argv in runner.calls:
        if argv[0] == "pg_dump":
            return argv
    raise AssertionError("pg_dump no se invocó")


# --------------------------------------------------------------------------
# La exclusión
# --------------------------------------------------------------------------


def test_the_dump_excludes_the_data_of_every_tenant_secret_table(tmp_path: Path) -> None:
    runner = FakeRunner()

    BackupEngine(_config(tmp_path), runner=runner, now=_NOW).run_full_backup()

    argv = _pg_dump_argv(runner)
    for table in COLUMN_SECRET_TABLES:
        assert f"--exclude-table-data={table}" in argv, (
            f"el dump sigue llevando las filas de {table}, o sea el ciphertext de "
            f"sus secretos: un dump robado + la env var de columna los abre (ADR 0146)"
        )


def test_the_schema_still_travels_so_the_restore_rebuilds_the_tables(tmp_path: Path) -> None:
    """`--exclude-table-data` (no `--exclude-table`): la tabla vuelve, vacía.

    La diferencia importa: sin la DEFINICIÓN, el restore dejaría una base sin
    esas tablas y la aplicación no arrancaría. Con ella, vuelve el esquema y lo
    que hay que rehacer es la configuración, que es lo que el runbook ordena.
    """
    runner = FakeRunner()

    BackupEngine(_config(tmp_path), runner=runner, now=_NOW).run_full_backup()

    argv = _pg_dump_argv(runner)
    for table in COLUMN_SECRET_TABLES:
        assert f"--exclude-table={table}" not in argv


def test_the_manifest_says_which_tables_did_not_travel(tmp_path: Path) -> None:
    """Un backup al que le falta algo a propósito tiene que decirlo en el acta.

    Es lo único que separa «decisión de diseño» de «pérdida de datos silenciosa»
    para quien abra el bundle dentro de seis meses.
    """
    runner = FakeRunner()

    result = BackupEngine(_config(tmp_path), runner=runner, now=_NOW).run_full_backup()

    manifest = json.loads((result.bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["column_secrets"]["excluded_tables"] == list(COLUMN_SECRET_TABLES)


def test_an_operator_can_turn_the_exclusion_off_and_the_manifest_records_it(
    tmp_path: Path,
) -> None:
    """La palanca existe y su rastro también: nadie descubre la vuelta atrás por sorpresa."""
    runner = FakeRunner()

    result = BackupEngine(
        _config(tmp_path, column_secret_tables=()), runner=runner, now=_NOW
    ).run_full_backup()

    assert not any(a.startswith("--exclude-table-data=") for a in _pg_dump_argv(runner))
    manifest = json.loads((result.bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["column_secrets"]["excluded_tables"] == []


def test_exclude_table_data_args_ignores_blanks() -> None:
    assert exclude_table_data_args(("a", "", "b")) == [
        "--exclude-table-data=a",
        "--exclude-table-data=b",
    ]


# --------------------------------------------------------------------------
# La frontera del ADR 0146, que es lo que hace que la excepción no crezca
# --------------------------------------------------------------------------


def test_the_excluded_tables_are_exactly_the_three_families_the_adr_names() -> None:
    """Ni una más. La frontera del ADR 0146 es el secreto que un TENANT configura
    para un tercero; las credenciales de PLATAFORMA siguen en Vault, y una tabla
    de más aquí sería pérdida de datos sin ganancia de seguridad."""
    assert COLUMN_SECRET_TABLES == (
        "sso_configurations",
        "notification_channels",
        "incoming_webhook_configs",
    )


def test_every_declared_column_exists_in_the_real_orm_model() -> None:
    """Si alguien renombra la columna cifrada, este fichero se entera.

    Sin esto la lista sería una copia envejecida del esquema: seguiría
    excluyendo la tabla correcta por casualidad, pero la documentación de QUÉ
    secreto vive ahí —que es lo que el ADR 0146 acota— dejaría de ser cierta.
    """
    import api_server.db.notification  # noqa: F401  (registra las tablas en el Base)
    from api_server.db.models import Base

    tables = Base.metadata.tables
    for table, columns in COLUMN_SECRET_COLUMNS.items():
        assert table in tables, f"{table} ya no existe en el esquema"
        present = set(tables[table].columns.keys())
        missing = [c for c in columns if c not in present]
        assert not missing, f"{table} ya no tiene {missing}: la frontera del ADR 0146 miente"


def test_the_settings_default_matches_the_adr(tmp_path: Path) -> None:
    """El default del `Settings` es el seguro: hay que ACTUAR para que viajen."""
    settings = Settings(backup_root=str(tmp_path))  # type: ignore[call-arg]
    assert tuple(settings.backup_column_secret_tables) == COLUMN_SECRET_TABLES
    assert BackupConfig.from_settings(settings).column_secret_tables == COLUMN_SECRET_TABLES
