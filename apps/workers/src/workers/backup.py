"""Full backup engine (Plan 12 task_12_01).

Produces a *timestamped backup bundle* on the worker host:

  1. a **pg_dump LOGICAL** dump of PostgreSQL in ``directory`` format
     (``--format=directory``). Logical — not pg_basebackup — because that is
     the only shape that lets a later task restore a *single tenant's* rows
     (Decisiones Clave, Plan 12). Directory format is parallel-friendly and
     ``pg_restore --list`` can introspect it for the verification step.
  2. a **tar + gzip** of each configured data volume (MinIO objects, the
     Redis RDB/AOF, the Vault file backend). Volume names + their host
     mount root come from config (``WORKERS_BACKUP_VOLUMES`` /
     ``WORKERS_BACKUP_VOLUMES_MOUNT_ROOT``), never hardcoded.
  3. a **manifest.json** describing what was captured: artifact paths, byte
     sizes, SHA-256 checksums, timestamps, and the overall status.

Then it prunes local bundles older than the retention window
(``WORKERS_BACKUP_RETENTION_DAYS``, default 7).

Design — the subprocess seam
----------------------------
Real ``pg_dump`` / ``tar`` / live volume access cannot run inside the unit
test environment, so every external command goes through an injectable
:class:`CommandRunner`. Production wires :class:`SubprocessRunner`; tests inject
a fake that records the argv it was asked to run and fabricates the artifact
files. Tests therefore assert *command construction* (pg_dump gets the right
URL + format, the configured volumes get tar'd), the *orchestration* (ordering,
clean failure, artifact naming), and the *verification/manifest* logic — never
a real dump of the live stack.

Clean-failure contract
----------------------
If ANY sub-step fails, the whole run fails: the partial bundle directory is
removed and :class:`BackupError` is raised. There is no partial "success" — a
half-written bundle that looked fine but is missing the DB dump is worse than
no bundle, because it gives false confidence at restore time.

Encryption (task_12_02) plugs in *after* this engine assembles the bundle; the
manifest already carries ``encrypted: false`` so the later task only has to
flip it and wrap the artifacts with the Vault-resolved AES-256 key.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import structlog

from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.backup")

# Manifest schema version — bump if the on-disk shape changes so a future
# restore (task_12_10) can branch on it.
MANIFEST_VERSION = 1
MANIFEST_FILENAME = "manifest.json"

# Timestamp format for the bundle directory name. UTC, sortable, filesystem-
# safe (no colons — Windows-hostile). e.g. "20260530T031500Z".
_BUNDLE_TS_FORMAT = "%Y%m%dT%H%M%SZ"

# The DB dump lives in a directory-format subtree inside the bundle.
_DB_DUMP_DIRNAME = "postgres"

# Read chunk for checksums — bounded memory even for multi-GB tarballs.
_CHECKSUM_CHUNK = 1024 * 1024


class BackupError(RuntimeError):
    """Raised when a backup sub-step fails. The partial bundle is removed
    before this propagates, so a failed run never leaves a misleading
    half-bundle behind."""


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one external command."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    """The subprocess seam. Production uses :class:`SubprocessRunner`; tests
    inject a fake that records argv and fabricates artifact files."""

    def run(
        self,
        args: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult: ...


@dataclass
class SubprocessRunner:
    """Real runner — shells out with explicit argv (never ``shell=True``)."""

    def run(
        self,
        args: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        import os

        full_env = {**os.environ, **(env or {})}
        completed = subprocess.run(  # — explicit argv, no shell
            list(args),
            capture_output=True,
            text=True,
            env=full_env,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


@dataclass(frozen=True)
class BackupConfig:
    """Operator-tunable knobs for one backup run — no magic numbers.

    Built from :class:`workers.config.Settings` via :meth:`from_settings`, which
    is the only place the env defaults are read. Tests construct it directly
    with ``tmp_path`` roots.
    """

    backup_root: Path
    database_url: str
    volumes: tuple[str, ...]
    volumes_mount_root: Path
    retention_days: int
    # Wall-clock caps for the two heavy commands. Generous; a hung pg_dump or
    # tar is a problem, but a legitimate multi-GB dump must not be killed.
    pg_dump_timeout_s: int = 3600
    tar_timeout_s: int = 3600

    @classmethod
    def from_settings(cls, settings: Settings) -> BackupConfig:
        return cls(
            backup_root=Path(settings.backup_root),
            database_url=settings.backup_database_url,
            volumes=tuple(settings.backup_volumes),
            volumes_mount_root=Path(settings.backup_volumes_mount_root),
            retention_days=int(settings.backup_retention_days),
        )


@dataclass(frozen=True)
class ArtifactRecord:
    """One captured artifact in the manifest."""

    name: str
    kind: str  # "pg_dump" | "volume_tar"
    path: str  # relative to the bundle directory
    size_bytes: int
    sha256: str
    # For a volume tar, which docker volume it came from.
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class BackupManifest:
    """The manifest written at the root of a completed bundle."""

    version: int
    backup_id: str
    created_at: str  # ISO-8601 UTC
    status: str  # "completed"
    database_url_sanitized: str
    encrypted: bool
    artifacts: list[ArtifactRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "backup_id": self.backup_id,
            "created_at": self.created_at,
            "status": self.status,
            "database": {"url": self.database_url_sanitized},
            "encrypted": self.encrypted,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "total_size_bytes": sum(a.size_bytes for a in self.artifacts),
        }


@dataclass(frozen=True)
class BackupResult:
    """What :meth:`BackupEngine.run_full_backup` returns."""

    backup_id: str
    bundle_dir: Path
    manifest_path: Path
    artifacts: tuple[ArtifactRecord, ...]
    pruned: tuple[str, ...]


def _sanitize_db_url(url: str) -> str:
    """Strip the password from a libpq URL so it is safe to log / manifest.

    ``postgresql://user:secret@host/db`` → ``postgresql://user:***@host/db``.
    A best-effort masker — never leak the credential into the manifest.
    """
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, _, hostpart = rest.partition("@")
    if ":" in creds:
        user, _, _ = creds.partition(":")
        creds = f"{user}:***"
    return f"{scheme}://{creds}@{hostpart}"


def _checksum_file(path: Path) -> str:
    """SHA-256 of a file (streamed)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHECKSUM_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _checksum_tree(root: Path) -> str:
    """SHA-256 over a directory tree (directory-format pg_dump).

    Folds each file's relative path + content into one digest so the
    checksum is stable regardless of filesystem iteration order.
    """
    h = hashlib.sha256()
    for child in sorted(root.rglob("*")):
        if not child.is_file():
            continue
        rel = child.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        with child.open("rb") as fh:
            for chunk in iter(lambda: fh.read(_CHECKSUM_CHUNK), b""):
                h.update(chunk)
    return h.hexdigest()


