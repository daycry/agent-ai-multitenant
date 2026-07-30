#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# scripts/backup-data.ps1
#
# Back up the agents' data-root (bare repos + per-task worktrees + dep-cache).
# Desde 2026-07-03 vive en el named volume EXTERNO `agentic-platform-agent-data`
# (durable frente a engine-restarts y `down -v`; ver
# docs/06-runbooks/data-durability-windows-wsl2.md) — este script lo vuelca a un
# path DURABLE de Windows antes de operaciones destructivas (docker volume rm,
# Docker Desktop Clean/Purge) o como snapshot manual extra al backup diario.
#
# Produces a .tar.gz via a throwaway alpine container that mounts the volume
# read-only; nothing is written back.
#
# Usage:
#   .\scripts\backup-data.ps1 -Destination C:\AgentData\backups
# -----------------------------------------------------------------------------
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Destination,
    [string]$Volume = "agentic-platform-agent-data"
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path $Destination)) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
}
$Destination = (Resolve-Path $Destination).Path
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$archive = "agent-platform-$stamp.tar.gz"

Write-Host "==> Backing up volume $Volume -> $Destination\$archive" -ForegroundColor Cyan
docker run --rm `
    -v "${Volume}:/data:ro" `
    -v "${Destination}:/backup" `
    alpine `
    tar czf "/backup/$archive" -C /data .

if ($LASTEXITCODE -ne 0) { throw "backup failed (docker exit $LASTEXITCODE)" }
Write-Host "Done: $Destination\$archive" -ForegroundColor Green
