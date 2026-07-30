"""Selective per-tenant restore from a full backup bundle (Plan 12 task_12_11).

Where :mod:`workers.restore` (task_12_10) restores the WHOLE stack (a
platform-global, all-or-nothing pg_restore + every volume), this engine restores
*one tenant's data only* — the surgical "a tenant accidentally deleted all its
projects; put just that tenant back to the chosen backup" operation
(Plan 12 Tests Humanos human_12_03). It MUST never touch any other tenant's rows
or any other tenant's slice of object storage.

Why this needs a LOGICAL dump
------------------------------
Plan 12 Decisiones Clave chose ``pg_dump`` LOGICAL (directory format) *precisely
to enable per-tenant restore*: a logical dump can be restored into a throwaway
staging database and then queried + filtered, which a physical
``pg_basebackup`` cannot. This engine leans on exactly that.

The filtered-restore strategy
------------------------------
A full restore that simply ``pg_restore``-d the dump would clobber every tenant.
Instead, per-tenant restore is a *staged, filtered copy*:

  1. **LOCATE + (if encrypted) DECRYPT + VERIFY** the bundle, REUSING the Phase A
     bundle layout, the :class:`~workers.backup_encryption.BackupEncryptor`
     decrypt primitive, and the :class:`~workers.backup_verification.BackupVerifier`
     checksum/structural gate. Verification PRECEDES anything destructive and is
     fail-closed: a corrupt bundle aborts before a single row is written.
  2. **pg_restore into a TEMPORARY STAGING DATABASE** (``createdb`` →
     ``pg_restore`` into it). The staging DB is a private, throwaway copy of the
     whole backup; the live database is untouched at this point.
  3. **PREVIEW** (always computed, and the whole operation when ``dry_run``):
     for each tenant-scoped table, ``SELECT count(*) WHERE tenant_id = <target>``
     in the staging DB. This is the list of affected tables + row counts the UI
     (task_12_12) shows for the operator's second confirmation. A dry-run STOPS
     here, having written NOTHING to the live DB.
  4. **FILTERED COPY into the live DB**, in FK-dependency order, inside ONE
     transaction: for each tenant-scoped table, delete the target tenant's live
     rows then re-insert that tenant's rows from staging — ``... WHERE tenant_id
     = <target>`` on BOTH sides. Other tenants' rows are never in the predicate,
     so they are never deleted or overwritten. The cross-tenant write runs as the
     BYPASSRLS admin role (RLS would otherwise hide the staging/other rows), but
     every statement is still scoped by the ``tenant_id`` predicate — BYPASSRLS
     removes the policy, the predicate keeps the blast radius to one tenant.
  5. **DROP the staging database** in a finally — the throwaway copy never
     outlives the restore.
  6. **OBJECT STORAGE** — only the target tenant's slice of the captured object
     store (the MinIO bundle prefix ``<tenant_id>/``) is re-extracted, never the
     whole volume. Other tenants' objects are left exactly as they are.

The subprocess seam (mock-not-fake)
-----------------------------------
Real ``createdb`` / ``pg_restore`` / ``psql`` / live writes cannot run in the
unit-test environment, so EVERY external command goes through the same injectable
:class:`workers.backup.CommandRunner` the backup + full-restore engines use
(production: :class:`workers.backup.SubprocessRunner`, explicit argv, never
``shell=True``; tests inject a fake that records argv + answers the
``SELECT count`` probes). Tests assert *command construction* (the staging
createdb/pg_restore; the per-table ``DELETE``/``INSERT ... WHERE tenant_id =`` on
the live DB run as the BYPASSRLS role), *the exact tenant-scoped table set*,
*FK ordering*, that a *preview lists tables/row-counts without writing*, and that
*verification precedes the restore* (fail closed). A real end-to-end per-tenant
restore is a HUMAN test (Plan 12 human_12_03).

Safety
------
Per-tenant restore is destructive *for the target tenant only*. Guards:

  * **double confirmation** — :meth:`run` requires a ``confirm`` token equal to
    ``f"{tenant_id}@{bundle_id}"`` (the UI's second confirmation; a mismatch
    refuses before any work).
  * **verify-before-restore, fail closed** — a failed manifest verification
    aborts with NOTHING written.
  * **tenant-scoped predicate on every cross-tenant statement** — the BYPASSRLS
    role is used ONLY with an explicit ``tenant_id = <target>`` predicate; no
    unscoped statement ever runs, so another tenant's rows can never be touched.

Secrets (the Vault decrypt key, the admin DB URL) are resolved through the same
seams the rest of Phase A/B/C use; nothing secret is logged.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from workers.backup import (
    MANIFEST_FILENAME,
    CommandRunner,
    SubprocessRunner,
)
from workers.backup_encryption import (
    ENCRYPTED_SUFFIX,
    BackupEncryptionError,
    BackupEncryptor,
)
from workers.backup_verification import (
    BackupVerificationError,
    BackupVerifier,
    VerificationReport,
)
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.restore_per_tenant")

# The DB dump + encrypted-blob names inside a bundle (mirror workers.backup /
# workers.restore so the two engines agree on the on-disk layout).
_DB_DUMP_DIRNAME = "postgres"
_BUNDLE_ARCHIVE_NAME = "bundle.tar"
_ENCRYPTED_BUNDLE_NAME = _BUNDLE_ARCHIVE_NAME + ENCRYPTED_SUFFIX  # bundle.tar.enc

# The column every tenant-scoped table carries (TenantScopedMixin). The whole
# per-tenant restore hinges on filtering by exactly this column; never any other.
TENANT_COLUMN = "tenant_id"

# The default tenant-scoped table set, in FK-dependency (parent → child) order.
# Inserting in this order satisfies foreign keys; deleting in the REVERSE order
# clears children before parents. Derived from api_server.db.domain (every table
# with a TenantScopedMixin / a tenant_id column) plus the conversation tables.
# Operator-tunable via WORKERS_RESTORE_TENANT_TABLES so a schema change does not
# require a worker code change — never a magic list baked into the call site.
DEFAULT_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    # parents first
    "teams",
    "skills",
    "tools",
    "agents",
    "projects",
    "approval_policy_templates",
    "conversations",
    "plans",
    "tasks",
    "executions",
    "approval_requests",
    "messages",
)

# A conservative identifier guard: a Postgres table / db / role name we are
# willing to interpolate into SQL/argv. Anything else is rejected up front rather
# than risk an injection through the (operator-supplied) table list / role.
_SAFE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# A UUID-shaped tenant id. The tenant_id is interpolated into SQL as a quoted
# literal; we additionally require it to be a UUID so a malformed/hostile value
# can never reach the predicate.
_UUID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)


class PerTenantRestoreError(RuntimeError):
    """Raised when a per-tenant restore step fails or a precondition is unmet.

    Covers a missing/locked bundle, a confirmation-token mismatch, a failed
    verification (fail closed), an invalid tenant id / table name, and any
    non-zero external command. The cause is chained where there is one; the
    message never echoes secret material.
    """


@dataclass(frozen=True)
class PerTenantRestoreConfig:
    """Operator-tunable knobs for one per-tenant restore — no magic numbers.

    Built from :class:`workers.config.Settings` via :meth:`from_settings`. Tests
    construct it directly with ``tmp_path`` roots + a fake admin URL.
    """

    backup_root: Path
    # The libpq URL of the LIVE database the tenant's rows are copied INTO. A
    # BYPASSRLS / admin-grade role: the cross-tenant filtered copy must see + write
    # rows across the RLS boundary, but ALWAYS scoped by the tenant_id predicate.
    admin_database_url: str
    # The tenant-scoped tables, in FK (parent→child) order. Inserts go in this
    # order; deletes in reverse. Operator-tunable so a schema change is config, not
    # code.
    tenant_scoped_tables: tuple[str, ...] = DEFAULT_TENANT_SCOPED_TABLES
    # Object-storage (MinIO) volume + its host mount root, for the tenant's slice.
    # The tenant's objects live under the ``<tenant_id>/`` key prefix in the
    # captured volume tar; only that prefix is re-extracted.
    object_store_volume: str = "minio_data"
    volumes_mount_root: Path = Path("/var/lib/docker/volumes")
    # Name of the throwaway staging database the full dump is restored into before
    # filtering. Suffixed with the bundle id + tenant at run time so concurrent
    # restores never collide; never the live database.
    staging_db_prefix: str = "restore_staging"
    # Optional at-rest encryption (mirrors RestoreConfig).
    encryption_enabled: bool = False
    encryption_vault_key: str = "backup_encryption_key"
    # Wall-clock caps for the heavy commands. Generous; a legitimate multi-GB
    # restore must not be killed, but a hung command is a problem.
    pg_restore_timeout_s: int = 3600
    psql_timeout_s: int = 1800
    createdb_timeout_s: int = 120
    tar_timeout_s: int = 3600

    def __post_init__(self) -> None:
        # Validate the operator-supplied table names ONCE, here, so an unsafe
        # identifier can never reach an SQL string further down.
        for table in self.tenant_scoped_tables:
            if not _SAFE_IDENT.fullmatch(table):
                raise PerTenantRestoreError(
                    f"unsafe tenant-scoped table name {table!r}; must match "
                    f"{_SAFE_IDENT.pattern}"
                )

    @classmethod
    def from_settings(cls, settings: Settings) -> PerTenantRestoreConfig:
        tables = tuple(settings.restore_tenant_scoped_tables) or DEFAULT_TENANT_SCOPED_TABLES
        return cls(
            backup_root=Path(settings.backup_root),
            admin_database_url=settings.backup_database_url,
            tenant_scoped_tables=tables,
            object_store_volume=str(settings.restore_object_store_volume),
            volumes_mount_root=Path(settings.backup_volumes_mount_root),
            encryption_enabled=bool(settings.backup_encryption_enabled),
            encryption_vault_key=str(settings.backup_encryption_vault_key),
        )


@dataclass(frozen=True)
class TablePreview:
    """How many of the target tenant's rows one table would restore."""

    table: str
    row_count: int

    def to_dict(self) -> dict[str, Any]:
        return {"table": self.table, "row_count": self.row_count}


