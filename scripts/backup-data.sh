#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# scripts/backup-data.sh
#
# Back up the agents' data-root (bare repos + worktrees + dep-cache). Desde
# 2026-07-03 vive en el named volume EXTERNO `agentic-platform-agent-data`
# (durable; ver data-durability-windows-wsl2.md) — este script lo vuelca a un
# path durable antes de operaciones destructivas (docker volume rm, Clean/Purge).
# Uses a throwaway alpine container that mounts the volume read-only.
#
# Usage:
#   ./scripts/backup-data.sh /mnt/c/AgentData/backups [volume]
# -----------------------------------------------------------------------------
set -euo pipefail

DEST="${1:?usage: backup-data.sh <destination-dir> [volume]}"
VOLUME="${2:-agentic-platform-agent-data}"
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="agent-platform-${STAMP}.tar.gz"

echo "==> Backing up volume ${VOLUME} -> ${DEST}/${ARCHIVE}"
docker run --rm \
    -v "${VOLUME}:/data:ro" \
    -v "${DEST}:/backup" \
    alpine \
    tar czf "/backup/${ARCHIVE}" -C /data .

echo "Done: ${DEST}/${ARCHIVE}"