def _dir_size(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


class BackupEngine:
    """Orchestrates one full-backup run behind an injectable command runner."""

    def __init__(
        self,
        config: BackupConfig,
        *,
        runner: CommandRunner | None = None,
        now: datetime | None = None,
    ) -> None:
        self._config = config
        self._runner: CommandRunner = runner or SubprocessRunner()
        # Injectable clock so tests get deterministic bundle ids + retention.
        self._now = now or datetime.now(UTC)

    @property
    def config(self) -> BackupConfig:
        return self._config

    # -- public API ---------------------------------------------------------

    def run_full_backup(self) -> BackupResult:
        """Run the full backup: DB dump → volume tars → manifest → prune.

        Raises :class:`BackupError` (after removing the partial bundle) if any
        sub-step fails. On success the bundle directory contains the DB dump,
        one ``<volume>.tar.gz`` per configured volume, and ``manifest.json``.
        """
        backup_id = self._now.strftime(_BUNDLE_TS_FORMAT)
        bundle_dir = self._config.backup_root / backup_id
        if bundle_dir.exists():
            raise BackupError(f"backup bundle {bundle_dir} already exists")

        bundle_dir.mkdir(parents=True, exist_ok=False)
        _log.info("backup.start", backup_id=backup_id, bundle_dir=str(bundle_dir))

        try:
            artifacts: list[ArtifactRecord] = []
            artifacts.append(self._dump_database(bundle_dir))
            for volume in self._config.volumes:
                artifacts.append(self._tar_volume(bundle_dir, volume))
            manifest = self._write_manifest(bundle_dir, backup_id, artifacts)
        except BackupError:
            # Remove the partial bundle so a failed run leaves nothing
            # that could be mistaken for a good backup.
            shutil.rmtree(bundle_dir, ignore_errors=True)
            raise
        except Exception as exc:  # any unexpected error → clean failure
            shutil.rmtree(bundle_dir, ignore_errors=True)
            raise BackupError(f"backup {backup_id} failed: {exc}") from exc

        pruned = self._prune_old_backups(keep=backup_id)

        _log.info(
            "backup.done",
            backup_id=backup_id,
            artifacts=len(artifacts),
            pruned=len(pruned),
        )
        return BackupResult(
            backup_id=backup_id,
            bundle_dir=bundle_dir,
            manifest_path=manifest,
            artifacts=tuple(artifacts),
            pruned=tuple(pruned),
        )

    # -- steps --------------------------------------------------------------

    def _dump_database(self, bundle_dir: Path) -> ArtifactRecord:
        """Run pg_dump in LOGICAL directory format into the bundle.

        ``--format=directory`` (``-Fd``) is mandatory: it is the only format
        ``pg_restore`` can list + filter by table/schema, which the later
        per-tenant restore (task_12_11) needs. ``--dbname`` carries the full
        libpq URL so the password never lands in a separate logged arg.
        """
        out_dir = bundle_dir / _DB_DUMP_DIRNAME
        args = [
            "pg_dump",
            "--format=directory",
            "--no-owner",
            "--no-privileges",
            f"--file={out_dir}",
            f"--dbname={self._config.database_url}",
        ]
        result = self._runner.run(args, timeout=self._config.pg_dump_timeout_s)
        if result.returncode != 0:
            raise BackupError(
                f"pg_dump failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        if not out_dir.exists():
            raise BackupError(f"pg_dump reported success but produced no output at {out_dir}")
        return ArtifactRecord(
            name=_DB_DUMP_DIRNAME,
            kind="pg_dump",
            path=_DB_DUMP_DIRNAME,
            size_bytes=_dir_size(out_dir),
            sha256=_checksum_tree(out_dir),
        )

    def _tar_volume(self, bundle_dir: Path, volume: str) -> ArtifactRecord:
        """tar + gzip one docker named volume's ``_data`` tree.

        The volume materialises at ``<mount_root>/<volume>/_data`` (docker's
        local driver layout). We tar from inside that directory (``-C``) so the
        archive holds the volume's contents at its root, not the host path.
        """
        archive_name = f"{volume}.tar.gz"
        archive_path = bundle_dir / archive_name
        source_dir = self._config.volumes_mount_root / volume / "_data"
        args = [
            "tar",
            "--gzip",
            f"--directory={source_dir}",
            f"--file={archive_path}",
            ".",
        ]
        result = self._runner.run(args, timeout=self._config.tar_timeout_s)
        if result.returncode != 0:
            raise BackupError(
                f"tar of volume {volume!r} failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        if not archive_path.exists():
            raise BackupError(f"tar of volume {volume!r} reported success but produced no archive")
        return ArtifactRecord(
            name=archive_name,
            kind="volume_tar",
            path=archive_name,
            size_bytes=archive_path.stat().st_size,
            sha256=_checksum_file(archive_path),
            source=volume,
        )

    def _write_manifest(
        self, bundle_dir: Path, backup_id: str, artifacts: list[ArtifactRecord]
    ) -> Path:
        manifest = BackupManifest(
            version=MANIFEST_VERSION,
            backup_id=backup_id,
            created_at=self._now.isoformat(),
            status="completed",
            database_url_sanitized=_sanitize_db_url(self._config.database_url),
            encrypted=False,
            artifacts=artifacts,
        )
        manifest_path = bundle_dir / MANIFEST_FILENAME
        manifest_path.write_text(
            json.dumps(manifest.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return manifest_path

    def _prune_old_backups(self, *, keep: str) -> list[str]:
        """Remove bundle directories older than the retention window.

        A bundle's age comes from its directory name (the UTC timestamp), not
        its mtime — robust against a touched/copied directory. ``keep`` is the
        just-written bundle, never pruned even if retention is 0.
        """
        root = self._config.backup_root
        if not root.exists():
            return []
        cutoff = self._now - timedelta(days=self._config.retention_days)
        pruned: list[str] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name == keep:
                continue
            ts = _parse_bundle_ts(entry.name)
            if ts is None:
                # Not one of ours — leave it alone.
                continue
            if ts < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                pruned.append(entry.name)
        if pruned:
            _log.info("backup.pruned", count=len(pruned), names=pruned)
        return pruned


def _parse_bundle_ts(name: str) -> datetime | None:
    """Parse a bundle directory name back to a UTC datetime, or None."""
    try:
        return datetime.strptime(name, _BUNDLE_TS_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def run_full_backup(
    *,
    settings: Settings | None = None,
    runner: CommandRunner | None = None,
    now: datetime | None = None,
) -> BackupResult:
    """Convenience entrypoint: build the engine from settings and run it.

    This is what ``scripts/backup.sh`` and the beat task call. ``runner`` /
    ``now`` are injectable for tests; production leaves them ``None`` (real
    subprocess, real clock).
    """
    cfg = BackupConfig.from_settings(settings or get_settings())
    return BackupEngine(cfg, runner=runner, now=now).run_full_backup()
