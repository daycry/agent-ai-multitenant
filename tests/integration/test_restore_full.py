"""Integration tests for the full restore engine (Plan 12 task_12_10).

Real ``pg_restore`` / ``docker compose stop|up`` / live volume writes CANNOT run
in the test environment, so the external-command seam
(:class:`workers.backup.CommandRunner`) is MOCKED. One fake runner is used both
to BUILD a real bundle on disk (via the Phase A :class:`workers.backup.BackupEngine`,
fabricating the pg_dump dir + the volume tars + answering the verifier's
``pg_restore --list`` / ``tar -tf`` probes) AND to drive the RESTORE (recording
the pg_restore / compose / tar-extract argv, fabricating the extracted trees).

The tests assert:

  * VERIFY-BEFORE-RESTORE — the bundle is verified (manifest checksums + the
    structural probes) BEFORE any destructive command; a verification failure
    ABORTS before pg_restore / compose-stop / volume-wipe ever run (fail closed,
    :class:`workers.restore.RestoreVerificationError`).
  * ORDERING — verify → stop app stack → pg_restore → stop volume services →
    restore volumes → start stack, in that order.
  * COMMAND CONSTRUCTION — pg_restore gets ``--clean --if-exists`` + the dump dir
    + the libpq URL; compose ops target the configured project + file; each
    volume tar is extracted into its ``_data`` mount tree.
  * DECRYPT — an encrypted bundle is decrypted (Vault key) + extracted BEFORE
    verification; a tampered blob fails closed with NOTHING destructive run.
  * DOUBLE CONFIRMATION — a wrong/absent confirm token refuses before any work.
  * FAILURE HANDLING — a failed step (pg_restore non-zero, compose stop non-zero)
    surfaces a typed :class:`workers.restore.RestoreError`; the stack is brought
    back up even when a step fails.

No real restore of a live stack runs here — that is HUMAN test human_12_02.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workers.backup import BackupConfig, BackupEngine, CommandResult
from workers.backup_encryption import BackupEncryptor
from workers.restore import (
    RestoreConfig,
    RestoreEngine,
    RestoreError,
    RestorePartialError,
    RestoreVerificationError,
    run_full_restore,
)
from workers.secrets import StaticSecretsProvider

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 30, 3, 0, 0, tzinfo=UTC)

_VAULT_KEY_NAME = "backup_encryption_key"
_VAULT_KEY_VALUE = "s3cr3t-vault-backup-key-MUST-NOT-LEAK-0123456789abcdef"
_DB_URL = "postgresql://migrations_user:s3cr3t@db:5432/agentic_platform"

# A canned, non-empty pg_restore --list TOC (mirrors the verification test) so
# the verify-before-restore gate passes on a good bundle.
_GOOD_TOC = (
    ";\n"
    "; Archive created at 2026-05-30 03:00:00 UTC\n"
    ";\n"
    "215; 1259 16401 TABLE public tenants migrations_user\n"
)


def _arg_value(argv: list[str], prefix: str) -> str:
    for token in argv:
        if token.startswith(prefix):
            return token[len(prefix) :]
    raise AssertionError(f"no arg with prefix {prefix!r} in {argv!r}")


# --------------------------------------------------------------------------- #
# Runners.
# --------------------------------------------------------------------------- #


@dataclass
class BuildRunner:
    """Fabricates a real plaintext bundle on disk via the BackupEngine.

    Handles pg_dump (a directory dump) + the per-volume tar (writes a real
    gzip-ish placeholder file). When ``encrypt_bundle_tar`` is set it also folds
    the member bytes into the bundle-collapse tar so the round-trip decrypt
    recovers real content.
    """

    encrypt_bundle_tar: bool = False
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
            out_dir = Path(_arg_value(argv, "--file="))
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "toc.dat").write_bytes(b"fake-toc")
            (out_dir / "3434.dat.gz").write_bytes(b"fake-table-data")
            return CommandResult(returncode=0)
        if argv[0] == "tar":
            archive = Path(_arg_value(argv, "--file="))
            directory = Path(_arg_value(argv, "--directory="))
            members = [t for t in argv[2:] if not t.startswith("--")]
            if self.encrypt_bundle_tar and members and members != ["."]:
                # Bundle-collapse tar: fold each member's bytes in so the
                # encrypted blob wraps real, recoverable content.
                out = bytearray(b"FAKETAR\0")
                for name in members:
                    target = directory / name
                    if target.is_dir():
                        blob = b"".join(
                            p.read_bytes() for p in sorted(target.rglob("*")) if p.is_file()
                        )
                    else:
                        blob = target.read_bytes()
                    out += name.encode() + b"\0" + str(len(blob)).encode() + b"\0" + blob
                archive.write_bytes(bytes(out))
            else:
                archive.write_bytes(b"fake-volume-tar-" + directory.parent.name.encode())
            return CommandResult(returncode=0)
        raise AssertionError(f"unexpected build command: {argv!r}")


@dataclass
class RestoreRunner:
    """Records restore argv + fabricates extracted artifacts; tunable failures.

    Answers the verifier probes (``pg_restore --list`` → a good TOC, ``tar
    --list`` → member names) so a good bundle verifies, and the restore commands
    (``docker compose ... stop|up``, ``pg_restore <dir>``, ``tar --extract``).

    ``fail_pg_restore`` / ``fail_compose_stop`` / ``fail_verify`` make the
    matching step return non-zero so failure handling + fail-closed can be
    exercised. ``decrypt_tar`` un-folds the BuildRunner's bundle-collapse tar
    when extracting the decrypted bundle.
    """

    fail_pg_restore: bool = False
    fail_compose_stop: bool = False
    fail_verify: bool = False
    fail_grants: bool = False
    fail_volume_extract: bool = False
    decrypt_tar: bool = False
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
        # Dispatch to a small handler per command shape so the single function
        # stays under the branch/return budget (ruff PLR0911).
        if argv[0] == "pg_restore" and "--list" in argv:
            return self._verify_pg(argv)
        if argv[0] == "tar" and "--list" in argv:
            return self._verify_tar(argv)
        if argv[0] == "tar" and "--extract" in argv:
            return self._extract(argv)
        if argv[0] == "docker":
            return self._compose(argv)
        if argv[0] == "pg_restore":
            return self._pg_restore(argv)
        if argv[0] == "psql":
            return self._psql(argv)
        raise AssertionError(f"unexpected restore command: {argv!r}")

    def _psql(self, argv: list[str]) -> CommandResult:
        """prod-04 task_prod_04_08: la re-concesión de GRANTs post-restore."""
        if self.fail_grants:
            return CommandResult(returncode=1, stderr='ERROR: role "app_user" does not exist')
        return CommandResult(returncode=0)

    # -- verifier probes (verify-before-restore) ---------------------------

    def _verify_pg(self, argv: list[str]) -> CommandResult:
        if self.fail_verify:
            return CommandResult(returncode=1, stderr="pg_restore: corrupt archive")
        return CommandResult(returncode=0, stdout=_GOOD_TOC)

    def _verify_tar(self, argv: list[str]) -> CommandResult:
        if self.fail_verify:
            return CommandResult(returncode=2, stderr="tar: Unexpected EOF")
        return CommandResult(returncode=0, stdout="./\n./objects/\n")

    # -- restore commands ---------------------------------------------------

    def _extract(self, argv: list[str]) -> CommandResult:
        directory = Path(_arg_value(argv, "--directory="))
        if "--gzip" in argv:
            if self.fail_volume_extract:
                return CommandResult(returncode=2, stderr="tar: write error: No space left")
            # A volume tar extracted into its _data tree.
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "restored-marker").write_text("x", encoding="utf-8")
        elif self.decrypt_tar:
            # The decrypted bundle.tar un-tar'd into the bundle dir.
            _unfold_bundle_tar(Path(_arg_value(argv, "--file=")), directory)
        return CommandResult(returncode=0)

    def _compose(self, argv: list[str]) -> CommandResult:
        if "stop" in argv and self.fail_compose_stop:
            return CommandResult(returncode=1, stderr="compose: no such service")
        return CommandResult(returncode=0)

    def _pg_restore(self, argv: list[str]) -> CommandResult:
        if self.fail_pg_restore:
            return CommandResult(returncode=1, stderr="pg_restore: relation already exists")
        return CommandResult(returncode=0)


def _unfold_bundle_tar(archive: Path, directory: Path) -> None:
    """Reverse BuildRunner's bundle-collapse tar back into member files/dirs."""
    data = archive.read_bytes()
    assert data.startswith(b"FAKETAR\0")
    rest = data[len(b"FAKETAR\0") :]
    while rest:
        name, _, after = rest.partition(b"\0")
        size_b, _, after = after.partition(b"\0")
        size = int(size_b)
        blob = after[:size]
        rest = after[size:]
        target = directory / name.decode()
        if name.decode() == "postgres":
            target.mkdir(parents=True, exist_ok=True)
            (target / "toc.dat").write_bytes(blob)
        else:
            target.write_bytes(blob)


