#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# scripts/dev/run-human-tests.ps1
#
# Launcher one-shot para ejecutar los tests humanos de los planes 02 y 04.5
# contra el stack de desarrollo. Asume que .\scripts\dev\up.ps1 esta corriendo
# (docker compose + api-server :8001 + admin-panel :3000). Si NO esta, este
# script lo arranca antes.
#
# El api-server de up.ps1 ya usa los defaults que necesitan los demos (puerto
# 15432 para postgres, API_SERVER_JWT_SECRET=dev-only-jwt-secret-change-me).
# Los demos leen los mismos defaults via pydantic-settings cuando no hay env
# vars sobreescritas; por eso NO necesitas exportar nada en tu shell.
#
# Uso:
#   .\scripts\dev\run-human-tests.ps1               # corre los 7 (Plan 02 x5 + Plan 04.5 x2)
#   .\scripts\dev\run-human-tests.ps1 -Only 02      # solo Plan 02
#   .\scripts\dev\run-human-tests.ps1 -Only 04_5    # solo Plan 04.5
#   .\scripts\dev\run-human-tests.ps1 -SkipStack    # asume el stack ya levantado
#   .\scripts\dev\run-human-tests.ps1 -Pause        # con pausas entre fases para leer
# -----------------------------------------------------------------------------

[CmdletBinding()]
param(
    [ValidateSet("all", "02", "04_5")]
    [string]$Only = "all",
    [switch]$Pause,
    [switch]$SkipStack
)

$ErrorActionPreference = "Stop"

$RepoRoot   = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw ".venv missing. Run .\scripts\dev\bootstrap.ps1 first."
}

# Sin pausas por defecto (corre rapido). Con -Pause deja pausas para leer.
if (-not $Pause) { $env:DEMO_NO_PAUSE = "1" } else { Remove-Item Env:\DEMO_NO_PAUSE -ErrorAction SilentlyContinue }

function Write-Step($Msg) { Write-Host ""; Write-Host "==> $Msg" -ForegroundColor Cyan }
function Write-Ok($Msg)   { Write-Host "    [OK] $Msg" -ForegroundColor Green }
function Write-Fail($Msg) { Write-Host "    [FAIL] $Msg" -ForegroundColor Red }

# -----------------------------------------------------------------------------
# 1) Asegura el stack arriba (docker + api-server + admin-panel)
# -----------------------------------------------------------------------------
function Test-ApiServerUp {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8001/healthz" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        return $r.StatusCode -eq 200
    } catch { return $false }
}

if (-not $SkipStack) {
    if (Test-ApiServerUp) {
        Write-Step "api-server :8001 ya esta arriba: no relanzo el stack"
    } else {
        Write-Step "Levantando el stack dev (docker + api-server + admin-panel)"
        & (Join-Path $PSScriptRoot "up.ps1")
        if ($LASTEXITCODE -ne 0) { throw "up.ps1 fallo" }
    }
} else {
    if (-not (Test-ApiServerUp)) {
        throw "api-server :8001 no responde. Lanza .\scripts\dev\up.ps1 primero o quita -SkipStack."
    }
    Write-Step "Reutilizando el stack ya arrancado (flag -SkipStack)"
}

# -----------------------------------------------------------------------------
# 2) Setup compartido. Idempotentes; siempre se ejecutan.
# -----------------------------------------------------------------------------
Write-Step "Setup Plan 02: proyecto + agente Writer compartidos"
& $VenvPython (Join-Path $RepoRoot "scripts\setup_demo_project.py")
if ($LASTEXITCODE -ne 0) { throw "setup_demo_project.py fallo" }

if ($Only -ne "02") {
    Write-Step "Setup Plan 04.5: KB + Document + Team"
    & $VenvPython (Join-Path $RepoRoot "scripts\setup_demo_04_5.py")
    if ($LASTEXITCODE -ne 0) { throw "setup_demo_04_5.py fallo" }
}

# -----------------------------------------------------------------------------
# 3) Ejecuta los demos en orden
# -----------------------------------------------------------------------------
$Plan02 = @(
    "demo_human_02_01.py",
    "demo_human_02_02.py",
    "demo_human_02_03.py",
    "demo_human_02_04.py",
    "demo_human_02_05.py"
)
$Plan045 = @(
    "demo_human_04_5_01.py",
    "demo_human_04_5_02.py"
)

$Demos = @()
if ($Only -in @("all", "02"))    { $Demos += $Plan02 }
if ($Only -in @("all", "04_5"))  { $Demos += $Plan045 }

$Results = @()
foreach ($demo in $Demos) {
    Write-Step "Ejecutando $demo"
    $script = Join-Path $RepoRoot "scripts\$demo"
    & $VenvPython $script
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        Write-Ok "$demo termino OK"
        $Results += [PSCustomObject]@{ Demo = $demo; Status = "PASS" }
    } else {
        Write-Fail "$demo termino con exit $code"
        $Results += [PSCustomObject]@{ Demo = $demo; Status = "FAIL ($code)" }
    }
}

# -----------------------------------------------------------------------------
# 4) Resumen
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Resumen ejecucion tests humanos"                                -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
$Results | Format-Table -AutoSize | Out-Host

$failed = $Results | Where-Object { $_.Status -ne "PASS" }
if ($failed.Count -gt 0) {
    Write-Host "$($failed.Count) demo(s) fallaron. Revisa el output arriba." -ForegroundColor Red
    Write-Host ""
    Write-Host "Para mirar lo que dejo cada test:" -ForegroundColor Yellow
    Write-Host "  - /admin/board                              tareas + Kanban"
    Write-Host "  - /admin/approvals                          aprobaciones pendientes"
    Write-Host "  - /admin/executions/<id>                    Timeline de una ejecucion"
    Write-Host "  - /admin/memories                           memorias destiladas"
    Write-Host "  - /admin/projects/<id>/knowledge-bases      KBs del proyecto"
    exit 1
} else {
    Write-Host "Los $($Results.Count) demo(s) pasaron. [OK]" -ForegroundColor Green
    Write-Host ""
    Write-Host "Donde verlo en el admin-panel (http://localhost:3000):" -ForegroundColor Cyan
    Write-Host "  - /admin/board                              tareas + Kanban con las del demo"
    Write-Host "  - /admin/approvals                          la solicitud que dejo demo_human_02_04"
    Write-Host "  - /admin/memories                           memorias del Memorizer + memory_store"
    Write-Host "  - /admin/projects/<id>/knowledge-bases      KB origen + KB destino (Plan 04.5)"
    exit 0
}
