#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Thin wrapper around the Python full-backup engine (Plan 12 task_12_01).
#
# The real logic lives in `apps/workers/src/workers/backup.py` so it is unit-
# testable (the tests mock the pg_dump / tar subprocess seam). This script is
# just the operator-facing entrypoint cron / the pre-upgrade hook invokes; it
# delegates to the engine and does NOT re-implement any of it.
#
# Produces a timestamped bundle under WORKERS_BACKUP_ROOT:
#   <id>/postgres/         pg_dump LOGICAL directory-format dump
#   <id>/<volume>.tar.gz   tar+gzip of each configured data volume
#   <id>/manifest.json     captured artifacts, sizes, SHA-256 checksums
# then prunes bundles older than WORKERS_BACKUP_RETENTION_DAYS.
#
# Tunables (all read by the engine from the WORKERS_ env, never hardcoded):
#   WORKERS_BACKUP_ROOT                where bundles are written
#   WORKERS_BACKUP_DATABASE_URL        libpq URL pg_dump connects with
#   WORKERS_BACKUP_RETENTION_DAYS      local retention window (default 7)
#   WORKERS_BACKUP_VOLUMES             JSON list of docker volume names
#   WORKERS_BACKUP_VOLUMES_MOUNT_ROOT  host dir holding the volumes
#
# Usage (from the repo root):
#   ./scripts/backup.sh
# -----------------------------------------------------------------------------
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"

exec "${PYTHON_BIN}" -c '
import sys
from workers.backup import BackupError, run_full_backup

try:
    result = run_full_backup()
except BackupError as exc:
    print(f"backup failed: {exc}", file=sys.stderr)
    raise SystemExit(1)

print(f"backup ok: {result.backup_id}")
print(f"  bundle:    {result.bundle_dir}")
print(f"  manifest:  {result.manifest_path}")
print(f"  artifacts: {len(result.artifacts)}")
print(f"  pruned:    {len(result.pruned)}")
'