# --------------------------------------------------------------------------- #
# Config + bundle helpers.
# --------------------------------------------------------------------------- #


def _backup_config(tmp_path: Path, *, encryption_enabled: bool = False) -> BackupConfig:
    return BackupConfig(
        backup_root=tmp_path / "backups",
        database_url=_DB_URL,
        volumes=("minio_data", "redis_data"),
        volumes_mount_root=tmp_path / "volumes",
        retention_days=7,
        encryption_enabled=encryption_enabled,
        encryption_vault_key=_VAULT_KEY_NAME,
    )


#: Los servicios que los tests paran. El preflight de prod-04 exige que estén
#: DECLARADOS en el compose al que apunta la config, así que los tests escriben
#: su propio compose en tmp en vez de apuntar al versionado (que a propósito no
#: declara los servicios de aplicación — por eso el preflight existe).
_APP_SERVICES = ("api-server", "workers")
_VOLUME_SERVICES = ("minio", "redis")


def _write_compose(tmp_path: Path, services: Sequence[str]) -> Path:
    path = tmp_path / "docker-compose.yml"
    body = "name: agentic-platform\nservices:\n" + "".join(
        f"  {name}:\n    image: example/{name}:test\n" for name in services
    )
    path.write_text(body, encoding="utf-8")
    return path


