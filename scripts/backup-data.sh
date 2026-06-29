#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# scripts/backup-data.sh
#
# Back up /data/agent-platform (bare repos + per-task worktrees) — the bind mount
# NOT covered by the tenant pg_dump backup (ADR 0036). Snapshot it to a durable
# path before any destructive op or VM reset (see data-durability-windows-wsl2.md).
# Uses a throwaway alpine container that mounts the data dir read-only.
#
# Usage:
#   ./scripts/backup-data.sh /mnt/c/AgentData/backups
# -----------------------------------------------------------------------------
set -euo pipefail

DEST="${1:?usage: backup-data.sh <destination-dir>}"
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="agent-platform-${STAMP}.tar.gz"

echo "==> Backing up /data/agent-platform -> ${DEST}/${ARCHIVE}"
docker run --rm \
    -v /data/agent-platform:/data:ro \
    -v "${DEST}:/backup" \
    alpine \
    tar czf "/backup/${ARCHIVE}" -C /data .

echo "Done: ${DEST}/${ARCHIVE}"
