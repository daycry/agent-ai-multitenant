#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# scripts/dev/down.ps1
#
# Stop the dev stack started by scripts/dev/up.ps1. Reads .dev/*.pid and
# tree-kills each process with taskkill /F /T so uvicorn workers and node
# children die with their parents.
#
# Usage:
#   .\scripts\dev\down.ps1            # stops api-server + admin-panel
#   .\scripts\dev\down.ps1 -Docker    # also `docker compose down` the stack
# -----------------------------------------------------------------------------

[CmdletBinding()]
param(
    [switch]$Docker
)

$ErrorActionPreference = "Continue"  # best-effort: keep going on partial failures

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$DevDir       = Join-Path $RepoRoot ".dev"
$ApiPidFile   = Join-Path $DevDir "api-server.pid"
$AdminPidFile = Join-Path $DevDir "admin-panel.pid"

function Stop-FromPidFile {
    param([string]$PidFile, [string]$Name)
    if (-not (Test-Path $PidFile)) {
        Write-Host "    no $PidFile -- $Name not tracked" -ForegroundColor DarkGray
        return
    }
    $procPid = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $procPid) {
        Remove-Item $PidFile -ErrorAction SilentlyContinue
        Write-Host "    $PidFile was empty" -ForegroundColor DarkGray
        return
    }
    if (-not (Get-Process -Id $procPid -ErrorAction SilentlyContinue)) {
        Write-Host "    $Name (pid $procPid) already gone" -ForegroundColor DarkGray
        Remove-Item $PidFile -ErrorAction SilentlyContinue
        return
    }
    Write-Host "==> Stopping $Name (pid $procPid)" -ForegroundColor Cyan
    & taskkill /F /T /PID $procPid 2>$null | Out-Null
    Remove-Item $PidFile -ErrorAction SilentlyContinue
}

Stop-FromPidFile -PidFile $AdminPidFile -Name "admin-panel"
Stop-FromPidFile -PidFile $ApiPidFile   -Name "api-server"

if ($Docker) {
    Write-Host "==> docker compose down" -ForegroundColor Cyan
    # --remove-orphans también para los servicios del overlay de monitoring
    # (up.ps1 -Monitoring) y el one-shot ollama-bootstrap, que no están en estos
    # dos ficheros pero sí en el mismo proyecto.
    & docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml down --remove-orphans
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
