"""Integration tests for the selective per-tenant restore engine (Plan 12 task_12_11).

Real ``createdb`` / ``pg_restore`` / ``psql`` / live writes CANNOT run in the
test environment, so the external-command seam
(:class:`workers.backup.CommandRunner`) is MOCKED. One fake runner BUILDS a real
bundle on disk via the Phase A :class:`workers.backup.BackupEngine`; another
DRIVES the per-tenant restore — recording the createdb / pg_restore-into-staging
/ per-table count / filtered-copy / drop argv, answering the verifier probes and
the ``SELECT count(*)`` previews, and fabricating the extracted object-store
slice.

The tests assert:

  * CROSS-TENANT SAFETY — restoring tenant A reinstates A's rows and leaves
    tenant B's rows UNTOUCHED: every DELETE + every staging SELECT is scoped by
    ``tenant_id = '<A>'``; B's id never appears in any live-write statement
    (@pytest.mark.cross_tenant).
  * EXACT TABLE SET — the tables copied are exactly the configured tenant-scoped
    set, no more.
  * FK ORDERING — inserts run in FK (parent→child) order; deletes in reverse.
  * PREVIEW (dry-run) — lists the affected tables + row counts and writes NOTHING
    to the live DB (no filtered-copy psql, no object-store extract).
  * VERIFY-BEFORE-RESTORE — a verification failure ABORTS before any staging DB
    is created (fail closed); a tampered encrypted bundle fails closed too.
  * DOUBLE CONFIRMATION — a wrong token refuses before any command.
  * STAGING — the dump is pg_restore'd into a throwaway staging DB that is dropped
    in a finally even when the copy fails.
  * OBJECT STORAGE — only the tenant's ``<tenant_id>/`` slice is extracted.

No real per-tenant restore of a live stack runs here — that is HUMAN test
human_12_03.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workers.backup import BackupConfig, BackupEngine, CommandResult
from workers.backup_encryption import BackupEncryptor
from workers.restore_per_tenant import (
    DEFAULT_TENANT_SCOPED_TABLES,
    PerTenantRestoreConfig,
    PerTenantRestoreEngine,
    PerTenantRestoreError,
    PerTenantRestoreVerificationError,
    confirmation_token,
    run_per_tenant_restore,
)
from workers.secrets import StaticSecretsProvider

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 30, 3, 0, 0, tzinfo=UTC)

_VAULT_KEY_NAME = "backup_encryption_key"
_VAULT_KEY_VALUE = "s3cr3t-vault-backup-key-MUST-NOT-LEAK-0123456789abcdef"
_DB_URL = "postgresql://migrations_user:s3cr3t@db:5432/agentic_platform"

# Two real, distinct UUID tenants. A = the tenant we restore; B = the bystander
# whose rows must NEVER be touched.
_TENANT_A = "11111111-1111-1111-1111-111111111111"
_TENANT_B = "22222222-2222-2222-2222-222222222222"

# A small, ordered tenant-scoped table set for the tests: parents → children.
_TABLES = ("teams", "projects", "plans", "tasks")

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


def _command(argv: list[str]) -> str:
    """The value passed after ``--command`` (the SQL), or '' if none."""
    for i, token in enumerate(argv):
        if token == "--command" and i + 1 < len(argv):
            return argv[i + 1]
    return ""


# --------------------------------------------------------------------------- #
# Bundle builder (reuses the Phase A engine).
# --------------------------------------------------------------------------- #


@dataclass
class BuildRunner:
    """Fabricates a real plaintext/encrypted bundle on disk via the BackupEngine."""

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


# --------------------------------------------------------------------------- #
# Per-tenant restore runner.
# --------------------------------------------------------------------------- #


@dataclass
class PerTenantRunner:
    """Records per-tenant-restore argv + answers probes; tunable failures.

    Answers the verifier probes (``pg_restore --list``, ``tar --list``), the
    per-table ``SELECT count(*)`` previews (a configurable count per table), the
    createdb / pg_restore-into-staging / filtered-copy / drop-staging commands,
    and fabricates the extracted object-store slice.
    """

    counts: dict[str, int] = field(default_factory=dict)
    fail_verify: bool = False
    fail_copy: bool = False
    fail_pg_restore: bool = False
    fail_minio_stop: bool = False
    fail_slice_extract: bool = False
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
        if argv[0] == "pg_restore" and "--list" in argv:
            return self._verify_pg()
        if argv[0] == "tar" and "--list" in argv:
            return self._verify_tar()
        if argv[0] == "tar" and "--extract" in argv:
            return self._extract(argv)
        if argv[0] == "pg_restore":
            return self._pg_restore()
        if argv[0] == "psql":
            return self._psql(argv)
        if argv[0] == "docker":
            # prod-04 task_prod_04_10: MinIO se PARA alrededor de la extracción de
            # la rebanada del tenant (escribir su `_data` en caliente produce
            # objetos que la API no puede leer) y se vuelve a arrancar siempre.
            return CommandResult(returncode=1 if self.fail_minio_stop else 0)
        raise AssertionError(f"unexpected restore command: {argv!r}")

    # -- verifier probes ----------------------------------------------------

    def _verify_pg(self) -> CommandResult:
        if self.fail_verify:
            return CommandResult(returncode=1, stderr="pg_restore: corrupt archive")
        return CommandResult(returncode=0, stdout=_GOOD_TOC)

    def _verify_tar(self) -> CommandResult:
        if self.fail_verify:
            return CommandResult(returncode=2, stderr="tar: Unexpected EOF")
        return CommandResult(returncode=0, stdout="./\n./objects/\n")

    # -- restore commands ---------------------------------------------------

    def _extract(self, argv: list[str]) -> CommandResult:
        directory = Path(_arg_value(argv, "--directory="))
        if "--gzip" in argv:
            if self.fail_slice_extract:
                return CommandResult(returncode=2, stderr="tar: write error")
            # The tenant's object-store slice extracted into the volume _data tree.
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "restored-marker").write_text("x", encoding="utf-8")
        elif self.decrypt_tar:
            _unfold_bundle_tar(Path(_arg_value(argv, "--file=")), directory)
        return CommandResult(returncode=0)

    def _pg_restore(self) -> CommandResult:
        if self.fail_pg_restore:
            return CommandResult(returncode=1, stderr="pg_restore: into staging failed")
        return CommandResult(returncode=0)

    def _psql(self, argv: list[str]) -> CommandResult:
        sql = _command(argv)
        # A per-table count probe (preview).
        m = re.search(r'count\(\*\) FROM "(\w+)"', sql)
        if m and "WHERE" in sql:
            table = m.group(1)
            return CommandResult(returncode=0, stdout=f"{self.counts.get(table, 0)}\n")
        # The big filtered-copy transaction.
        if sql.startswith("BEGIN;") and "INSERT INTO" in sql:
            if self.fail_copy:
                return CommandResult(returncode=1, stderr="psql: copy failed")
            return CommandResult(returncode=0)
        # createdb / dropdb maintenance statements.
        return CommandResult(returncode=0)


def _unfold_bundle_tar(archive: Path, directory: Path) -> None:
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


def _restore_config(tmp_path: Path, *, encryption_enabled: bool = False) -> PerTenantRestoreConfig:
    return PerTenantRestoreConfig(
        backup_root=tmp_path / "backups",
        admin_database_url=_DB_URL,
        tenant_scoped_tables=_TABLES,
        object_store_volume="minio_data",
        volumes_mount_root=tmp_path / "restore-volumes",
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


def _copy_script(runner: PerTenantRunner) -> str:
    """The big filtered-copy transaction SQL the engine ran (asserts there is one)."""
    scripts = [
        _command(c)
        for c in runner.calls
        if c[0] == "psql" and _command(c).startswith("BEGIN;") and "INSERT INTO" in _command(c)
    ]
    assert len(scripts) == 1, f"expected exactly one filtered-copy script, got {len(scripts)}"
    return scripts[0]


# --------------------------------------------------------------------------- #
# Cross-tenant safety — the headline guarantee.
# --------------------------------------------------------------------------- #


@pytest.mark.cross_tenant
def test_restoring_tenant_a_never_touches_tenant_b(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner(counts={"teams": 1, "projects": 2, "plans": 2, "tasks": 5})
    engine = PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner)

    result = engine.run(
        bundle,
        tenant_id=_TENANT_A,
        confirm=confirmation_token(_TENANT_A, bundle.name),
    )

    assert result.tenant_id == _TENANT_A
    assert result.dry_run is False

    script = _copy_script(runner)
    # EVERY write statement is scoped to tenant A.
    delete_and_select = [
        line for line in script.splitlines() if line.startswith("DELETE FROM") or "dblink(" in line
    ]
    assert delete_and_select  # there is real work
    for line in delete_and_select:
        assert _TENANT_A in line, f"statement not scoped to tenant A: {line!r}"
        # Tenant B's id must NEVER appear in a statement that writes the live DB.
        assert _TENANT_B not in line, f"tenant B leaked into a live-write statement: {line!r}"
    # And B never appears anywhere in the whole copy transaction.
    assert _TENANT_B not in script


@pytest.mark.cross_tenant
def test_every_delete_and_remote_select_is_tenant_scoped(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner(counts={t: 1 for t in _TABLES})
    PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner).run(
        bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name)
    )

    script = _copy_script(runner)
    predicate = f"tenant_id = '{_TENANT_A}'"
    # One DELETE per table, each tenant-scoped.
    for table in _TABLES:
        assert f'DELETE FROM "{table}" WHERE {predicate};' in script
    # Each dblink remote SELECT is tenant-scoped (single-quotes doubled inside the
    # dblink SQL literal).
    doubled = f"tenant_id = ''{_TENANT_A}''"
    for table in _TABLES:
        assert f'SELECT * FROM "{table}" WHERE {doubled}' in script


# --------------------------------------------------------------------------- #
# Exact table set + FK ordering.
# --------------------------------------------------------------------------- #


def test_restored_table_set_is_exactly_the_tenant_scoped_tables(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner(counts={t: 1 for t in _TABLES})
    result = PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner).run(
        bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name)
    )

    assert result.restored_tables == _TABLES

    script = _copy_script(runner)
    inserted = re.findall(r'INSERT INTO "(\w+)"', script)
    # Exactly the configured set — no extra tables, no missing ones.
    assert set(inserted) == set(_TABLES)
    assert len(inserted) == len(_TABLES)


def test_inserts_in_fk_order_deletes_in_reverse(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner(counts={t: 1 for t in _TABLES})
    PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner).run(
        bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name)
    )

    script = _copy_script(runner)
    inserted = re.findall(r'INSERT INTO "(\w+)"', script)
    deleted = re.findall(r'DELETE FROM "(\w+)"', script)

    # Parents are inserted before children (FK order).
    assert inserted == list(_TABLES)
    # Children are deleted before parents (reverse FK order).
    assert deleted == list(reversed(_TABLES))
    # Every DELETE precedes every INSERT (clear the tenant, then re-load).
    first_insert = script.index("INSERT INTO")
    last_delete = script.rindex("DELETE FROM")
    assert last_delete < first_insert


# --------------------------------------------------------------------------- #
# Preview / dry-run — lists tables + row counts, writes NOTHING.
# --------------------------------------------------------------------------- #


def test_preview_lists_affected_tables_and_row_counts_without_writing(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    counts = {"teams": 1, "projects": 3, "plans": 0, "tasks": 7}
    runner = PerTenantRunner(counts=counts)
    engine = PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner)

    preview = engine.preview(bundle, tenant_id=_TENANT_A)

    # Every tenant-scoped table appears, in FK order, with its staged row count.
    assert [t.table for t in preview.tables] == list(_TABLES)
    assert {t.table: t.row_count for t in preview.tables} == counts
    assert preview.total_rows == 11
    # affected_tables excludes the empty one.
    assert preview.affected_tables == ("teams", "projects", "tasks")

    # NOTHING was written to the live DB: no filtered-copy transaction, no
    # object-store extract.
    assert not any(
        c[0] == "psql" and _command(c).startswith("BEGIN;") and "INSERT INTO" in _command(c)
        for c in runner.calls
    )
    assert not any(c[0] == "tar" and "--extract" in c and "--gzip" in c for c in runner.calls)


def test_dry_run_run_returns_preview_and_writes_nothing(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner(counts={t: 2 for t in _TABLES})
    engine = PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner)

    result = engine.run(
        bundle,
        tenant_id=_TENANT_A,
        confirm=confirmation_token(_TENANT_A, bundle.name),
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.restored_tables == ()
    assert result.object_store_restored is False
    assert result.preview.total_rows == 8
    # Staging was still created (to count), but no live copy ran.
    assert any(c[0] == "pg_restore" and "--list" not in c for c in runner.calls)
    assert not any(
        c[0] == "psql" and _command(c).startswith("BEGIN;") and "INSERT INTO" in _command(c)
        for c in runner.calls
    )


# --------------------------------------------------------------------------- #
# Verify-before-restore, fail closed.
# --------------------------------------------------------------------------- #


def test_verification_failure_aborts_before_any_staging(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner(fail_verify=True)
    engine = PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner)

    with pytest.raises(PerTenantRestoreVerificationError) as exc_info:
        engine.run(bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name))

    assert exc_info.value.report.valid is False
    # NOTHING destructive ran: no staging createdb, no pg_restore, no copy.
    assert not any(c[0] == "pg_restore" and "--list" not in c for c in runner.calls)
    assert not any(c[0] == "psql" and "CREATE DATABASE" in _command(c) for c in runner.calls)


def test_checksum_mismatch_aborts_fail_closed(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    # Corrupt a volume archive AFTER the manifest captured its checksum.
    (bundle / "minio_data.tar.gz").write_bytes(b"tampered bytes not in the manifest")
    runner = PerTenantRunner(counts={t: 1 for t in _TABLES})
    engine = PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner)

    with pytest.raises(PerTenantRestoreVerificationError):
        engine.run(bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name))

    assert not any(c[0] == "pg_restore" and "--list" not in c for c in runner.calls)


def test_tampered_encrypted_bundle_fails_closed(tmp_path: Path) -> None:
    bundle = _build_encrypted_bundle(tmp_path)
    blob = bundle / "bundle.tar.enc"
    data = bytearray(blob.read_bytes())
    data[-1] ^= 0x01
    blob.write_bytes(bytes(data))

    runner = PerTenantRunner(decrypt_tar=True)
    engine = PerTenantRestoreEngine(
        _restore_config(tmp_path, encryption_enabled=True),
        runner=runner,
        encryptor=_encryptor(),
    )

    with pytest.raises(PerTenantRestoreError, match="failed to decrypt backup bundle"):
        engine.run(bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name))

    assert not any(c[0] == "pg_restore" and "--list" not in c for c in runner.calls)


# --------------------------------------------------------------------------- #
# Double confirmation + validation.
# --------------------------------------------------------------------------- #


def test_wrong_confirm_token_refuses_before_any_command(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner()
    engine = PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner)

    with pytest.raises(PerTenantRestoreError, match="confirmation token does not match"):
        engine.run(bundle, tenant_id=_TENANT_A, confirm="wrong-token")

    assert runner.calls == []


def test_confirm_token_for_other_tenant_refuses(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner()
    engine = PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner)

    # A token minted for tenant B must not authorise restoring tenant A.
    with pytest.raises(PerTenantRestoreError, match="confirmation token does not match"):
        engine.run(bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_B, bundle.name))
    assert runner.calls == []


def test_non_uuid_tenant_id_refused(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner()
    engine = PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner)

    with pytest.raises(PerTenantRestoreError, match="is not a UUID"):
        engine.run(bundle, tenant_id="not-a-uuid", confirm="anything")
    assert runner.calls == []


def test_missing_bundle_raises(tmp_path: Path) -> None:
    runner = PerTenantRunner()
    engine = PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner)

    with pytest.raises(PerTenantRestoreError, match="backup bundle not found"):
        engine.run(
            "20990101T000000Z",
            tenant_id=_TENANT_A,
            confirm=confirmation_token(_TENANT_A, "20990101T000000Z"),
        )
    assert runner.calls == []


def test_unsafe_table_name_rejected_at_config_time() -> None:
    with pytest.raises(PerTenantRestoreError, match="unsafe tenant-scoped table name"):
        PerTenantRestoreConfig(
            backup_root=Path("/tmp/x"),
            admin_database_url=_DB_URL,
            tenant_scoped_tables=("tasks; DROP TABLE users;--",),
        )


# --------------------------------------------------------------------------- #
# Staging DB lifecycle.
# --------------------------------------------------------------------------- #


def test_dump_restored_into_staging_db_not_live(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner(counts={t: 1 for t in _TABLES})
    PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner).run(
        bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name)
    )

    pg_restores = [c for c in runner.calls if c[0] == "pg_restore" and "--list" not in c]
    assert len(pg_restores) == 1
    argv = pg_restores[0]
    # No --clean / --if-exists: staging is a fresh empty DB, never the live one.
    assert "--clean" not in argv
    assert "--if-exists" not in argv
    # The restore target is the staging DB, not the live `agentic_platform`.
    dbname = _arg_value(argv, "--dbname=")
    assert "restore_staging" in dbname
    assert dbname.rsplit("/", 1)[-1] != "agentic_platform"
    # The dump source is the bundle's LOGICAL directory dump.
    assert str(bundle / "postgres") in argv


def test_staging_db_dropped_even_when_copy_fails(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner(counts={t: 1 for t in _TABLES}, fail_copy=True)
    engine = PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner)

    with pytest.raises(PerTenantRestoreError, match="filtered per-tenant copy"):
        engine.run(bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name))

    # The throwaway staging DB is dropped in the finally even though the copy failed.
    drops = [c for c in runner.calls if c[0] == "psql" and "DROP DATABASE" in _command(c)]
    assert drops, "staging DB was never dropped after a failed copy"
    assert any("restore_staging" in _command(c) for c in drops)


def test_staging_createdb_uses_maintenance_db(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner(counts={t: 1 for t in _TABLES})
    PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner).run(
        bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name)
    )

    creates = [c for c in runner.calls if c[0] == "psql" and "CREATE DATABASE" in _command(c)]
    assert len(creates) == 1
    # CREATE DATABASE cannot run while connected to the target DB → it connects to
    # the `postgres` maintenance DB.
    dbname = _arg_value(creates[0], "--dbname=")
    assert dbname.rsplit("/", 1)[-1] == "postgres"


# --------------------------------------------------------------------------- #
# Object storage — only the tenant's slice.
# --------------------------------------------------------------------------- #


def test_only_tenant_object_store_slice_extracted(tmp_path: Path) -> None:
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner(counts={t: 1 for t in _TABLES})
    cfg = _restore_config(tmp_path)
    result = PerTenantRestoreEngine(cfg, runner=runner).run(
        bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name)
    )

    assert result.object_store_restored is True
    extracts = [c for c in runner.calls if c[0] == "tar" and "--extract" in c and "--gzip" in c]
    assert len(extracts) == 1
    argv = extracts[0]
    # Extracts into the minio volume _data tree...
    assert _arg_value(argv, "--directory=") == str(cfg.volumes_mount_root / "minio_data" / "_data")
    # ...and ONLY the tenant's prefix member — never the whole archive.
    members = [t for t in argv if not t.startswith("--") and t != "tar"]
    assert members == [f"./{_TENANT_A}"]
    # Tenant B's prefix is never an extract target.
    assert f"./{_TENANT_B}" not in argv


def test_minio_is_stopped_around_the_extraction_and_started_again(tmp_path: Path) -> None:
    """prod-04 task_prod_04_10 (hallazgo gap3-6).

    MinIO no soporta que le escriban el `_data` por debajo mientras corre: el
    formato xl guarda metadatos por objeto y el servidor cachea, así que una
    extracción en caliente deja objetos que el filesystem tiene y la API no ve.
    Hasta prod-04 el restore por tenant hacía exactamente eso.
    """
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner(counts={t: 1 for t in _TABLES})
    PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner).run(
        bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name)
    )

    verbs = [
        ("stop" if "stop" in c else "start") if c[0] == "docker" else "extract"
        for c in runner.calls
        if c[0] == "docker" or (c[0] == "tar" and "--extract" in c and "--gzip" in c)
    ]
    assert verbs == [
        "stop",
        "extract",
        "start",
    ], f"la extracción no quedó encerrada entre el stop y el start de MinIO: {verbs}"
    stop = next(c for c in runner.calls if c[0] == "docker" and "stop" in c)
    assert stop[-1] == "minio"
    assert "--project-name" in stop and "--file" in stop


def test_minio_is_restarted_even_when_the_extraction_fails(tmp_path: Path) -> None:
    """Dejar MinIO caído por el restore de UN tenant deja sin object storage a
    TODOS los demás: peor que el problema que se estaba resolviendo."""
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner(counts={t: 1 for t in _TABLES}, fail_slice_extract=True)
    with pytest.raises(PerTenantRestoreError):
        PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner).run(
            bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name)
        )
    assert any(
        c[0] == "docker" and "start" in c for c in runner.calls
    ), "MinIO se quedó parado tras un fallo de extracción"


def test_a_failed_stop_aborts_before_touching_the_volume(tmp_path: Path) -> None:
    """Si no se puede parar MinIO, NO se escribe su `_data`: fail-closed."""
    bundle = _build_plaintext_bundle(tmp_path)
    runner = PerTenantRunner(counts={t: 1 for t in _TABLES}, fail_minio_stop=True)
    with pytest.raises(PerTenantRestoreError, match="no se pudo parar"):
        PerTenantRestoreEngine(_restore_config(tmp_path), runner=runner).run(
            bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name)
        )
    assert not [
        c for c in runner.calls if c[0] == "tar" and "--extract" in c and "--gzip" in c
    ], "se extrajo la rebanada con MinIO vivo"


def test_a_failed_wipe_is_a_hard_error_not_best_effort(tmp_path: Path) -> None:
    """Era `shutil.rmtree(..., ignore_errors=True)`: un borrado a medias dejaba
    al tenant con una mezcla de dos momentos distintos, y nadie se enteraba."""
    import workers.restore_per_tenant as rpt

    bundle = _build_plaintext_bundle(tmp_path)
    cfg = _restore_config(tmp_path)
    tenant_dir = cfg.volumes_mount_root / "minio_data" / "_data" / _TENANT_A
    tenant_dir.mkdir(parents=True, exist_ok=True)
    (tenant_dir / "objeto.bin").write_bytes(b"x")

    def _boom(path, *a, **k):  # type: ignore[no-untyped-def]
        raise OSError(13, "Permission denied")

    runner = PerTenantRunner(counts={t: 1 for t in _TABLES})
    original = rpt.shutil.rmtree
    rpt.shutil.rmtree = _boom  # type: ignore[assignment]
    try:
        with pytest.raises(PerTenantRestoreError, match="no se pudo vaciar la rebanada"):
            PerTenantRestoreEngine(cfg, runner=runner).run(
                bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name)
            )
    finally:
        rpt.shutil.rmtree = original  # type: ignore[assignment]

    assert not [c for c in runner.calls if c[0] == "tar" and "--extract" in c and "--gzip" in c]
    # Y MinIO vuelve: el fallo del wipe no puede dejar el object storage caído.
    assert any(c[0] == "docker" and "start" in c for c in runner.calls)


# --------------------------------------------------------------------------- #
# Encrypted bundle: decrypt → verify → restore.
# --------------------------------------------------------------------------- #


def test_encrypted_bundle_decrypted_then_verified_then_restored(tmp_path: Path) -> None:
    bundle = _build_encrypted_bundle(tmp_path)
    assert (bundle / "bundle.tar.enc").is_file()
    assert not (bundle / "postgres").exists()

    runner = PerTenantRunner(counts={t: 1 for t in _TABLES}, decrypt_tar=True)
    engine = PerTenantRestoreEngine(
        _restore_config(tmp_path, encryption_enabled=True),
        runner=runner,
        encryptor=_encryptor(),
    )

    result = engine.run(
        bundle, tenant_id=_TENANT_A, confirm=confirmation_token(_TENANT_A, bundle.name)
    )

    assert result.encrypted is True
    # Decryption restored the plaintext layout BEFORE the verifier / staging ran.
    assert (bundle / "postgres").exists()
    assert any(c[0] == "pg_restore" and "--list" not in c for c in runner.calls)


# --------------------------------------------------------------------------- #
# Entrypoint + defaults.
# --------------------------------------------------------------------------- #


def test_run_per_tenant_restore_entrypoint_builds_engine_from_settings(tmp_path: Path) -> None:
    from workers.config import Settings

    bundle = _build_plaintext_bundle(tmp_path)
    settings = Settings(
        backup_root=str(tmp_path / "backups"),
        backup_database_url=_DB_URL,
        backup_volumes=["minio_data", "redis_data"],
        backup_volumes_mount_root=str(tmp_path / "restore-volumes"),
        restore_tenant_scoped_tables=list(_TABLES),
        restore_object_store_volume="minio_data",
    )
    runner = PerTenantRunner(counts={t: 1 for t in _TABLES})

    result = run_per_tenant_restore(
        bundle.name,
        tenant_id=_TENANT_A,
        confirm=confirmation_token(_TENANT_A, bundle.name),
        settings=settings,
        runner=runner,
    )

    assert result.tenant_id == _TENANT_A
    assert result.restored_tables == _TABLES


def test_default_table_set_falls_back_when_settings_empty(tmp_path: Path) -> None:
    from workers.config import Settings

    settings = Settings(
        backup_root=str(tmp_path / "backups"),
        backup_database_url=_DB_URL,
        restore_tenant_scoped_tables=[],  # empty → built-in default
    )
    cfg = PerTenantRestoreConfig.from_settings(settings)
    assert cfg.tenant_scoped_tables == DEFAULT_TENANT_SCOPED_TABLES
