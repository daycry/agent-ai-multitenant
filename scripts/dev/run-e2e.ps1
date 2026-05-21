#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# scripts/dev/run-e2e.ps1
#
# All-in-one Playwright runner for task_00_13. Replaces the 4-terminal
# manual choreography (docker / api-server / admin-panel / playwright)
# with a single command.
#
# Steps:
#   1. docker compose up -d (idempotent).
#   2. Wait for postgres to be healthy.
#   3. alembic upgrade head.
#   4. Launch uvicorn on $ApiPort in the background, log to a file.
#   5. Wait for /healthz to respond.
#   6. Register the admin user (skip on 409) and UPDATE is_system_admin.
#   7. `npm run e2e` — Playwright auto-starts its own `npm run dev`
#      thanks to the webServer block in playwright.config.ts.
#   8. Tear down the background uvicorn (in `finally`, so even a test
#      failure stops the server cleanly).
#
# Usage:
#   .\scripts\dev\run-e2e.ps1
#   .\scripts\dev\run-e2e.ps1 -ApiPort 8002 -AdminEmail other@x.test
#
# Requires: PowerShell 5.1+, Docker Desktop, .venv created via
# scripts/dev/bootstrap.ps1, apps/admin-panel npm install done,
# Playwright browsers installed (npm run e2e:install once).
# -----------------------------------------------------------------------------

[CmdletBinding()]
param(
    [string]$AdminEmail = "",
    [string]$AdminPassword = "",
    [int]$ApiPort = 8001
)

$ErrorActionPreference = "Stop"

# Resolve defaults (env vars win over hardcoded ones).
if (-not $AdminEmail) {
    $AdminEmail = if ($env:E2E_ADMIN_EMAIL) { $env:E2E_ADMIN_EMAIL } else { "root@example.com" }
}
if (-not $AdminPassword) {
    $AdminPassword = if ($env:E2E_ADMIN_PASSWORD) { $env:E2E_ADMIN_PASSWORD } else { "longenoughpw" }
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot
Write-Host "==> Repo root: $RepoRoot" -ForegroundColor Cyan

$ComposeArgs = @(
    "-f", "docker/docker-compose.yml",
    "-f", "docker/docker-compose.dev.yml"
)

# ---------------------------------------------------------------------------
# 1) Docker stack
# ---------------------------------------------------------------------------
Write-Host "==> Ensuring docker stack is up" -ForegroundColor Cyan
& docker compose @ComposeArgs up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }

# ---------------------------------------------------------------------------
# 2) Wait for postgres healthy
# ---------------------------------------------------------------------------
Write-Host "==> Waiting for postgres to be healthy (max 60s)" -ForegroundColor Cyan
$pgDeadline = (Get-Date).AddSeconds(60)
$pgHealthy = $false
while ((Get-Date) -lt $pgDeadline) {
    $raw = & docker compose @ComposeArgs ps postgres --format json 2>$null
    if ($raw) {
        try {
            $entry = $raw | ConvertFrom-Json -ErrorAction Stop
            if ($entry -and $entry.Health -eq "healthy") {
                $pgHealthy = $true
                break
            }
        } catch { }
    }
    Start-Sleep -Seconds 2
}
if (-not $pgHealthy) { throw "postgres did not become healthy within 60s" }

# ---------------------------------------------------------------------------
# 3) Alembic migrations
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
# 4) Launch api-server in background
# ---------------------------------------------------------------------------
$env:API_SERVER_DATABASE_URL = "postgresql+asyncpg://app_user:changeme-app-dev-only@localhost:15432/agentic_platform"
$env:API_SERVER_ADMIN_DATABASE_URL = "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
$env:API_SERVER_REDIS_URL = "redis://localhost:6379/0"
$env:API_SERVER_JWT_SECRET = "dev-only-jwt-secret-change-me"

$apiLog = Join-Path $RepoRoot ".e2e-api-server.log"
$apiErr = Join-Path $RepoRoot ".e2e-api-server.err.log"
Remove-Item $apiLog, $apiErr -ErrorAction SilentlyContinue