def _restore_config(
    tmp_path: Path,
    *,
    encryption_enabled: bool = False,
    compose_file: Path | None = None,
    autostart_on_failure: bool = False,
    projects_root: str = "",
    bind_paths: tuple[str, ...] = (),
    required_db_role: str = "migrations_user",
    grant_app_role: str = "app_user",
) -> RestoreConfig:
    return RestoreConfig(
        backup_root=tmp_path / "backups",
        database_url=_DB_URL,
        volumes=("minio_data", "redis_data"),
        volumes_mount_root=tmp_path / "restore-volumes",
        compose_project="agentic-platform",
        compose_file=(
            compose_file
            if compose_file is not None
            else _write_compose(tmp_path, (*_APP_SERVICES, *_VOLUME_SERVICES, "postgres"))
        ),
        app_services=_APP_SERVICES,
        volume_services=_VOLUME_SERVICES,
        projects_root=projects_root,
        bind_paths=bind_paths,
        autostart_on_failure=autostart_on_failure,
        required_db_role=required_db_role,
        grant_app_role=grant_app_role,
        encryption_enabled=encryption_enabled,
        encryption_vault_key=_VAULT_KEY_NAME,
    )


def _encryptor() -> BackupEncryptor:
    return BackupEncryptor(
        provider=StaticSecretsProvider(values={_VAULT_KEY_NAME: _VAULT_KEY_VALUE}),
        vault_key_name=_VAULT_KEY_NAME,
    )


def _build_plaintext_bundle(tmp_path: Path) -> Path:
    runner = BuildRunner()
    engine = BackupEngine(_backup_config(tmp_path), runner=runner, now=_NOW)
    return engine.run_full_backup().bundle_dir


def _build_encrypted_bundle(tmp_path: Path) -> Path:
    runner = BuildRunner(encrypt_bundle_tar=True)
    engine = BackupEngine(
        _backup_config(tmp_path, encryption_enabled=True),
        runner=runner,
        encryptor=_encryptor(),
        now=_NOW,
    )
    return engine.run_full_backup().bundle_dir


# --------------------------------------------------------------------------- #
# Happy path: ordering + command construction.
# --------------------------------------------------------------------------- #


