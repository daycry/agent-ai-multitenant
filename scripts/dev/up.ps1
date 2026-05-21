#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# scripts/dev/up.ps1
#
# Start the dev stack (docker + api-server + admin-panel) DETACHED. After
# this returns you can close the terminal and the services keep running.
# PIDs go to .dev/*.pid; logs go to .dev/*.log; both gitignored.
#
# Use scripts/dev/down.ps1 to stop.
#
# Usage:
#   .\scripts\dev\up.ps1
#   .\scripts\dev\up.ps1 -ApiPort 8002 -AdminPort 3001
# -----------------------------------------------------------------------------

[CmdletBinding()]
param(
    [int]$ApiPort = 8001,
    [int]$AdminPort = 3000
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$DevDir = Join-Path $RepoRoot ".dev"
New-Item -ItemType Directory -Path $DevDir -Force | Out-Null

$ApiPidFile   = Join-Path $DevDir "api-server.pid"
$AdminPidFile = Join-Path $DevDir "admin-panel.pid"
$ApiLog       = Join-Path $DevDir "api-server.log"
$ApiErr       = Join-Path $DevDir "api-server.err.log"
$AdminLog     = Join-Path $DevDir "admin-panel.log"
$AdminErr     = Join-Path $DevDir "admin-panel.err.log"

$ComposeArgs = @(
    "-f", "docker/docker-compose.yml",
    "-f", "docker/docker-compose.dev.yml"
)

function Test-PortBindable {
    param([int]$Port)
    try {
        $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $l.Start(); $l.Stop()
        return $true
    } catch { return $false }
}

# ---------------------------------------------------------------------------
# Reject "already running" scenarios up front. If a previous up.ps1 already
# launched stuff, the user should run down.ps1 first instead of stacking.
# ---------------------------------------------------------------------------
foreach ($pair in @(@($ApiPidFile, "api-server"), @($AdminPidFile, "admin-panel"))) {
    $file, $name = $pair
    if (Test-Path $file) {
        $oldPid = Get-Content $file -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
            throw "$name already running (pid $oldPid). Run scripts/dev/down.ps1 first, or delete $file if it's stale."
        }
        Remove-Item $file -ErrorAction SilentlyContinue
    }
}

if (-not (Test-PortBindable -Port $ApiPort))   { throw "port $ApiPort is in use. Free it or pick another with -ApiPort." }
if (-not (Test-PortBindable -Port $AdminPort)) { throw "port $AdminPort is in use. Free it or pick another with -AdminPort." }

# ---------------------------------------------------------------------------
# Docker stack
# ---------------------------------------------------------------------------
Write-Host "==> Bringing docker stack up" -ForegroundColor Cyan
& docker compose @ComposeArgs up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }

Write-Host "==> Waiting for postgres to be healthy (max 60s)" -ForegroundColor Cyan
$pgDeadline = (Get-Date).AddSeconds(60)
$pgHealthy = $false
while ((Get-Date) -lt $pgDeadline) {
    $raw = & docker compose @ComposeArgs ps postgres --format json 2>$null
    if ($raw) {
        try {
            $entry = $raw | ConvertFrom-Json -ErrorAction Stop
            if ($entry -and $entry.Health -eq "healthy") { $pgHealthy = $true; break }
        } catch { }
    }
    Start-Sleep -Seconds 2
}
if (-not $pgHealthy) { throw "postgres did not become healthy within 60s" }

# ---------------------------------------------------------------------------
# Alembic migrations
# ---------------------------------------------------------------------------
$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw ".venv missing. Run .\scripts\dev\bootstrap.ps1 first."
}

