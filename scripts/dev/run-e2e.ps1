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
    [int]$ApiPort = 8001,
    # Open a real Chromium window. Default is headless (CI mode).
    [switch]$Headed,
    # Slow each action by N ms when visible. Only honored with -Headed.
    [int]$SlowMo = 0,
    # Path of a single spec file (relative to apps/admin-panel) to run
    # instead of the full suite. Useful for focused walkthroughs.
    [string]$Spec = "",
    # Substring (or regex) of the test title to run. Useful with -Headed
    # so you only get ONE browser window open instead of the suite
    # opening Chromium per-test in sequence.
    [string]$Grep = "",
    # Open Playwright's interactive UI mode (overrides -Headed/-Spec).
    [switch]$Ui
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
# 2b) Port preflight + stray-uvicorn cleanup.
# Get-NetTCPConnection sometimes reports stale entries whose owning PID is
# already dead (Windows kernel keeps the TCP control block in a half-closed
# state). The only authoritative test is: can we bind 127.0.0.1:$ApiPort?
# That is exactly what uvicorn will try.
# ---------------------------------------------------------------------------
function Test-PortBindable {
    param([int]$Port)
    try {
        $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $l.Start()
        $l.Stop()
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-PortBindable -Port $ApiPort)) {
    $portOwner = Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1

    if ($null -eq $portOwner) {
        throw "127.0.0.1:$ApiPort is not bindable but no listener is reported. Try a different port (-ApiPort)."
    }

    $ownerPid = $portOwner.OwningProcess
    $ownerProc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
    $ownerCim = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid" -ErrorAction SilentlyContinue
    $ownerCmd = if ($ownerCim) { $ownerCim.CommandLine } else { "" }
    $ownerExe = if ($ownerCim) { $ownerCim.ExecutablePath } else { "" }

    if ($null -eq $ownerProc -and $null -eq $ownerCim) {
        # GHOST LISTENER: Get-NetTCPConnection still reports a listener but
        # the owning process is gone. The kernel kept a half-closed TCP
        # control block after the process died abnormally. It typically
        # clears in 30s-2min; in stubborn cases only `netsh int ip reset`
        # + reboot recovers it. Bind to 0.0.0.0 still works on this port
        # but uvicorn defaults to 127.0.0.1, so we cannot proceed cleanly.
        Write-Host ""
        Write-Host "ERROR: port $ApiPort is held by a GHOST listener (pid $ownerPid no longer exists)." -ForegroundColor Red
        Write-Host "       This is a Windows kernel quirk -- a half-closed TCP control block survived its process."
        Write-Host "       Options:"
        Write-Host "         - Wait 30s-2min for the kernel to release it, then rerun."
        Write-Host "         - Rerun with a different port:  .\scripts\dev\run-e2e.ps1 -ApiPort 8002"
        Write-Host "         - As a last resort (admin + reboot):  netsh int ip reset"
        throw "port $ApiPort held by ghost listener (pid $ownerPid is dead)"
    }

    # Multiple signals can identify a stray uvicorn we left behind:
    #   1. CommandLine contains 'uvicorn' (parent process).
    #   2. CommandLine is a multiprocessing worker spawned BY uvicorn (on
    #      Windows uvicorn workers re-exec via multiprocessing.spawn, so
    #      the worker's CommandLine no longer mentions 'uvicorn').
    #   3. ExecutablePath is exactly our repo's .venv python -- only this
    #      script puts that python on this port.
    $ourVenvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $isOurs = ($ownerCmd -match 'uvicorn') -or
              ($ownerCmd -match 'spawn_main') -or
              ($ownerCmd -match 'multiprocessing') -or
              ($ownerExe -and ($ownerExe.ToLower() -eq $ourVenvPy.ToLower()))

    if (-not $isOurs) {
        $ownerName = if ($ownerProc) { "$($ownerProc.ProcessName) (pid $ownerPid)" } else { "pid $ownerPid" }
        Write-Host ""
        Write-Host "ERROR: port $ApiPort is held by $ownerName, which does not look like our uvicorn." -ForegroundColor Red
        Write-Host "       Executable:   $ownerExe"
        Write-Host "       Command line: $ownerCmd"
        Write-Host "       Refusing to kill an unknown process."
        Write-Host "       Either stop it manually, or rerun with -ApiPort <free port>."
        throw "port $ApiPort held by foreign process"
    }

    Write-Host "==> Port $ApiPort held by stray uvicorn/python (pid $ownerPid). Killing tree." -ForegroundColor Yellow
    Write-Host "    CommandLine: $ownerCmd" -ForegroundColor DarkGray
    & taskkill /F /T /PID $ownerPid 2>$null | Out-Null

    # Re-verify with a real bind test (Get-NetTCPConnection may lie even
    # after a successful kill; only an actual bind tells the truth).
    $freed = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-PortBindable -Port $ApiPort) { $freed = $true; break }
    }
    if (-not $freed) {
        throw "port $ApiPort still not bindable 10s after killing pid $ownerPid (likely a ghost listener -- rerun with -ApiPort)"
    }
}

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
# Each Playwright spec performs a fresh login. With the default 5
# attempts / 15 min limit we'd trip 429 once we have a handful of
# screen tests. Loosen it for E2E only.
$env:API_SERVER_LOGIN_RATE_LIMIT_COUNT = "1000"
$env:API_SERVER_LOGIN_RATE_LIMIT_WINDOW_SECONDS = "60"

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
            # Give uvicorn a beat to flush its last log line, then surface
            # both stdout AND stderr (uvicorn writes bind errors to stderr).
            Start-Sleep -Milliseconds 300
            Write-Host "    api-server stderr:" -ForegroundColor Red
            Get-Content $apiErr -Tail 30 -ErrorAction SilentlyContinue | Out-Host
            Write-Host "    api-server stdout:" -ForegroundColor Red
            Get-Content $apiLog -Tail 30 -ErrorAction SilentlyContinue | Out-Host
            $code = $apiProc.ExitCode
            if ($null -eq $code) { $code = "?" }
            throw "api-server exited prematurely (exit $code). See logs above."
        }
        try {
            # 127.0.0.1 explicit -- 'localhost' on Windows resolves to ::1 first,
            # uvicorn binds only on 127.0.0.1, and Invoke-RestMethod will hang
            # on the IPv6 attempt until -TimeoutSec instead of falling through.
            Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/healthz" -TimeoutSec 2 -ErrorAction Stop | Out-Null
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
    #
    # Login-first to handle the case where the email already exists in the
    # DB with a DIFFERENT password. A naive 409-is-ok branch would let the
    # script "succeed" and then Playwright would fail with 401.
    #
    # We also clear Redis rate-limit keys for the login endpoint before
    # probing: repeated dev runs (this script, manual tests, Playwright's
    # wrong-password test) accumulate failures and trip the 429 brownout,
    # which would then mask actual password mismatches.
    # -----------------------------------------------------------------------
    Write-Host "==> Ensuring admin user '$AdminEmail' is registered + promoted" -ForegroundColor Cyan

    # Wipe per-email + per-IP rate limit counters. Safe: this is the dev
    # stack, and the limits only exist to slow down brute force.
    & docker compose @ComposeArgs exec -T redis redis-cli `
        --no-raw DEL "rl:login:email:$AdminEmail" "rl:login:ip:127.0.0.1" 2>$null | Out-Null

    $loginBody = @{
        email    = $AdminEmail
        password = $AdminPassword
    } | ConvertTo-Json
    $registerBody = @{
        email     = $AdminEmail
        password  = $AdminPassword
        full_name = "E2E Admin"
    } | ConvertTo-Json

    # Returns one of: 'ok' (200), 'bad-password' (401), 'rate-limited' (429),
    # 'no-user' (404 or similar), or 'other:<code>'.
    function Get-AdminLoginStatus {
        try {
            Invoke-RestMethod -Method Post `
                -Uri "http://127.0.0.1:$ApiPort/auth/login" `
                -ContentType "application/json" -Body $loginBody -ErrorAction Stop | Out-Null
            return 'ok'
        } catch {
            $code = $null
            try { $code = $_.Exception.Response.StatusCode.value__ } catch { }
            switch ($code) {
                200 { return 'ok' }
                401 { return 'bad-password' }
                429 { return 'rate-limited' }
                404 { return 'no-user' }
                default { return "other:$code" }
            }
        }
    }

    $loginStatus = Get-AdminLoginStatus
    if ($loginStatus -eq 'ok') {
        Write-Host "    User already exists with the expected password." -ForegroundColor DarkGray
    } elseif ($loginStatus -eq 'rate-limited') {
        # We just cleared the keys; if we still see this, something else
        # is filling them. Better to bail than loop forever.
        throw "/auth/login is rate-limited even after clearing Redis keys. Wait a minute and rerun."
    } else {
        # Try to register. Status code 409 means the row exists but with
        # a different password than we expected.
        try {
            Invoke-RestMethod -Method Post `
                -Uri "http://127.0.0.1:$ApiPort/auth/register" `
                -ContentType "application/json" -Body $registerBody -ErrorAction Stop | Out-Null
            Write-Host "    Registered new user." -ForegroundColor DarkGray
        } catch {
            $code = $null
            try { $code = $_.Exception.Response.StatusCode.value__ } catch { }
            if ($code -eq 409) {
                throw @"
user '$AdminEmail' exists in the DB with a DIFFERENT password than '$AdminPassword'.
Either:
  - rerun with -AdminPassword <the password it actually has>, or
  - delete it and re-run:
      docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml exec postgres ``
        psql -U postgres -d agentic_platform -c "DELETE FROM users WHERE email = '$AdminEmail'"
"@
            } else {
                throw "Register failed: $($_.Exception.Message)"
            }
        }
        # Clear rate limits again (the failed login above counted toward
        # the bucket) and re-verify.
        & docker compose @ComposeArgs exec -T redis redis-cli `
            --no-raw DEL "rl:login:email:$AdminEmail" "rl:login:ip:127.0.0.1" 2>$null | Out-Null
        $postRegStatus = Get-AdminLoginStatus
        if ($postRegStatus -ne 'ok') {
            throw "registered '$AdminEmail' but /auth/login returns '$postRegStatus' -- aborting."
        }
    }

    # Promote (idempotent).
    $promoteSql = "UPDATE users SET is_system_admin = true WHERE email = '$AdminEmail'"
    & docker compose @ComposeArgs exec -T postgres psql -U postgres -d agentic_platform -c $promoteSql | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "UPDATE is_system_admin failed" }

    # -----------------------------------------------------------------------
    # 6b) Apply built-in seeds (agents/skills/tools/teams/templates/policies).
    # Plan-01 Playwright tests assume the 11 built-in agents exist.
    # The seed module is idempotent (ON CONFLICT DO UPDATE).
    # -----------------------------------------------------------------------
    Write-Host "==> Applying built-in seeds" -ForegroundColor Cyan
    $env:API_SERVER_ADMIN_DATABASE_URL = "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
    Push-Location (Join-Path $RepoRoot "apps\api-server")
    try {
        & $venvPython -m api_server.seeds
        if ($LASTEXITCODE -ne 0) { throw "seed run failed" }
    } finally {
        Pop-Location
    }

    # -----------------------------------------------------------------------
    # 7) Run Playwright (which auto-starts npm run dev via webServer:)
    # -----------------------------------------------------------------------
    # Build the Playwright args.
    # Modes:
    #   -Ui              -> `playwright test --ui` (interactive)
    #   -Headed          -> `--headed`; pair with -SlowMo for delay
    #   -SlowMo N        -> sets E2E_SLOW_MO so the config applies
    #                       launchOptions.slowMo (Playwright has no
    #                       --slow-mo CLI flag).
    #   -Spec <path>     -> only that file
    #   default          -> full suite, headless
    $pwArgs = @()
    if ($Ui)         { $pwArgs += "--ui" }
    elseif ($Headed) { $pwArgs += "--headed" }
    if ($Grep) {
        $pwArgs += "--grep"
        $pwArgs += $Grep
    }
    if ($Spec) {
        # Playwright treats the spec arg as a regex; Windows backslashes
        # poison that (`\p` becomes a regex escape, the matcher finds
        # nothing). Normalize to forward slashes.
        $pwArgs += $Spec.Replace('\', '/')
    }
    if ($SlowMo -gt 0 -and $Headed) {
        $env:E2E_SLOW_MO = "$SlowMo"
    } else {
        Remove-Item Env:\E2E_SLOW_MO -ErrorAction SilentlyContinue
    }

    Write-Host ("==> Running Playwright" + $(if ($pwArgs) { " (" + ($pwArgs -join ' ') + ")" } else { "" })) -ForegroundColor Cyan
    Push-Location (Join-Path $RepoRoot "apps\admin-panel")
    try {
        $env:E2E_ADMIN_EMAIL = $AdminEmail
        $env:E2E_ADMIN_PASSWORD = $AdminPassword
        # Next dev server bakes lib/api.ts's API_URL from NEXT_PUBLIC_API_URL.
        # Without this the browser tests would call the default
        # http://localhost:8001 even when we run uvicorn on a different port.
        $env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:$ApiPort"
        # Call playwright directly via npx -- `npm run e2e -- ...` eats
        # `--headed` / `--slow-mo=` on Windows because npm interprets
        # them before forwarding (only the trailing positional arg makes
        # it through).
        if ($pwArgs.Count -gt 0) {
            & npx playwright test @pwArgs
        } else {
            & npx playwright test
        }
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
        # /T kills the full tree so uvicorn workers don't outlive their parent.
        & taskkill /F /T /PID $apiProc.Id 2>$null | Out-Null
    }
    # The docker stack stays UP — useful for the next run. Stop it
    # manually with:
    #   docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml down
}
