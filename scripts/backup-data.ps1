#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# scripts/backup-data.ps1
#
# Back up /data/agent-platform (bare repos + per-task worktrees) — the bind mount
# that lives INSIDE the WSL2 VM and is NOT covered by the tenant pg_dump backup
# (ADR 0036 / docs/06-runbooks/backups.md). Vulnerable to `wsl --shutdown` and
# Docker Desktop "Clean / Purge data" (see data-durability-windows-wsl2.md), so
# snapshot it to a DURABLE Windows path before any destructive op or VM reset.
#
# Produces a .tar.gz via a throwaway alpine container that mounts the data dir
# read-only; nothing is written back to /data.
#
# Usage:
#   .\scripts\backup-data.ps1 -Destination C:\AgentData\backups
# -----------------------------------------------------------------------------
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Destination
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path $Destination)) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
}
$Destination = (Resolve-Path $Destination).Path
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$archive = "agent-platform-$stamp.tar.gz"

Write-Host "==> Backing up /data/agent-platform -> $Destination\$archive" -ForegroundColor Cyan
docker run --rm `
    -v /data/agent-platform:/data:ro `
    -v "${Destination}:/backup" `
    alpine `
    tar czf "/backup/$archive" -C /data .

if ($LASTEXITCODE -ne 0) { throw "backup failed (docker exit $LASTEXITCODE)" }
Write-Host "Done: $Destination\$archive" -ForegroundColor Green
