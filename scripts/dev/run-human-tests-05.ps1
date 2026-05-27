#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# scripts/dev/run-human-tests-05.ps1
#
# Launcher one-shot para ejecutar los tres tests humanos del Plan 05
# (MCP y Tools Avanzadas). A diferencia del launcher de Planes 02/04.5,
# los demos del Plan 05 son **standalone**: NO requieren docker compose,
# postgres, redis ni api-server arrancado.
#
# Lo que SI necesitan:
#   - demo_human_05_01: solo el venv (toy MCP server local)
#   - demo_human_05_02: docker daemon corriendo
#   - demo_human_05_03: internet (degrada a step 1 sin red)
#
# Uso:
#   .\scripts\dev\run-human-tests-05.ps1               # corre los 3
#   .\scripts\dev\run-human-tests-05.ps1 -Only 01      # solo el 1er demo
#   .\scripts\dev\run-human-tests-05.ps1 -SkipDocker   # salta el 02 sin avisar
# -----------------------------------------------------------------------------

[CmdletBinding()]
param(
    [ValidateSet("all", "01", "02", "03")]
    [string]$Only = "all",
    [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw ".venv missing. Run .\scripts\dev\bootstrap.ps1 first."
}

function Write-Step($Msg) { Write-Host ""; Write-Host "==> $Msg" -ForegroundColor Cyan }
function Write-Ok($Msg)   { Write-Host "    [OK] $Msg" -ForegroundColor Green }
function Write-Fail($Msg) { Write-Host "    [FAIL] $Msg" -ForegroundColor Red }
function Write-Skip($Msg) { Write-Host "    [SKIP] $Msg" -ForegroundColor Yellow }

# -----------------------------------------------------------------------------
# 1) Pre-flight: docker daemon (solo para demo 02)
# -----------------------------------------------------------------------------
$DockerAvailable = $false
if (-not $SkipDocker) {
    try {
        $null = docker info 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $DockerAvailable = $true }
    } catch { $DockerAvailable = $false }
    if (-not $DockerAvailable) {
        Write-Host "==> Docker daemon no responde — demo_human_05_02 se saltara." -ForegroundColor Yellow
        Write-Host "    Para correrlo: arranca Docker Desktop y reintenta." -ForegroundColor DarkGray
    }
} else {
    Write-Host "==> -SkipDocker activo: demo_human_05_02 se saltara sin chequear daemon." -ForegroundColor DarkGray
}

# -----------------------------------------------------------------------------
# 2) Lista de demos a correr
# -----------------------------------------------------------------------------
$Plan05 = @(
    @{ Id = "01"; Script = "demo_human_05_01.py"; NeedsDocker = $false },
    @{ Id = "02"; Script = "demo_human_05_02.py"; NeedsDocker = $true  },
    @{ Id = "03"; Script = "demo_human_05_03.py"; NeedsDocker = $false }
)

$Demos = @()
foreach ($d in $Plan05) {
    if ($Only -eq "all" -or $Only -eq $d.Id) { $Demos += , $d }
}

# -----------------------------------------------------------------------------
# 3) Ejecuta
# -----------------------------------------------------------------------------
$Results = @()
foreach ($d in $Demos) {
    if ($d.NeedsDocker -and -not $DockerAvailable) {
        Write-Step "Saltando $($d.Script) (Docker no disponible)"
        Write-Skip "$($d.Script) requiere Docker — no disponible"
        $Results += [PSCustomObject]@{ Demo = $d.Script; Status = "SKIP" }
        continue
    }

    Write-Step "Ejecutando $($d.Script)"
    $script = Join-Path $RepoRoot "scripts\$($d.Script)"
    & $VenvPython $script
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        Write-Ok "$($d.Script) termino OK"
        $Results += [PSCustomObject]@{ Demo = $d.Script; Status = "PASS" }
    } else {
        Write-Fail "$($d.Script) termino con exit $code"
        $Results += [PSCustomObject]@{ Demo = $d.Script; Status = "FAIL ($code)" }
    }
}

# -----------------------------------------------------------------------------
# 4) Resumen
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Resumen Plan 05 — tests humanos"                                 -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
$Results | Format-Table -AutoSize | Out-Host

$failed = $Results | Where-Object { $_.Status -like "FAIL*" }
if ($failed.Count -gt 0) {
    Write-Host "$($failed.Count) demo(s) fallaron. Revisa el output arriba." -ForegroundColor Red
    exit 1
}
$skipped = $Results | Where-Object { $_.Status -eq "SKIP" }
$passed  = $Results | Where-Object { $_.Status -eq "PASS" }
Write-Host "$($passed.Count) demo(s) pasaron, $($skipped.Count) skipped. [OK]" -ForegroundColor Green
Write-Host ""
Write-Host "Para mas detalle de cada test:" -ForegroundColor DarkGray
Write-Host "  docs\03-guides\human-tests\05-mcp-tools-avanzadas.md" -ForegroundColor DarkGray
exit 0