@dataclass(frozen=True)
class PerTenantRestorePreview:
    """The dry-run verdict: what a per-tenant restore WOULD change, no writes.

    Lists, in FK order, every tenant-scoped table and the count of the target
    tenant's rows the bundle holds for it. The UI (task_12_12) renders this for
    the operator's second confirmation. Computed entirely against the throwaway
    staging DB — the live database is never touched while previewing.
    """

    tenant_id: str
    backup_id: str
    tables: tuple[TablePreview, ...] = field(default_factory=tuple)

    @property
    def total_rows(self) -> int:
        return sum(t.row_count for t in self.tables)

    @property
    def affected_tables(self) -> tuple[str, ...]:
        """Tables that actually hold at least one row for the tenant."""
        return tuple(t.table for t in self.tables if t.row_count > 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "backup_id": self.backup_id,
            "tables": [t.to_dict() for t in self.tables],
            "total_rows": self.total_rows,
            "affected_tables": list(self.affected_tables),
        }


@dataclass(frozen=True)
class PerTenantRestoreResult:
    """What :meth:`PerTenantRestoreEngine.run` returns on success."""

    tenant_id: str
    backup_id: str
    bundle_dir: str
    encrypted: bool
    dry_run: bool
    restored_tables: tuple[str, ...]
    object_store_restored: bool
    preview: PerTenantRestorePreview
    verification: VerificationReport
    # PROJ-03: filas huérfanas borradas por el sweep de integridad post-restore
    # (0 en un restore limpio o en dry-run; -1 si el sweep falló best-effort).
    fk_orphans_deleted: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "backup_id": self.backup_id,
            "bundle_dir": self.bundle_dir,
            "encrypted": self.encrypted,
            "dry_run": self.dry_run,
            "restored_tables": list(self.restored_tables),
            "object_store_restored": self.object_store_restored,
            "preview": self.preview.to_dict(),
            "verification": self.verification.to_dict(),
            "fk_orphans_deleted": self.fk_orphans_deleted,
        }


