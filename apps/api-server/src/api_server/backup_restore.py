"""Read-only backup-bundle introspection for the restore UI (Plan 12 task_12_12).

The restore UI's list + preview endpoints need to enumerate the available backup
bundles and read one bundle's manifest WITHOUT importing the heavy workers
restore engines or running anything destructive. This module is exactly that
read-only seam: it walks the configured ``backup_root`` and parses each bundle's
``manifest.json`` (the same checksummed manifest :mod:`workers.backup` writes).

It does NOT decrypt, verify, or restore — those happen in the workers background
job the trigger endpoint enqueues. Listing only reads the bundle directory names
+ each manifest's summary fields, so it is cheap + side-effect-free and safe to
call on the api-server request thread.

The per-tenant table list shown in the preview comes from the workers'
``DEFAULT_TENANT_SCOPED_TABLES`` (the single source of truth for which tables a
per-tenant restore touches), imported lazily so the workers package import cost
stays off the api-server hot path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

_log = structlog.get_logger("api_server.backup_restore")

MANIFEST_FILENAME = "manifest.json"

# Bundle directory names are UTC timestamps like "20260530T031500Z" — sortable
# lexicographically, so a reverse sort gives newest-first. A directory that is
# not one of ours (no manifest) is skipped.
_BUNDLE_NAME_LEN = len("20260530T031500Z")


class BackupBundleError(RuntimeError):
    """Raised when a requested bundle is missing or its manifest is unreadable.

    The list endpoint tolerates a single bad bundle (skips it); the preview
    endpoint surfaces this as a 404 / 422 for the specific bundle asked for."""


@dataclass(frozen=True)
class ArtifactSummary:
    """One artifact entry pulled from a bundle manifest (preview)."""

    name: str
    kind: str
    size_bytes: int
    source: str | None = None


@dataclass(frozen=True)
class BundleSummary:
    """The summary of one local bundle (list + preview)."""

    backup_id: str
    encrypted: bool
    created_at: str | None
    status: str | None
    total_size_bytes: int
    artifacts: tuple[ArtifactSummary, ...] = field(default_factory=tuple)

    @property
    def has_database_dump(self) -> bool:
        """True when the bundle captured a database dump (plaintext) OR is an
        encrypted bundle (which collapses the dump inside the blob).

        A per-tenant restore needs the logical dump to filter; a bundle that
        captured one (or an encrypted bundle that wraps one) supports it."""
        if self.encrypted:
            return True
        return any(a.kind == "pg_dump" for a in self.artifacts)


def _is_bundle_dir(entry: Path) -> bool:
    return entry.is_dir() and (entry / MANIFEST_FILENAME).is_file()


def _read_manifest(bundle_dir: Path) -> dict[str, Any]:
    manifest_path = bundle_dir / MANIFEST_FILENAME
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupBundleError(f"could not read manifest at {manifest_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BackupBundleError(f"manifest at {manifest_path} is not a JSON object")
    return data


def _summarise(backup_id: str, manifest: dict[str, Any]) -> BundleSummary:
    raw_artifacts = manifest.get("artifacts", [])
    artifacts: list[ArtifactSummary] = []
    if isinstance(raw_artifacts, list):
        for art in raw_artifacts:
            if not isinstance(art, dict):
                continue
            artifacts.append(
                ArtifactSummary(
                    name=str(art.get("name", "")),
                    kind=str(art.get("kind", "")),
                    size_bytes=int(art.get("size_bytes", 0) or 0),
                    source=(str(art["source"]) if art.get("source") is not None else None),
                )
            )
    return BundleSummary(
        backup_id=backup_id,
        encrypted=bool(manifest.get("encrypted", False)),
        created_at=(str(manifest["created_at"]) if manifest.get("created_at") else None),
        status=(str(manifest["status"]) if manifest.get("status") else None),
        total_size_bytes=int(manifest.get("total_size_bytes", 0) or 0),
        artifacts=tuple(artifacts),
    )


def list_local_bundles(backup_root: str | Path) -> list[BundleSummary]:
    """Enumerate the local backup bundles under ``backup_root``, newest first.

    Each bundle is a directory holding a ``manifest.json``; a directory without a
    readable manifest is skipped (a half-written / foreign directory must not
    break the listing). Returns an empty list when the root does not exist yet.
    """
    root = Path(backup_root)
    if not root.is_dir():
        return []
    summaries: list[BundleSummary] = []
    for entry in sorted(root.iterdir(), reverse=True):
        if not _is_bundle_dir(entry):
            continue
        try:
            manifest = _read_manifest(entry)
        except BackupBundleError as exc:
            _log.warning("restore.list.bad_bundle", bundle=str(entry), error=str(exc))
            continue
        summaries.append(_summarise(entry.name, manifest))
    return summaries


def load_local_bundle(backup_root: str | Path, backup_id: str) -> BundleSummary:
    """Read one local bundle's manifest summary, for the preview pane.

    ``backup_id`` is resolved under ``backup_root`` ONLY (never an absolute path
    a client could traverse to) — a name with a path separator is rejected. A
    missing bundle / unreadable manifest raises :class:`BackupBundleError`.
    """
    if "/" in backup_id or "\\" in backup_id or backup_id in {"", ".", ".."}:
        raise BackupBundleError(f"invalid backup id {backup_id!r}")
    bundle_dir = Path(backup_root) / backup_id
    if not _is_bundle_dir(bundle_dir):
        raise BackupBundleError(f"backup bundle not found: {backup_id}")
    return _summarise(backup_id, _read_manifest(bundle_dir))


def tenant_scoped_tables() -> list[str]:
    """The FK-ordered tenant-scoped table list a per-tenant restore touches.

    Imported lazily from the workers package (the single source of truth) so the
    workers import cost stays off the api-server hot path. The preview pane shows
    this so the operator sees the blast radius before confirming a per-tenant
    restore."""
    from workers.restore_per_tenant import DEFAULT_TENANT_SCOPED_TABLES

    return list(DEFAULT_TENANT_SCOPED_TABLES)


__all__ = [
    "ArtifactSummary",
    "BackupBundleError",
    "BundleSummary",
    "list_local_bundles",
    "load_local_bundle",
    "tenant_scoped_tables",
]
