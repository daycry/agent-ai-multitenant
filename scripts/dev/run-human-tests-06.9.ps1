#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# scripts/dev/run-human-tests-06.9.ps1
#
# Launcher informativo de los 4 tests humanos del Plan 06.9
# (Agent-scoped KBs). Igual que el 06.8: NO automatiza nada porque
# todos los tests requieren navegacion UI y verificacion visual.
#
# Flujo:
#   1) Verifica api-server :8001 y admin-panel :3000.
#   2) Lee scripts/demos/.demo_state_06_9.json (creado por setup_demo_06_9.py)
#      e imprime los UUIDs + URLs concretas + checklist resumido por test.
#
# Si nunca corriste el setup:
#   .\.venv\Scripts\python.exe scripts\demos\setup_demo_06_9.py
# -----------------------------------------------------------------------------

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

function Write-Step($Msg) { Write-Host ""; Write-Host "==> $Msg" -ForegroundColor Cyan }
function Write-Ok($Msg)   { Write-Host "    [OK] $Msg" -ForegroundColor Green }
function Write-Fail($Msg) { Write-Host "    [FAIL] $Msg" -ForegroundColor Red }
function Write-Hint($Msg) { Write-Host "    $Msg" -ForegroundColor DarkGray }

function Test-Url {
    param([string]$Url)
    try {
        $r = Invoke-WebRequest -Uri $Url -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        return $r.StatusCode -in 200, 204, 304
    } catch { return $false }
}

Write-Step "Chequeo de servicios"
$apiOk = Test-Url "http://127.0.0.1:8001/healthz"
$webOk = Test-Url "http://127.0.0.1:3000"
if ($apiOk) { Write-Ok "api-server :8001" } else { Write-Fail "api-server :8001 no responde" }
if ($webOk) { Write-Ok "admin-panel :3000" } else { Write-Fail "admin-panel :3000 no responde" }
if (-not $apiOk) {
    Write-Hint "Lanza primero el stack: .\scripts\dev\up.ps1"
    exit 1
}

$StateFile = Join-Path $RepoRoot "scripts\demos\.demo_state_06_9.json"
if (-not (Test-Path $StateFile)) {
    Write-Step "No existe $StateFile"
    Write-Hint "Corre primero: .\.venv\Scripts\python.exe scripts\demos\setup_demo_06_9.py"
    exit 1
}

$state = Get-Content $StateFile -Raw | ConvertFrom-Json

Write-Step "Datos sembrados (tenant: $($state.tenant_slug))"
Write-Ok "KB:               $($state.kb_id) — API REST design principles"
Write-Ok "Agente template:  $($state.agent_template_id) — backend-dev-tenant"
Write-Ok "Proyecto Python:  $($state.project_python_id)"
Write-Ok "Proyecto PHP:     $($state.project_php_id)"
if ($state.builtin_pm_id) {
    Write-Ok "Built-in PM:      $($state.builtin_pm_id) — project_manager (test 04)"
} else {
    Write-Fail "Built-in PM NO seedeado — corre primero: python -m api_server.seeds"
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Credenciales (login en :3000/login)"                              -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  $($state.admin.email)   pwd: $($state.admin.password)   (tenant_admin)"

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Que hacer con cada test (detalle en el doc)"                      -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "[human_06_9_01] KB de rol grant a agente template" -ForegroundColor Yellow
Write-Host "  - /admin/agents/$($state.agent_template_id) -> seccion KBs"
Write-Host "  - Grant 'API REST design principles' al agente"
Write-Host "  - Abrir /admin/projects/$($state.project_python_id) y /admin/projects/$($state.project_php_id)"
Write-Host "  - Verificar que el agente ve la KB en ambos sin grants individuales"
Write-Host "  - Revoke desde el agente -> la KB desaparece en los dos proyectos"
Write-Host ""
Write-Host "[human_06_9_02] Plantilla pre-grantea KBs de stack" -ForegroundColor Yellow
Write-Host "  - /admin/projects/new -> plantilla 'Plantilla: API REST'"
Write-Host "  - Crear proyecto -> /admin/projects/{nuevoId} -> seccion KBs"
Write-Host "  - Deben aparecer python-fastapi + api-rest + postgresql automaticamente"
Write-Host "  - Repetir con 'Plantilla: Webapp Full-Stack' -> 4 KBs aparecen"
Write-Host "  - Crear proyecto 'desde cero' (sin template) -> seccion KBs vacia"
Write-Host ""
Write-Host "[human_06_9_03] Panel Asignaciones desde la KB" -ForegroundColor Yellow
Write-Host "  - /admin/knowledge-bases -> boton 'Asignaciones' en la fila de 'API REST design'"
Write-Host "  - Dialog lista proyectos + agentes con grant"
Write-Host "  - Click Revoke en una fila -> desaparece"
Write-Host "  - Recargar -> el proyecto/agente afectado ya no ve la KB"
Write-Host ""
Write-Host "[human_06_9_04] Built-in agent rechaza grant" -ForegroundColor Yellow
Write-Host "  - /admin/agents/$($state.builtin_pm_id) (built-in project_manager)"
Write-Host "  - Tab KBs visible pero SIN boton 'Grant KB' (UI guard)"
Write-Host "  - curl POST con token del admin -> 403 con mensaje 'global_builtin'"
Write-Host "  - Forkear el built-in (boton 'Hacer copia') -> la copia SI permite grant"
Write-Host ""

Write-Host "Detalle por test:" -ForegroundColor DarkGray
Write-Host "  docs\03-guides\human-tests\06.9-agent-scoped-kbs.md" -ForegroundColor DarkGray
Write-Host ""

exit 0