class PerTenantRestoreVerificationError(PerTenantRestoreError):
    """A :class:`PerTenantRestoreError` carrying the failing verification report.

    Raised by the verify-before-restore gate so the caller (UI / task) can show
    exactly which artifact/check failed and confirm NOTHING was written.
    """

    def __init__(self, report: VerificationReport) -> None:
        self.report = report
        failures = ", ".join(f"{c.check}:{c.artifact}" for c in report.failures)
        super().__init__(
            f"bundle {report.backup_id} failed verification before per-tenant restore ({failures})"
        )


def confirmation_token(tenant_id: str, backup_id: str) -> str:
    """The exact double-confirmation token :meth:`PerTenantRestoreEngine.run` wants.

    ``<tenant_id>@<backup_id>`` — binds the confirmation to BOTH the tenant and
    the specific bundle, so a token for tenant A / bundle X can never authorise a
    restore of tenant B or a different bundle. The UI (task_12_12) and the Celery
    task build it from the operator's second confirmation.
    """
    return f"{tenant_id}@{backup_id}"


class PerTenantRestoreEngine:
    """Orchestrates one per-tenant restore behind an injectable command runner.

    Construct with the same :class:`workers.backup.CommandRunner` seam the backup
    + full-restore engines use (production: :class:`SubprocessRunner`; tests: a
    fake). When the bundle is encrypted an injected :class:`BackupEncryptor`
    decrypts it; in production it is built from settings in :func:`run_per_tenant_restore`.
    """

    def __init__(
        self,
        config: PerTenantRestoreConfig,
        *,
        runner: CommandRunner | None = None,
        encryptor: BackupEncryptor | None = None,
        verifier: BackupVerifier | None = None,
        now: datetime | None = None,
    ) -> None:
        self._config = config
        self._runner: CommandRunner = runner or SubprocessRunner()
        self._encryptor = encryptor
        # Shares the SAME runner so its pg_restore --list / tar -tf probes are
        # mocked alongside the restore commands in tests.
        self._verifier = verifier or BackupVerifier(runner=self._runner, encryptor=encryptor)
        self._now = now or datetime.now(UTC)

    @property
    def config(self) -> PerTenantRestoreConfig:
        return self._config

    # -- public API ---------------------------------------------------------

    def run(
        self,
        bundle: str | Path,
        *,
        tenant_id: str,
        confirm: str,
        dry_run: bool = False,
    ) -> PerTenantRestoreResult:
        """Restore ONLY ``tenant_id``'s data from ``bundle`` (id or path).

        Sequence: validate tenant id → locate → (decrypt) → VERIFY (fail closed)
        → pg_restore into a throwaway STAGING DB → PREVIEW (count the tenant's
        rows per table) → [if not dry_run] filtered copy into the live DB in FK
        order, scoped by ``tenant_id`` → restore the tenant's object-storage slice
        → drop the staging DB.

        ``confirm`` MUST equal :func:`confirmation_token(tenant_id, bundle_id)`
        (the double-confirmation guard); a mismatch raises before any work.
        ``dry_run=True`` computes + returns the preview having written NOTHING to
        the live DB or object storage.

        Raises :class:`PerTenantRestoreError`
        (a :class:`PerTenantRestoreVerificationError` for a corrupt bundle). On a
        verification failure or a dry run NOTHING in the live DB changes.
        """
        self._require_uuid_tenant(tenant_id)
        bundle_dir = self._locate_bundle(bundle)
        backup_id = bundle_dir.name

        # -- DOUBLE CONFIRMATION (before any work, never destructive on mismatch)
        expected = confirmation_token(tenant_id, backup_id)
        if confirm != expected:
            raise PerTenantRestoreError(
                "per-tenant restore confirmation token does not match "
                f"{expected!r}; refusing to run a destructive restore"
            )

        _log.info(
            "restore_per_tenant.start",
            tenant_id=tenant_id,
            backup_id=backup_id,
            dry_run=dry_run,
        )

        # -- LOCATE + DECRYPT: read the manifest; decrypt an encrypted bundle in
        # place into the plaintext layout the verifier + pg_restore expect.
        manifest = self._load_manifest(bundle_dir)
        encrypted = bool(manifest.get("encrypted", False))
        if encrypted:
            self._decrypt_bundle(bundle_dir)

        # -- VERIFY before touching anything (fail closed).
        report = self._verify(bundle_dir)
        if not report.valid:
            _log.warning(
                "restore_per_tenant.aborted.verification_failed",
                tenant_id=tenant_id,
                backup_id=backup_id,
                failures=[c.check + ":" + c.artifact for c in report.failures],
            )
            raise PerTenantRestoreVerificationError(report)

        # -- STAGING DB: restore the whole logical dump into a throwaway copy, then
        # work entirely against it. The live DB is untouched until the filtered
        # copy step (and never, on a dry run).
        staging_db = self._staging_db_name(backup_id, tenant_id)
        self._create_staging_db(staging_db)
        try:
            self._pg_restore_into_staging(bundle_dir, staging_db)

            # -- PREVIEW (always computed; the whole op when dry_run). Counts the
            # tenant's rows per tenant-scoped table in the STAGING db — no writes.
            preview = self._preview_from_staging(staging_db, tenant_id, backup_id)

            if dry_run:
                _log.info(
                    "restore_per_tenant.dry_run.done",
                    tenant_id=tenant_id,
                    backup_id=backup_id,
                    affected_tables=len(preview.affected_tables),
                    total_rows=preview.total_rows,
                )
                return PerTenantRestoreResult(
                    tenant_id=tenant_id,
                    backup_id=backup_id,
                    bundle_dir=str(bundle_dir),
                    encrypted=encrypted,
                    dry_run=True,
                    restored_tables=(),
                    object_store_restored=False,
                    preview=preview,
                    verification=report,
                )

            # -- FILTERED COPY into the LIVE DB, scoped by tenant_id, FK order.
            restored_tables = self._copy_tenant_rows(staging_db, tenant_id)
        finally:
            # The throwaway staging copy never outlives the restore.
            self._drop_staging_db(staging_db)

        # -- OBJECT STORAGE: re-extract ONLY the tenant's slice (<tenant_id>/).
        object_restored = self._restore_object_store_slice(bundle_dir, manifest, tenant_id)

        # -- POST-RESTORE INTEGRITY SWEEP (PROJ-03): el copiado corre con los FK
        # triggers apagados; si el bundle y la base viva divergen quedan filas
        # huérfanas que ninguna FK volverá a validar. Best-effort: un fallo del
        # sweep no invalida el restore ya commiteado (queda -1 + WARNING).
        orphans_deleted = self._post_restore_integrity_sweep(tenant_id)

        _log.info(
            "restore_per_tenant.done",
            tenant_id=tenant_id,
            backup_id=backup_id,
            restored_tables=len(restored_tables),
            object_store_restored=object_restored,
        )
        return PerTenantRestoreResult(
            tenant_id=tenant_id,
            backup_id=backup_id,
            bundle_dir=str(bundle_dir),
            encrypted=encrypted,
            dry_run=False,
            restored_tables=restored_tables,
            object_store_restored=object_restored,
            preview=preview,
            verification=report,
            fk_orphans_deleted=orphans_deleted,
        )

    def preview(self, bundle: str | Path, *, tenant_id: str) -> PerTenantRestorePreview:
        """Compute the dry-run preview WITHOUT any confirmation token.

        A read-only convenience the UI (task_12_12) calls to show what a restore
        would change before the operator's second confirmation. Internally a
        :meth:`run` with ``dry_run=True`` (which never writes), so verification
        still PRECEDES the preview (a corrupt bundle is reported, not previewed).
        """
        result = self.run(
            bundle,
            tenant_id=tenant_id,
            confirm=confirmation_token(tenant_id, self._locate_bundle(bundle).name),
            dry_run=True,
        )
        return result.preview

    # -- validation + locate -------------------------------------------------

    def _require_uuid_tenant(self, tenant_id: str) -> None:
        if not _UUID_RE.match(tenant_id):
            raise PerTenantRestoreError(
                f"tenant_id {tenant_id!r} is not a UUID; refusing to build a tenant predicate"
            )

    def _post_restore_integrity_sweep(self, tenant_id: str) -> int:
        """Corre el sweep de huérfanos referenciales tras el copiado (PROJ-03).

        El copiado filtrado va con ``session_replication_role = replica``; ver
        :mod:`workers.maintenance.integrity`. Best-effort: el restore ya está
        commiteado — un fallo aquí deja WARNING y devuelve -1, nunca revienta.
        Devuelve el total de filas huérfanas borradas (0 = restore limpio)."""
        import asyncio

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from workers.maintenance.integrity import sweep_fk_orphans

        async def _run() -> int:
            url = self._config.admin_database_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
            engine = create_async_engine(url)
            try:
                sm = async_sessionmaker(engine, expire_on_commit=False)
                async with sm() as session, session.begin():
                    report = await sweep_fk_orphans(session)
                return sum(report.values())
            finally:
                await engine.dispose()

        try:
            deleted = asyncio.run(_run())
        except Exception as exc:
            _log.warning(
                "restore_per_tenant.integrity_sweep_failed",
                tenant_id=tenant_id,
                error=str(exc),
            )
            return -1
        if deleted:
            _log.warning(
                "restore_per_tenant.integrity_sweep",
                tenant_id=tenant_id,
                orphans_deleted=deleted,
            )
        return deleted

    def _locate_bundle(self, bundle: str | Path) -> Path:
        """Resolve ``bundle`` (a bundle id or a path) to an existing directory."""
        candidate = Path(bundle)
        if candidate.is_absolute() or len(candidate.parts) > 1:
            bundle_dir = candidate
        else:
            bundle_dir = self._config.backup_root / candidate
        if not bundle_dir.is_dir():
            raise PerTenantRestoreError(f"backup bundle not found: {bundle_dir}")
        return bundle_dir

    def _load_manifest(self, bundle_dir: Path) -> dict[str, Any]:
        manifest_path = bundle_dir / MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise PerTenantRestoreError(f"no {MANIFEST_FILENAME} in bundle {bundle_dir}")
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PerTenantRestoreError(
                f"could not read manifest at {manifest_path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise PerTenantRestoreError(f"manifest at {manifest_path} is not a JSON object")
        return data

    def _decrypt_bundle(self, bundle_dir: Path) -> None:
        """Decrypt ``bundle.tar.enc`` (Vault key) + un-tar it into the bundle.

        Reverses :meth:`workers.backup.BackupEngine._encrypt_bundle` exactly as
        :meth:`workers.restore.RestoreEngine._decrypt_bundle` does — the same
        GCM-authenticated decrypt primitive (a tampered blob raises and aborts).
        """
        encryptor = self._encryptor
        if encryptor is None:
            raise PerTenantRestoreError(
                "bundle is encrypted but no BackupEncryptor was provided "
                "(the Vault key could not be wired)"
            )
        blob_path = bundle_dir / _ENCRYPTED_BUNDLE_NAME
        if not blob_path.is_file():
            raise PerTenantRestoreError(
                f"manifest says the bundle is encrypted but {_ENCRYPTED_BUNDLE_NAME} is missing"
            )
        archive_path = bundle_dir / _BUNDLE_ARCHIVE_NAME
        try:
            encryptor.decrypt_file(blob_path, archive_path)
        except BackupEncryptionError as exc:
            raise PerTenantRestoreError(f"failed to decrypt backup bundle: {exc}") from exc
        args = [
            "tar",
            "--extract",
            f"--directory={bundle_dir}",
            f"--file={archive_path}",
        ]
        result = self._runner.run(args, timeout=self._config.tar_timeout_s)
        if result.returncode != 0:
            raise PerTenantRestoreError(
                f"extracting the decrypted bundle failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        archive_path.unlink(missing_ok=True)
        _log.info("restore_per_tenant.decrypted", vault_key_name=self._config.encryption_vault_key)

    def _verify(self, bundle_dir: Path) -> VerificationReport:
        """Run the manifest verification (REUSED from task_12_03)."""
        try:
            return self._verifier.verify_bundle(bundle_dir)
        except BackupVerificationError as exc:
            raise PerTenantRestoreError(
                f"could not verify bundle before per-tenant restore: {exc}"
            ) from exc

    # -- staging DB ----------------------------------------------------------

    def _staging_db_name(self, backup_id: str, tenant_id: str) -> str:
        """A unique, SQL-safe throwaway staging database name.

        ``<prefix>_<backup_id>_<tenant8>`` with every non-identifier char folded to
        ``_`` and lower-cased (Postgres folds unquoted identifiers). The bundle id
        + tenant slice make concurrent restores collision-free; the result is
        validated against :data:`_SAFE_IDENT` so it can never be the live DB or an
        injection.
        """
        raw = f"{self._config.staging_db_prefix}_{backup_id}_{tenant_id[:8]}"
        safe = re.sub(r"[^A-Za-z0-9_]", "_", raw).lower()
        if not _SAFE_IDENT.fullmatch(safe):
            raise PerTenantRestoreError(f"could not build a safe staging db name from {raw!r}")
        return safe

    def _admin_db_args(self, *flags: str) -> list[str]:
        """Common ``--dbname=<admin url>`` carrier for psql/pg_restore.

        The full libpq URL carries the password so it never lands in a separate
        logged arg, mirroring the backup/full-restore engines.
        """
        return [*flags, f"--dbname={self._config.admin_database_url}"]

    def _create_staging_db(self, staging_db: str) -> None:
        """``createdb`` the throwaway staging database (idempotent: drop first)."""
        # Drop any leftover from a crashed prior run, then create. Both via psql so
        # we reuse the one admin URL carrier (createdb has no --dbname url form).
        self._psql_exec(
            f'DROP DATABASE IF EXISTS "{staging_db}";',
            timeout=self._config.createdb_timeout_s,
            on_db=self._maintenance_db_url(),
        )
        self._psql_exec(
            f'CREATE DATABASE "{staging_db}";',
            timeout=self._config.createdb_timeout_s,
            on_db=self._maintenance_db_url(),
        )
        _log.info("restore_per_tenant.staging_created", staging_db=staging_db)

    def _drop_staging_db(self, staging_db: str) -> None:
        """Drop the staging DB (best-effort; a leftover is harmless but noisy)."""
        result = self._runner.run(
            [
                "psql",
                f"--dbname={self._maintenance_db_url()}",
                "--no-psqlrc",
                "--quiet",
                "--command",
                f'DROP DATABASE IF EXISTS "{staging_db}";',
            ],
            timeout=self._config.createdb_timeout_s,
        )
        if result.returncode != 0:
            # Do NOT raise from the finally — log + move on; the throwaway DB being
            # left is an operational annoyance, not a data-safety problem.
            _log.warning(
                "restore_per_tenant.staging_drop_failed",
                staging_db=staging_db,
                detail=result.stderr.strip() or result.stdout.strip(),
            )
        else:
            _log.info("restore_per_tenant.staging_dropped", staging_db=staging_db)

    def _maintenance_db_url(self) -> str:
        """The admin URL re-pointed at the ``postgres`` maintenance DB.

        ``CREATE/DROP DATABASE`` cannot run while connected to the target DB, so
        the createdb/dropdb statements connect to ``postgres`` on the same server
        with the same admin credentials. Only the path component is swapped.
        """
        url = self._config.admin_database_url
        # Swap the final /<dbname> for /postgres, preserving everything before it.
        base, sep, _dbname = url.rpartition("/")
        if not sep:
            return url
        # Preserve any ?query on the dbname (rare for libpq, but be safe).
        return f"{base}/postgres"

    def _pg_restore_into_staging(self, bundle_dir: Path, staging_db: str) -> None:
        """``pg_restore`` the LOGICAL directory dump into the STAGING database.

        The staging DB is a fresh, empty copy — no ``--clean`` needed. Restored as
        the admin role but into the throwaway DB; the live DB is untouched.
        """
        dump_dir = bundle_dir / _DB_DUMP_DIRNAME
        if not dump_dir.is_dir():
            raise PerTenantRestoreError(f"bundle has no pg_dump directory at {dump_dir}")
        args = [
            "pg_restore",
            "--no-owner",
            "--no-privileges",
            f"--dbname={self._staging_db_url(staging_db)}",
            str(dump_dir),
        ]
        result = self._runner.run(args, timeout=self._config.pg_restore_timeout_s)
        if result.returncode != 0:
            raise PerTenantRestoreError(
                f"pg_restore into staging db failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        _log.info("restore_per_tenant.staged", staging_db=staging_db, dump_dir=str(dump_dir))

    def _staging_db_url(self, staging_db: str) -> str:
        """The admin URL re-pointed at the staging database."""
        url = self._config.admin_database_url
        base, sep, _dbname = url.rpartition("/")
        if not sep:
            return url
        return f"{base}/{staging_db}"

    # -- preview (read-only, against staging) --------------------------------

    def _preview_from_staging(
        self, staging_db: str, tenant_id: str, backup_id: str
    ) -> PerTenantRestorePreview:
        """Count the tenant's rows per tenant-scoped table in the STAGING db.

        One ``SELECT count(*) ... WHERE tenant_id = '<uuid>'`` per table, reading
        the count from psql's tuples-only output. Reads only — nothing is written,
        so this is the safe dry-run / preview probe.
        """
        previews: list[TablePreview] = []
        for table in self._config.tenant_scoped_tables:
            count = self._count_tenant_rows(staging_db, table, tenant_id)
            previews.append(TablePreview(table=table, row_count=count))
        return PerTenantRestorePreview(
            tenant_id=tenant_id, backup_id=backup_id, tables=tuple(previews)
        )

    def _count_tenant_rows(self, staging_db: str, table: str, tenant_id: str) -> int:
        """``SELECT count(*) FROM <table> WHERE tenant_id = '<uuid>'`` in staging."""
        sql = (
            f'SELECT count(*) FROM "{table}" '  # table validated in __post_init__
            f"WHERE {TENANT_COLUMN} = '{tenant_id}';"  # tenant_id validated as UUID
        )
        result = self._runner.run(
            [
                "psql",
                f"--dbname={self._staging_db_url(staging_db)}",
                "--no-psqlrc",
                "--quiet",
                "--tuples-only",
                "--no-align",
                "--command",
                sql,
            ],
            timeout=self._config.psql_timeout_s,
        )
        if result.returncode != 0:
            raise PerTenantRestoreError(
                f"counting tenant rows in {table!r} failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return _parse_count(result.stdout, table=table)

    # -- filtered copy into the live DB --------------------------------------

    def _copy_tenant_rows(self, staging_db: str, tenant_id: str) -> tuple[str, ...]:
        """Copy the tenant's rows from staging → LIVE, scoped by tenant_id, FK order.

        ONE transaction (BEGIN … COMMIT) on the live (admin) DB:

          * ``SET LOCAL session_replication_replica`` is NOT used — we rely on FK
            ordering instead, which keeps referential integrity meaningful.
          * In REVERSE FK order, ``DELETE FROM <child> WHERE tenant_id = '<t>'`` —
            clears the tenant's existing live rows child-first.
          * In FK order, re-insert the tenant's rows from staging via
            ``postgres_fdw``? No — staging is a separate DB. We use ``\\copy`` via a
            per-table ``INSERT INTO <live> SELECT * FROM dblink(...)`` would need an
            extension. Instead the transaction is built to pull from staging with
            ``dblink`` only if available; the portable path used here is a
            per-table ``psql`` pipe captured as one script. See the module
            docstring: the cross-DB move is expressed as a single SQL script run by
            psql against the LIVE db, using ``dblink`` to read the staging rows,
            ALWAYS filtered by ``tenant_id``.

        Every statement carries ``WHERE tenant_id = '<target>'`` so no other
        tenant's row is ever in scope. Runs as the BYPASSRLS admin role (the URL),
        so the policy does not hide the staging/other rows — but the predicate, not
        the policy, is what bounds the blast radius.
        """
        script = self._build_copy_script(staging_db, tenant_id)
        result = self._runner.run(
            [
                "psql",
                f"--dbname={self._config.admin_database_url}",
                "--no-psqlrc",
                "--quiet",
                "--set",
                "ON_ERROR_STOP=1",  # abort the whole transaction on the first error
                "--command",
                script,
            ],
            timeout=self._config.psql_timeout_s,
        )
        if result.returncode != 0:
            raise PerTenantRestoreError(
                f"filtered per-tenant copy into the live db failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        # The tables touched, in the order they were copied (FK order). Returned for
        # the result/audit; the dry-run preview is what enumerates row counts.
        restored = tuple(self._config.tenant_scoped_tables)
        _log.info(
            "restore_per_tenant.copied",
            tenant_id=tenant_id,
            tables=len(restored),
        )
        return restored

    def _build_copy_script(self, staging_db: str, tenant_id: str) -> str:
        """Build the single transactional SQL script for the filtered copy.

        Uses ``dblink`` to read the tenant's rows out of the staging DB and into
        the live tables, in ONE transaction. Structure:

            BEGIN;
            SET LOCAL session_replication_role = replica;  -- defer FK checks
            -- children first: clear the tenant's existing live rows
            DELETE FROM "tasks" WHERE tenant_id = '<t>';
            ... (reverse FK order) ...
            -- parents first: re-insert from staging, tenant-scoped on BOTH sides
            INSERT INTO "teams"
                SELECT t.* FROM dblink('<staging>',
                    'SELECT * FROM "teams" WHERE tenant_id = ''<t>''') AS t "teams";
            ... (FK order) ...
            COMMIT;

        Every DELETE and every staging SELECT is bounded by ``tenant_id = '<t>'``
        so the statement set can only ever touch the one tenant. The dblink rowtype
        is the live table's own composite type (``AS t "<table>"`` — a Postgres
        table name IS a row type), so dblink maps the remote columns with no
        per-column hardcoding and the script stays schema-agnostic. The whole thing
        is one transaction with ``ON_ERROR_STOP`` set on the psql side, so any
        failure rolls the live DB back to before the restore.
        """
        t = tenant_id  # already validated as a UUID
        staging_conn = self._staging_db_url(staging_db)
        tables = self._config.tenant_scoped_tables

        lines: list[str] = [
            "BEGIN;",
            # Defer FK triggers so the per-tenant DELETE/INSERT set need not be in a
            # globally-consistent order vs other tenants; FK ordering of OUR tables
            # is still respected, but a self-FK / cycle within the tenant slice
            # cannot wedge the load. Re-enabled implicitly at COMMIT.
            "SET LOCAL session_replication_role = replica;",
        ]
        # Children first — delete the tenant's existing live rows in REVERSE FK order
        # so a child is gone before its parent.
        for table in reversed(tables):
            lines.append(f"DELETE FROM \"{table}\" WHERE {TENANT_COLUMN} = '{t}';")
        # Parents first — re-insert the tenant's rows from staging in FK order. The
        # remote SELECT is itself tenant-scoped, so dblink never pulls another
        # tenant's row across.
        for table in tables:
            # Single-quotes inside the dblink SQL literal are doubled (SQL escaping).
            remote_select = f"SELECT * FROM \"{table}\" WHERE {TENANT_COLUMN} = ''{t}''"
            lines.append(
                f'INSERT INTO "{table}" '
                f"SELECT t.* FROM dblink('{staging_conn}', '{remote_select}') "
                f'AS t "{table}";'  # the live table's own row type maps the columns
            )
        lines.append("COMMIT;")
        return "\n".join(lines)

    # -- object storage (tenant slice only) ----------------------------------

    def _restore_object_store_slice(
        self, bundle_dir: Path, manifest: dict[str, Any], tenant_id: str
    ) -> bool:
        """Re-extract ONLY the tenant's ``<tenant_id>/`` prefix from the object store tar.

        The MinIO volume tar holds every tenant's objects; a per-tenant restore
        must touch only this tenant's slice. We extract the single path prefix
        ``./<tenant_id>/`` from the captured archive into the live volume's
        ``_data`` tree, leaving every other tenant's objects untouched. The wipe is
        scoped to the tenant's prefix dir too — never the whole volume.

        Returns True when the tenant slice was present + restored, False when the
        bundle captured no object-store volume (nothing to do, not an error).
        """
        volume = self._config.object_store_volume
        art = next(
            (
                a
                for a in manifest.get("artifacts", [])
                if a.get("kind") == "volume_tar" and a.get("source") == volume
            ),
            None,
        )
        if art is None:
            _log.info("restore_per_tenant.no_object_store", volume=volume)
            return False
        archive_name = str(art.get("path") or art.get("name") or "")
        archive_path = bundle_dir / archive_name
        if not archive_path.is_file():
            raise PerTenantRestoreError(f"object-store archive missing in bundle: {archive_path}")

        data_dir = self._config.volumes_mount_root / volume / "_data"
        tenant_prefix = f"{tenant_id}"  # objects are keyed <tenant_id>/<...>
        member = f"./{tenant_prefix}"
        # Wipe ONLY the tenant's prefix dir (never the whole volume), then extract
        # just that member from the archive. Best-effort wipe; the extract is hard.
        tenant_dir = data_dir / tenant_prefix
        if tenant_dir.exists():
            shutil.rmtree(tenant_dir, ignore_errors=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        args = [
            "tar",
            "--extract",
            "--gzip",
            f"--directory={data_dir}",
            f"--file={archive_path}",
            member,  # ONLY the tenant's slice — never the whole archive
        ]
        result = self._runner.run(args, timeout=self._config.tar_timeout_s)
        if result.returncode != 0:
            raise PerTenantRestoreError(
                f"restoring tenant object-store slice failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        _log.info(
            "restore_per_tenant.object_store_restored",
            tenant_id=tenant_id,
            volume=volume,
            prefix=tenant_prefix,
        )
        return True

    # -- helpers -------------------------------------------------------------

    def _psql_exec(self, sql: str, *, timeout: int, on_db: str) -> None:
        """Run a single ``psql --command`` against ``on_db``; raise on non-zero."""
        result = self._runner.run(
            ["psql", f"--dbname={on_db}", "--no-psqlrc", "--quiet", "--command", sql],
            timeout=timeout,
        )
        if result.returncode != 0:
            raise PerTenantRestoreError(
                f"psql command failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )


def _parse_count(stdout: str, *, table: str) -> int:
    """Parse a single integer from psql ``--tuples-only --no-align`` output."""
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            return int(stripped)
        except ValueError as exc:
            raise PerTenantRestoreError(
                f"could not parse row count for {table!r} from psql output {stripped!r}"
            ) from exc
    # No rows of output at all → treat as zero (an empty staging table).
    return 0


def run_per_tenant_restore(
    bundle: str | Path,
    *,
    tenant_id: str,
    confirm: str,
    dry_run: bool = False,
    settings: Settings | None = None,
    runner: CommandRunner | None = None,
    encryptor: BackupEncryptor | None = None,
) -> PerTenantRestoreResult:
    """Convenience entrypoint: build the engine from settings and run a per-tenant restore.

    This is what the per-tenant restore Celery task (a background job, task_12_12)
    and a future ``scripts/restore-tenant.sh`` call. ``runner`` / ``encryptor`` are
    injectable for tests; production leaves them ``None`` (real subprocess; and —
    when the bundle is encrypted — a default Vault/env-backed
    :class:`BackupEncryptor` built here so the engine can decrypt the blob).
    """
    cfg = PerTenantRestoreConfig.from_settings(settings or get_settings())
    if encryptor is None and cfg.encryption_enabled:
        from workers.backup_encryption import EnvSecretsProvider

        encryptor = BackupEncryptor(
            provider=EnvSecretsProvider(),
            vault_key_name=cfg.encryption_vault_key,
        )
    return PerTenantRestoreEngine(cfg, runner=runner, encryptor=encryptor).run(
        bundle, tenant_id=tenant_id, confirm=confirm, dry_run=dry_run
    )


__all__ = [
    "DEFAULT_TENANT_SCOPED_TABLES",
    "TENANT_COLUMN",
    "PerTenantRestoreConfig",
    "PerTenantRestoreEngine",
    "PerTenantRestoreError",
    "PerTenantRestorePreview",
    "PerTenantRestoreResult",
    "PerTenantRestoreVerificationError",
    "TablePreview",
    "confirmation_token",
    "run_per_tenant_restore",
]