Write-Host "==> Starting api-server on http://localhost:$ApiPort (logs: $apiLog)" -ForegroundColor Cyan
$apiProc = Start-Process -PassThru -NoNewWindow `
    -FilePath $venvPython `
    -ArgumentList "-m", "uvicorn", "api_server.main:app", "--port", $ApiPort `
    -WorkingDirectory (Join-Path $RepoRoot "apps\api-server") `
    -RedirectStandardOutput $apiLog `
    -RedirectStandardError $apiErr

try {
    # -----------------------------------------------------------------------
    # 5) Wait for /healthz
    # -----------------------------------------------------------------------
    Write-Host "==> Waiting for /healthz (max 30s)" -ForegroundColor Cyan
    $healthDeadline = (Get-Date).AddSeconds(30)
    $apiUp = $false
    while ((Get-Date) -lt $healthDeadline) {
        if ($apiProc.HasExited) {
            Get-Content $apiErr -Tail 30 -ErrorAction SilentlyContinue | Out-Host
            throw "api-server exited prematurely (exit $($apiProc.ExitCode)). See log above."
        }
        try {
            Invoke-RestMethod -Uri "http://localhost:$ApiPort/healthz" -TimeoutSec 2 -ErrorAction Stop | Out-Null
            $apiUp = $true
            break
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $apiUp) {
        Get-Content $apiLog, $apiErr -Tail 30 -ErrorAction SilentlyContinue | Out-Host
        throw "api-server /healthz did not respond within 30s"
    }

    # -----------------------------------------------------------------------
    # 6) Register + promote admin user
    # -----------------------------------------------------------------------
    Write-Host "==> Ensuring admin user '$AdminEmail' is registered + promoted" -ForegroundColor Cyan
    $registerBody = @{
        email     = $AdminEmail
        password  = $AdminPassword
        full_name = "E2E Admin"
    } | ConvertTo-Json
    try {
        Invoke-RestMethod -Method Post `
            -Uri "http://localhost:$ApiPort/auth/register" `
            -ContentType "application/json" -Body $registerBody -ErrorAction Stop | Out-Null
        Write-Host "    Registered new user." -ForegroundColor DarkGray
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch { }
        if ($code -eq 409) {
            Write-Host "    User already exists (409) -- continuing." -ForegroundColor DarkGray
        } else {
            throw "Register failed: $($_.Exception.Message)"
        }
    }

    # Promote (idempotent).
    $promoteSql = "UPDATE users SET is_system_admin = true WHERE email = '$AdminEmail'"
    & docker compose @ComposeArgs exec -T postgres psql -U postgres -d agentic_platform -c $promoteSql | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "UPDATE is_system_admin failed" }

    # -----------------------------------------------------------------------
    # 7) Run Playwright (which auto-starts npm run dev via webServer:)
    # -----------------------------------------------------------------------
    Write-Host "==> Running Playwright" -ForegroundColor Cyan
    Push-Location (Join-Path $RepoRoot "apps\admin-panel")
    try {
        $env:E2E_ADMIN_EMAIL = $AdminEmail
        $env:E2E_ADMIN_PASSWORD = $AdminPassword
        & npm run e2e
        $playwrightExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }

    if ($playwrightExit -ne 0) {
        Write-Host ""
        Write-Host "==> Playwright FAILED (exit $playwrightExit)." -ForegroundColor Red
        Write-Host "    Last 30 lines of api-server stdout:" -ForegroundColor DarkGray
        Get-Content $apiLog -Tail 30 -ErrorAction SilentlyContinue | Out-Host
        exit $playwrightExit
    }

    Write-Host ""
    Write-Host "OK. Playwright passed." -ForegroundColor Green
}
finally {
    # -----------------------------------------------------------------------
    # 8) Cleanup
    # -----------------------------------------------------------------------
    if ($null -ne $apiProc -and -not $apiProc.HasExited) {
        Write-Host "==> Stopping api-server (pid $($apiProc.Id))" -ForegroundColor Cyan
        Stop-Process -Id $apiProc.Id -Force -ErrorAction SilentlyContinue
    }
    # The docker stack stays UP — useful for the next run. Stop it
    # manually with:
    #   docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml down
}
