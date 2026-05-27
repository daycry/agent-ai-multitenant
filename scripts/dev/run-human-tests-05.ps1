#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# scripts/dev/run-human-tests-05.ps1
#
# Launcher one-shot para ejecutar los tres tests humanos del Plan 05
# (MCP y Tools Avanzadas).
#
# Flujo:
#   1) Chequea que api-server :8001 esta arriba (necesario para que
#      los demos hagan HTTP contra /projects/<id>/...).
#   2) Lanza setup_demo_05.py que siembra proyecto + agentes + tools
#      (idempotente al nivel de "crea uno nuevo cada vez").
#   3) Corre los tres demos en orden. Cada uno te imprime las URLs
#      del admin-panel que tienes que abrir.
#
# Lo que cada demo necesita:
#   - 05_01: solo api-server + el venv (toy MCP server local)
#   - 05_02: + docker daemon corriendo
#   - 05_03: + (opcional) internet para el round-trip al httpbin
#
# Uso:
#   .\scripts\dev\run-human-tests-05.ps1
#   .\scripts\dev\run-human-tests-05.ps1 -Only 01
#   .\scripts\dev\run-human-tests-05.ps1 -SkipDocker  # salta el 05_02
#   .\scripts\dev\run-human-tests-05.ps1 -SkipSetup   # reusa el state previo
# -----------------------------------------------------------------------------

[CmdletBinding()]
param(
    [ValidateSet("all", "01", "02", "03")]
    [string]$Only = "all",
    [switch]$SkipDocker,
    [switch]$SkipSetup
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
# 1) api-server :8001 debe estar arriba
# -----------------------------------------------------------------------------
function Test-ApiServerUp {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8001/healthz" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        return $r.StatusCode -eq 200
    } catch { return $false }
}
if (-not (Test-ApiServerUp)) {
    Write-Fail "api-server :8001 no responde. Lanza primero el stack:"
    Write-Host "         .\scripts\dev\up.ps1" -ForegroundColor DarkGray
    exit 1
}
Write-Step "api-server :8001 OK"

# -----------------------------------------------------------------------------
# 2) Docker daemon (opcional, solo para demo 02)
# -----------------------------------------------------------------------------
$DockerAvailable = $false
if (-not $SkipDocker) {
    try {
        $null = docker info 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $DockerAvailable = $true }
    } catch { $DockerAvailable = $false }
    if (-not $DockerAvailable) {
        Write-Host "==> Docker daemon no responde - demo_human_05_02 se saltara." -ForegroundColor Yellow
    }
} else {
    Write-Host "==> -SkipDocker activo: demo_human_05_02 se saltara." -ForegroundColor DarkGray
}

# -----------------------------------------------------------------------------
# 3) Seed: proyecto + agentes + tools + mcp_servers
# -----------------------------------------------------------------------------
function Invoke-NativeScript {
    param([string]$Path)
    # Dos gotchas en una funcion:
    # (a) PowerShell 5.1 envuelve cada linea de stderr de un native
    #     exe en un ErrorRecord; con ErrorActionPreference=Stop eso
    #     mata el script aunque el exit code sea 0.
    # (b) Si una funcion no redirige el stdout del exe, ese stdout
    #     se mete en el pipeline de retorno de la funcion y el
    #     caller acaba leyendo un array enorme en vez del exit code.
    #     `| Out-Host` empuja la salida al terminal y deja el
    #     pipeline limpio para que `return $LASTEXITCODE` sea lo
    #     unico en el.
    $prevErr = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $VenvPython $Path | Out-Host
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevErr
    }
    return $code
}

if (-not $SkipSetup) {
    Write-Step "Sembrando escenario Plan 05 (proyecto + agentes + tools)"
    $setupCode = Invoke-NativeScript (Join-Path $RepoRoot "scripts\setup_demo_05.py")
    if ($setupCode -ne 0) {
        Write-Fail "setup_demo_05.py termino con exit $setupCode"
        exit 1
    }
    Write-Ok "setup OK"
} else {
    $stateFile = Join-Path $RepoRoot "scripts\.demo_state_05.json"
    if (-not (Test-Path $stateFile)) {
        Write-Fail "-SkipSetup pero no hay scripts\.demo_state_05.json. Quita -SkipSetup."
        exit 1
    }
    Write-Step "Reutilizando state previo (-SkipSetup)"
}

# -----------------------------------------------------------------------------
# 4) Lista de demos a correr
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
# 5) Ejecuta
# -----------------------------------------------------------------------------
$Results = @()
foreach ($d in $Demos) {
    if ($d.NeedsDocker -and -not $DockerAvailable) {
        Write-Step "Saltando $($d.Script) (Docker no disponible)"
        Write-Skip "$($d.Script) requiere Docker - no disponible"
        $Results += [PSCustomObject]@{ Demo = $d.Script; Status = "SKIP" }
        continue
    }

    Write-Step "Ejecutando $($d.Script)"
    $script = Join-Path $RepoRoot "scripts\$($d.Script)"
    $code = Invoke-NativeScript $script
    if ($code -eq 0) {
        Write-Ok "$($d.Script) termino OK"
        $Results += [PSCustomObject]@{ Demo = $d.Script; Status = "PASS" }
    } else {
        Write-Fail "$($d.Script) termino con exit $code"
        $Results += [PSCustomObject]@{ Demo = $d.Script; Status = "FAIL ($code)" }
    }
}

# -----------------------------------------------------------------------------
# 6) Resumen + URLs del proyecto
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Resumen Plan 05 - tests humanos"                                 -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
$Results | Format-Table -AutoSize | Out-Host

# Carga el state para enseñar las URLs del proyecto sembrado.
$state = $null
$stateFile = Join-Path $RepoRoot "scripts\.demo_state_05.json"
if (Test-Path $stateFile) {
    try {
        $state = Get-Content $stateFile -Raw | ConvertFrom-Json
    } catch { }
}

if ($state) {
    Write-Host "Que abrir en el admin-panel (Ctrl+click si la terminal lo soporta):" -ForegroundColor Cyan
    Write-Host "  - http://localhost:3000/admin/projects/$($state.project_id)/mcp-servers"
    Write-Host "    -> Card 'toy-mcp' + boton 'Probar conexion' que lista 3 tools"
    Write-Host "  - http://localhost:3000/admin/projects/$($state.project_id)/agent-tools-diagnostic"
    Write-Host "    -> Card MCP servers + cards de agentes con sus tools wired"
    Write-Host ""
    Write-Host "Detalle por test:" -ForegroundColor DarkGray
    Write-Host "  docs\03-guides\human-tests\05-mcp-tools-avanzadas.md" -ForegroundColor DarkGray
}

$failed = $Results | Where-Object { $_.Status -like "FAIL*" }
if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "$($failed.Count) demo(s) fallaron." -ForegroundColor Red
    exit 1
}
$skipped = $Results | Where-Object { $_.Status -eq "SKIP" }
$passed  = $Results | Where-Object { $_.Status -eq "PASS" }
Write-Host ""
Write-Host "$($passed.Count) demo(s) pasaron, $($skipped.Count) skipped. [OK]" -ForegroundColor Green
exit 0