def test_full_restore_runs_steps_in_order(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner()
    engine = RestoreEngine(_restore_config(tmp_path), runner=runner)

    result = engine.run_full_restore(bundle, confirm=bundle.name)

    assert result.backup_id == "20260530T030000Z"
    assert result.encrypted is False
    assert result.restored_volumes == ("minio_data", "redis_data")

    # Reduce the call stream to an ordered list of (verb) phases.
    phases: list[str] = []
    for c in runner.calls:
        if c[0] == "pg_restore" and "--list" in c:
            phases.append("verify_pg")
        elif c[0] == "tar" and "--list" in c:
            phases.append("verify_tar")
        elif c[0] == "docker" and "stop" in c:
            phases.append("stop:" + ",".join(t for t in c if t in {"api-server", "minio"}))
        elif c[0] == "pg_restore":
            phases.append("pg_restore")
        elif c[0] == "tar" and "--extract" in c:
            phases.append("volume_extract")
        elif c[0] == "docker" and "up" in c:
            phases.append("up")

    # Verification probes come first; then app-stack stop; then pg_restore;
    # then volume-service stop; then the volume extracts; then the stack up.
    assert phases.index("verify_pg") < phases.index("stop:api-server")
    assert phases.index("verify_tar") < phases.index("stop:api-server")
    assert phases.index("stop:api-server") < phases.index("pg_restore")
    assert phases.index("pg_restore") < phases.index("stop:minio")
    assert phases.index("stop:minio") < phases.index("volume_extract")
    assert phases.index("volume_extract") < phases.index("up")


def test_pg_restore_command_construction(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner()
    RestoreEngine(_restore_config(tmp_path), runner=runner).run_full_restore(
        bundle, confirm=bundle.name
    )

    pg_calls = [c for c in runner.calls if c[0] == "pg_restore" and "--list" not in c]
    assert len(pg_calls) == 1
    argv = pg_calls[0]
    # --clean --if-exists so a full restore drops + recreates existing objects.
    assert "--clean" in argv
    assert "--if-exists" in argv
    assert "--no-owner" in argv
    assert f"--dbname={_DB_URL}" in argv
    # The LOGICAL directory dump inside the bundle is the restore source.
    assert str(bundle / "postgres") in argv


def test_compose_ops_target_configured_project_and_file(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner()
    RestoreEngine(_restore_config(tmp_path), runner=runner).run_full_restore(
        bundle, confirm=bundle.name
    )

    compose_file = str(tmp_path / "docker-compose.yml")  # OS-native separators
    compose_calls = [c for c in runner.calls if c[0] == "docker"]
    assert compose_calls  # at least stop(app), stop(volumes), up
    for c in compose_calls:
        assert c[:2] == ["docker", "compose"]
        assert "--project-name" in c and "agentic-platform" in c
        assert "--file" in c and compose_file in c

    # The app stop targets the app services; the DB is NOT stopped.
    app_stop = next(c for c in compose_calls if "stop" in c and "api-server" in c)
    assert "workers" in app_stop
    assert "db" not in app_stop and "postgres" not in app_stop
    # A separate stop targets the volume-backing services.
    vol_stop = next(c for c in compose_calls if "stop" in c and "minio" in c)
    assert "redis" in vol_stop
    # The stack is brought back up detached.
    up = next(c for c in compose_calls if "up" in c)
    assert "--detach" in up


def test_volume_tars_extracted_into_their_data_trees(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner()
    cfg = _restore_config(tmp_path)
    RestoreEngine(cfg, runner=runner).run_full_restore(bundle, confirm=bundle.name)

    extract_calls = [c for c in runner.calls if c[0] == "tar" and "--extract" in c]
    targets = [_arg_value(c, "--directory=") for c in extract_calls]
    assert targets == [
        str(cfg.volumes_mount_root / "minio_data" / "_data"),
        str(cfg.volumes_mount_root / "redis_data" / "_data"),
    ]
    for c in extract_calls:
        assert "--gzip" in c
        archive = _arg_value(c, "--file=")
        assert archive.endswith(".tar.gz")


# --------------------------------------------------------------------------- #
# Verify-before-restore, fail closed.
# --------------------------------------------------------------------------- #


def test_verification_failure_aborts_before_anything_destructive(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner(fail_verify=True)
    engine = RestoreEngine(_restore_config(tmp_path), runner=runner)

    with pytest.raises(RestoreVerificationError) as exc_info:
        engine.run_full_restore(bundle, confirm=bundle.name)

    # The report is attached so the UI can show what failed.
    assert exc_info.value.report.valid is False

    # NOTHING destructive ran: no compose stop, no pg_restore, no volume extract.
    assert not any(c[0] == "docker" for c in runner.calls)
    assert not any(c[0] == "pg_restore" and "--list" not in c for c in runner.calls)
    assert not any(c[0] == "tar" and "--extract" in c for c in runner.calls)


def test_checksum_mismatch_aborts_fail_closed(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    # Corrupt a volume archive AFTER the manifest captured its checksum.
    (bundle / "minio_data.tar.gz").write_bytes(b"tampered bytes not in the manifest")
    runner = RestoreRunner()
    engine = RestoreEngine(_restore_config(tmp_path), runner=runner)

    with pytest.raises(RestoreVerificationError):
        engine.run_full_restore(bundle, confirm=bundle.name)

    assert not any(c[0] == "docker" for c in runner.calls)
    assert not any(c[0] == "pg_restore" and "--list" not in c for c in runner.calls)


# --------------------------------------------------------------------------- #
# Double confirmation.
# --------------------------------------------------------------------------- #


def test_wrong_confirm_token_refuses_before_any_command(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner()
    engine = RestoreEngine(_restore_config(tmp_path), runner=runner)

    with pytest.raises(RestoreError, match="confirmation token does not match"):
        engine.run_full_restore(bundle, confirm="not-the-bundle-id")

    # Refused before verification even ran — no commands at all.
    assert runner.calls == []


def test_missing_bundle_raises(tmp_path: Path) -> None:
    runner = RestoreRunner()
    engine = RestoreEngine(_restore_config(tmp_path), runner=runner)

    with pytest.raises(RestoreError, match="backup bundle not found"):
        engine.run_full_restore("20990101T000000Z", confirm="20990101T000000Z")
    assert runner.calls == []


# --------------------------------------------------------------------------- #
# Failure handling: a failed step surfaces RestoreError, stack comes back up.
# --------------------------------------------------------------------------- #


def test_failed_pg_restore_leaves_the_stack_stopped(tmp_path: Path) -> None:
    """FAIL-STOPPED (prod-04 task_prod_04_04) — el contrato INVERSO al anterior.

    Hasta prod-04 el motor tenía un `finally: docker compose up -d` y este mismo
    test afirmaba «even on failure the stack is brought back up». Eso contradecía
    a los dos runbooks de DR («mantén el stack parado para no servir datos
    parciales») y era el peor de los dos mundos: la aplicación arrancaba sobre una
    base de datos a medio hacer `--clean`, o sea con tablas borradas y sin
    restaurar. El test se cambió porque el COMPORTAMIENTO estaba mal, no porque
    estorbase.
    """
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner(fail_pg_restore=True)
    engine = RestoreEngine(_restore_config(tmp_path), runner=runner)

    with pytest.raises(RestorePartialError) as exc_info:
        engine.run_full_restore(bundle, confirm=bundle.name)

    err = exc_info.value
    assert err.stage == "app_stack_stopped"
    assert err.stack_running is False
    # El mensaje lleva estado + siguiente paso (es lo que necesita un operador).
    assert "sigue PARADO" in str(err)
    assert "RE-EJECUTA" in str(err)

    # Y lo que importa: NADIE arrancó el stack.
    assert not any(
        c[0] == "docker" and "up" in c for c in runner.calls
    ), "el restore arrancó la aplicación sobre una base de datos a medio restaurar"
    # No volume extract ran — pg_restore failed before the volume step.
    assert not any(c[0] == "tar" and "--extract" in c for c in runner.calls)


def test_failed_volume_extract_also_leaves_the_stack_stopped(tmp_path: Path) -> None:
    """Un fallo MÁS TARDE (ya con la BD restaurada) tampoco arranca nada, y el
    `stage` dice hasta dónde se llegó."""
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner(fail_volume_extract=True)
    engine = RestoreEngine(_restore_config(tmp_path), runner=runner)

    with pytest.raises(RestorePartialError) as exc_info:
        engine.run_full_restore(bundle, confirm=bundle.name)

    assert exc_info.value.stage == "grants_reapplied"
    assert not any(c[0] == "docker" and "up" in c for c in runner.calls)


def test_autostart_on_failure_is_opt_in(tmp_path: Path) -> None:
    """El arranque tras un fallo existe, pero solo si el operador lo pide.

    Sin este test el default podría cambiarse sin que nada se quejara.
    """
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner(fail_pg_restore=True)
    engine = RestoreEngine(_restore_config(tmp_path, autostart_on_failure=True), runner=runner)

    with pytest.raises(RestorePartialError) as exc_info:
        engine.run_full_restore(bundle, confirm=bundle.name)

    assert exc_info.value.stack_running is True
    assert "se ha ARRANCADO" in str(exc_info.value)
    assert any(c[0] == "docker" and "up" in c for c in runner.calls)


def test_pg_restore_does_not_mask_sql_errors(tmp_path: Path) -> None:
    """`--exit-on-error`: sin él pg_restore continúa tras un error y sale con 0
    «con warnings», y una tabla que no se restauró pasaba por un restore bueno."""
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner()
    RestoreEngine(_restore_config(tmp_path), runner=runner).run_full_restore(
        bundle, confirm=bundle.name
    )
    argv = next(c for c in runner.calls if c[0] == "pg_restore" and "--list" not in c)
    assert "--exit-on-error" in argv, f"pg_restore enmascararía errores de SQL: {argv}"


def test_failed_compose_stop_raises(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner(fail_compose_stop=True)
    engine = RestoreEngine(_restore_config(tmp_path), runner=runner)

    with pytest.raises(RestoreError, match="stopping the app stack failed"):
        engine.run_full_restore(bundle, confirm=bundle.name)

    # pg_restore never ran — the stop failed first (the DB writers are not safely
    # stopped, so we must not touch the DB).
    assert not any(c[0] == "pg_restore" and "--list" not in c for c in runner.calls)


# --------------------------------------------------------------------------- #
# Encrypted bundle: decrypt + verify before any restore.
# --------------------------------------------------------------------------- #


def test_encrypted_bundle_is_decrypted_then_verified_then_restored(tmp_path: Path) -> None:
    bundle = _build_encrypted_bundle(tmp_path)
    # Sanity: the on-disk bundle is the single encrypted blob, no plaintext dump.
    assert (bundle / "bundle.tar.enc").is_file()
    assert not (bundle / "postgres").exists()

    runner = RestoreRunner(decrypt_tar=True)
    engine = RestoreEngine(
        _restore_config(tmp_path, encryption_enabled=True),
        runner=runner,
        encryptor=_encryptor(),
    )

    result = engine.run_full_restore(bundle, confirm=bundle.name)

    assert result.encrypted is True
    # Decryption extracted the plaintext layout BEFORE the verifier probed it.
    assert (bundle / "postgres").exists()
    # And the destructive steps ran after a successful verify.
    assert any(c[0] == "pg_restore" and "--list" not in c for c in runner.calls)


def test_tampered_encrypted_bundle_fails_closed_no_destructive(tmp_path: Path) -> None:
    bundle = _build_encrypted_bundle(tmp_path)
    # Flip a byte in the encrypted blob → GCM tag fails → decrypt aborts.
    blob = bundle / "bundle.tar.enc"
    data = bytearray(blob.read_bytes())
    data[-1] ^= 0x01
    blob.write_bytes(bytes(data))

    runner = RestoreRunner(decrypt_tar=True)
    engine = RestoreEngine(
        _restore_config(tmp_path, encryption_enabled=True),
        runner=runner,
        encryptor=_encryptor(),
    )

    with pytest.raises(RestoreError, match="failed to decrypt backup bundle"):
        engine.run_full_restore(bundle, confirm=bundle.name)

    # Fail closed: nothing destructive ran on the bad bundle.
    assert not any(c[0] == "docker" for c in runner.calls)
    assert not any(c[0] == "pg_restore" and "--list" not in c for c in runner.calls)


def test_encrypted_bundle_without_encryptor_aborts(tmp_path: Path) -> None:
    bundle = _build_encrypted_bundle(tmp_path)
    runner = RestoreRunner(decrypt_tar=True)
    # encryption_enabled config but NO encryptor injected → cannot decrypt.
    engine = RestoreEngine(_restore_config(tmp_path, encryption_enabled=True), runner=runner)

    with pytest.raises(RestoreError, match="no BackupEncryptor was provided"):
        engine.run_full_restore(bundle, confirm=bundle.name)
    assert not any(c[0] == "docker" for c in runner.calls)


# --------------------------------------------------------------------------- #
# Entrypoint: build the engine from settings.
# --------------------------------------------------------------------------- #


def test_run_full_restore_entrypoint_builds_engine_from_settings(tmp_path: Path) -> None:
    from workers.config import Settings

    bundle = _build_plaintext_bundle(tmp_path)
    settings = Settings(
        backup_root=str(tmp_path / "backups"),
        backup_database_url=_DB_URL,
        backup_volumes=["minio_data", "redis_data"],
        backup_volumes_mount_root=str(tmp_path / "restore-volumes"),
        backup_projects_root="",
        backup_bind_paths=[],
        restore_compose_project="agentic-platform",
        restore_compose_file=str(
            _write_compose(tmp_path, (*_APP_SERVICES, *_VOLUME_SERVICES, "postgres"))
        ),
        restore_app_services=list(_APP_SERVICES),
        restore_volume_services=list(_VOLUME_SERVICES),
    )
    runner = RestoreRunner()

    result = run_full_restore(bundle.name, confirm=bundle.name, settings=settings, runner=runner)

    assert result.backup_id == bundle.name
    assert result.restored_volumes == ("minio_data", "redis_data")


# --------------------------------------------------------------------------- #
# Cross-tenant safety: a FULL restore is platform-global all-or-nothing — it
# never does per-tenant selection (that is task_12_11). This guards against a
# full restore accidentally being scoped to (and thus silently dropping) a
# subset of tenants.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# prod-04 task_prod_04_03 — preflight de servicios: un fantasma en la lista
# abortaba el restore en el primer paso destructivo (ADR 0117 c).
# --------------------------------------------------------------------------- #


def test_a_service_missing_from_the_compose_aborts_before_anything_destructive(
    tmp_path: Path,
) -> None:
    compose = _write_compose(tmp_path, ("api-server", "minio", "redis", "postgres"))
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner()
    # `workers` NO está declarado → `docker compose stop workers` daría != 0.
    engine = RestoreEngine(_restore_config(tmp_path, compose_file=compose), runner=runner)

    with pytest.raises(RestoreError, match="NO están declarados"):
        engine.run_full_restore(bundle, confirm=bundle.name)

    assert not any(
        c[0] == "docker" for c in runner.calls
    ), "el preflight tiene que abortar ANTES de tocar el stack"
    assert not any(c[0] == "pg_restore" and "--list" not in c for c in runner.calls)


def test_the_preflight_message_names_the_phantom(tmp_path: Path) -> None:
    compose = _write_compose(tmp_path, ("api-server", "minio", "redis"))
    bundle = _build_plaintext_bundle(tmp_path)
    engine = RestoreEngine(_restore_config(tmp_path, compose_file=compose), runner=RestoreRunner())

    with pytest.raises(RestoreError) as exc_info:
        engine.run_full_restore(bundle, confirm=bundle.name)
    assert "'workers'" in str(exc_info.value)


def test_the_preflight_does_not_block_when_the_compose_is_unreachable(tmp_path: Path) -> None:
    """No verificable ≠ inválido: un compose ausente no puede parar un DR.

    Es el único punto donde el guard cede, y a propósito: el guard estático
    (`tests/unit/test_restore_services_exist.py`) cubre el repositorio, y
    `_stop_app_stack` da un mensaje accionable si compose rechaza un nombre.
    """
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner()
    engine = RestoreEngine(
        _restore_config(tmp_path, compose_file=tmp_path / "no-existe.yml"), runner=runner
    )
    result = engine.run_full_restore(bundle, confirm=bundle.name)
    assert result.restored_volumes == ("minio_data", "redis_data")


# --------------------------------------------------------------------------- #
# prod-04 task_prod_04_08 — GRANTs y rol de conexión.
# --------------------------------------------------------------------------- #


def test_grants_are_reapplied_to_the_runtime_role_after_pg_restore(tmp_path: Path) -> None:
    """`--no-owner --no-privileges` tira las ACLs: sin re-conceder, la aplicación
    arranca y falla con «permission denied for table»."""
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner()
    RestoreEngine(_restore_config(tmp_path), runner=runner).run_full_restore(
        bundle, confirm=bundle.name
    )

    psql = [c for c in runner.calls if c[0] == "psql"]
    assert len(psql) == 1, "no se re-concedieron los permisos tras el pg_restore"
    argv = psql[0]
    joined = " ".join(argv)
    assert "--set=ON_ERROR_STOP=1" in argv, "psql enmascararía un GRANT fallido"
    assert f"--dbname={_DB_URL}" in argv
    assert "GRANT USAGE ON SCHEMA public TO app_user" in joined
    assert "ON ALL TABLES IN SCHEMA public TO app_user" in joined
    assert "ON ALL SEQUENCES IN SCHEMA public TO app_user" in joined
    assert "ALTER DEFAULT PRIVILEGES" in joined
    # Y va DESPUÉS del pg_restore (antes no habría tablas que conceder).
    order = [c[0] for c in runner.calls]
    assert order.index("pg_restore") < order.index("psql")


def test_a_failed_grant_step_is_fail_stopped_too(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner(fail_grants=True)
    engine = RestoreEngine(_restore_config(tmp_path), runner=runner)

    with pytest.raises(RestorePartialError) as exc_info:
        engine.run_full_restore(bundle, confirm=bundle.name)
    assert exc_info.value.stage == "database_restored"
    assert not any(c[0] == "docker" and "up" in c for c in runner.calls)


def test_restoring_as_the_wrong_role_is_refused_before_pg_restore(tmp_path: Path) -> None:
    """`pg_restore --clean` deja el ownership en el rol que conecta: hacerlo como
    `app_user` deja el esquema inservible para las migraciones."""
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner()
    cfg = _restore_config(tmp_path)
    bad = RestoreConfig(
        **{
            **{f.name: getattr(cfg, f.name) for f in cfg.__dataclass_fields__.values()},
            "database_url": "postgresql://app_user:x@db:5432/agentic_platform",
        }
    )
    engine = RestoreEngine(bad, runner=runner)

    with pytest.raises(RestorePartialError) as exc_info:
        engine.run_full_restore(bundle, confirm=bundle.name)
    assert "app_user" in str(exc_info.value) and "migrations_user" in str(exc_info.value)
    assert not any(c[0] == "pg_restore" and "--list" not in c for c in runner.calls)


def test_the_role_guard_can_be_disabled_but_is_on_by_default(tmp_path: Path) -> None:
    from workers.config import Settings

    assert Settings().restore_required_db_role == "migrations_user"
    assert Settings().restore_grant_app_role == "app_user"
    assert Settings().restore_autostart_on_failure is False


# --------------------------------------------------------------------------- #
# prod-04 task_prod_04_05 — los repos de proyectos y los binds SÍ se restauran.
# --------------------------------------------------------------------------- #


def _bundle_with_projects_and_bind(tmp_path: Path, bind: Path) -> Path:
    """Un bundle que además trae `projects_tar` y `bind_tar`."""
    projects = bind / "projects"
    projects.mkdir(parents=True)
    (projects / "t1").mkdir()
    runner = BuildRunner()
    cfg = BackupConfig(
        backup_root=tmp_path / "backups",
        database_url=_DB_URL,
        volumes=("minio_data", "redis_data"),
        volumes_mount_root=tmp_path / "volumes",
        retention_days=7,
        bind_paths=(str(bind),),
        projects_root=str(projects),
        transient_excludes=("worktrees",),
    )
    return BackupEngine(cfg, runner=runner, now=_NOW).run_full_backup().bundle_dir


def test_project_repos_and_declared_binds_are_re_extracted(tmp_path: Path) -> None:
    """La regresión de fondo: `bind_tar` se respaldaba, se verificaba… y el
    restore lo ignoraba (filtraba `kind == "volume_tar"`). El código de los
    proyectos no volvía de un DR."""
    bind = tmp_path / "agent-platform"
    bundle = _bundle_with_projects_and_bind(tmp_path, bind)
    projects_root = str(tmp_path / "restored" / "projects")
    runner = RestoreRunner()
    cfg = _restore_config(tmp_path, projects_root=projects_root, bind_paths=(str(bind),))
    result = RestoreEngine(cfg, runner=runner).run_full_restore(bundle, confirm=bundle.name)

    extracts = [c for c in runner.calls if c[0] == "tar" and "--extract" in c]
    targets = [_arg_value(c, "--directory=") for c in extracts]
    assert projects_root in targets, f"los bare repos no se restauraron: {targets}"
    assert str(bind) in targets, f"el bind declarado no se restauró: {targets}"
    assert set(result.restored_paths) == {projects_root, str(bind)}


def test_a_bind_whose_source_is_not_declared_is_skipped_not_extracted(tmp_path: Path) -> None:
    """Extraer en una ruta absoluta que solo aparece en un fichero del bundle no
    es una potestad que el motor deba tener."""
    bind = tmp_path / "agent-platform"
    bundle = _bundle_with_projects_and_bind(tmp_path, bind)
    runner = RestoreRunner()
    cfg = _restore_config(
        tmp_path,
        projects_root=str(tmp_path / "restored" / "projects"),
        bind_paths=(str(tmp_path / "otro-sitio"),),  # NO coincide con el manifest
    )
    result = RestoreEngine(cfg, runner=runner).run_full_restore(bundle, confirm=bundle.name)

    targets = [_arg_value(c, "--directory=") for c in runner.calls if "--extract" in c]
    assert str(bind) not in targets
    assert str(bind) not in result.restored_paths


@pytest.mark.cross_tenant
def test_full_restore_is_platform_global_not_per_tenant(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = RestoreRunner()
    RestoreEngine(_restore_config(tmp_path), runner=runner).run_full_restore(
        bundle, confirm=bundle.name
    )

    pg_calls = [c for c in runner.calls if c[0] == "pg_restore" and "--list" not in c]
    assert len(pg_calls) == 1
    argv = pg_calls[0]
    joined = " ".join(argv)
    # A full restore restores the WHOLE dump: no per-table / per-schema / row
    # selection (those are the per-tenant restore's levers in task_12_11). Its
    # presence here would mean a "full" restore silently scoped to a subset.
    assert "--table" not in joined
    assert "--schema" not in joined
    assert "tenant_id" not in joined