Write-Host "==> Applying Alembic migrations" -ForegroundColor Cyan
$env:DATABASE_URL = "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
Push-Location (Join-Path $RepoRoot "apps\api-server")
try {
    & $venvPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "alembic upgrade failed" }
} finally {
    Pop-Location
    Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------
# api-server (uvicorn) — detached
# ---------------------------------------------------------------------------
$env:API_SERVER_DATABASE_URL       = "postgresql+asyncpg://app_user:changeme-app-dev-only@localhost:15432/agentic_platform"
$env:API_SERVER_ADMIN_DATABASE_URL = "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
$env:API_SERVER_REDIS_URL          = "redis://localhost:6379/0"
$env:API_SERVER_JWT_SECRET         = "dev-only-jwt-secret-change-me"

Remove-Item $ApiLog, $ApiErr -ErrorAction SilentlyContinue
Write-Host "==> Starting api-server on http://127.0.0.1:$ApiPort (logs: $ApiLog)" -ForegroundColor Cyan
$apiProc = Start-Process -PassThru -WindowStyle Hidden `
    -FilePath $venvPython `
    -ArgumentList "-m", "uvicorn", "api_server.main:app", "--port", $ApiPort `
    -WorkingDirectory (Join-Path $RepoRoot "apps\api-server") `
    -RedirectStandardOutput $ApiLog `
    -RedirectStandardError $ApiErr
$apiProc.Id | Out-File -Encoding ascii $ApiPidFile

Write-Host "==> Waiting for /healthz (max 30s)" -ForegroundColor Cyan
$hzDeadline = (Get-Date).AddSeconds(30)
$apiUp = $false
while ((Get-Date) -lt $hzDeadline) {
    if ($apiProc.HasExited) {
        Start-Sleep -Milliseconds 300
        Get-Content $ApiErr -Tail 30 -ErrorAction SilentlyContinue | Out-Host
        Remove-Item $ApiPidFile -ErrorAction SilentlyContinue
        throw "api-server exited prematurely. See $ApiErr"
    }
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/healthz" -TimeoutSec 2 -ErrorAction Stop | Out-Null
        $apiUp = $true; break
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $apiUp) { throw "api-server /healthz did not respond within 30s. See $ApiLog / $ApiErr" }

# ---------------------------------------------------------------------------
# admin-panel (npm run dev) — detached
# Launched via cmd.exe so taskkill /T can walk the tree (cmd -> npm.cmd -> node).
# ---------------------------------------------------------------------------
if (-not (Test-Path (Join-Path $RepoRoot "apps\admin-panel\node_modules\next"))) {
    throw "admin-panel deps missing. Run: cd apps\admin-panel; npm install"
}

Remove-Item $AdminLog, $AdminErr -ErrorAction SilentlyContinue
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:$ApiPort"
Write-Host "==> Starting admin-panel on http://localhost:$AdminPort (logs: $AdminLog)" -ForegroundColor Cyan
$adminProc = Start-Process -PassThru -WindowStyle Hidden `
    -FilePath "cmd.exe" `
    -ArgumentList "/c", "npm", "run", "dev", "--", "-p", $AdminPort `
    -WorkingDirectory (Join-Path $RepoRoot "apps\admin-panel") `
    -RedirectStandardOutput $AdminLog `
    -RedirectStandardError $AdminErr
$adminProc.Id | Out-File -Encoding ascii $AdminPidFile

Write-Host "==> Waiting for admin-panel to compile (max 60s)" -ForegroundColor Cyan
$adminDeadline = (Get-Date).AddSeconds(60)
$adminUp = $false
while ((Get-Date) -lt $adminDeadline) {
    if ($adminProc.HasExited) {
        Start-Sleep -Milliseconds 300
        Get-Content $AdminErr -Tail 30 -ErrorAction SilentlyContinue | Out-Host
        Remove-Item $AdminPidFile -ErrorAction SilentlyContinue
        throw "admin-panel exited prematurely. See $AdminErr"
    }
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$AdminPort/" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop | Out-Null
        $adminUp = $true; break
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $adminUp) { throw "admin-panel did not start within 60s. See $AdminLog / $AdminErr" }

# ---------------------------------------------------------------------------
# Print URLs + handoff
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "Dev stack is up. You can close this terminal."                   -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Admin panel:   http://localhost:$AdminPort/login"
Write-Host "                 (root@example.com / longenoughpw)"
Write-Host "  API docs:      http://127.0.0.1:$ApiPort/docs"
Write-Host "  API healthz:   http://127.0.0.1:$ApiPort/healthz"
Write-Host "  MinIO:         http://localhost:9001 (minioadmin / changeme-dev-only)"
Write-Host "  Vault UI:      http://localhost:8200/ui (token: dev-root-token)"
Write-Host ""
Write-Host "  Logs:   $DevDir\*.log"
Write-Host "  Stop:   .\scripts\dev\down.ps1"
Write-Host ""
